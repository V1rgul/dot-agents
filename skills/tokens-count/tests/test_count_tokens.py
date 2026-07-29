import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).parents[1] / 'scripts' / 'count_tokens.py'
SPEC = importlib.util.spec_from_file_location('count_tokens', SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
COUNT_TOKENS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COUNT_TOKENS)

class CursorUsageTests(unittest.TestCase):
	def setUp(self):
		self.temporary_directory = tempfile.TemporaryDirectory()
		self.database_path = Path(self.temporary_directory.name) / 'state.vscdb'
		connection = sqlite3.connect(self.database_path)
		connection.execute('CREATE TABLE composerHeaders (composerId TEXT PRIMARY KEY, workspaceId TEXT, createdAt INTEGER, lastUpdatedAt INTEGER, isArchived INTEGER, isSubagent INTEGER, recency INTEGER, checkpointAt INTEGER, value TEXT)')
		connection.execute('CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)')
		connection.execute('INSERT INTO composerHeaders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', ('CaseSensitive-ID', 'workspace', 0, 0, 0, 0, 0, 0, json.dumps({'name': 'Cursor thread'})))
		connection.execute('INSERT INTO cursorDiskKV VALUES (?, ?)', ('composerData:CaseSensitive-ID', json.dumps({'name': 'Cursor thread'})))
		bubbles = [
			('human', {'createdAt': '2026-07-28T10:00:00Z', 'type': 1, 'tokenCount': {'inputTokens': 0, 'outputTokens': 0}, 'usageData': None}),
			('tool-a', {'createdAt': '2026-07-28T10:01:00Z', 'type': 2, 'toolFormerData': {'toolCallId': 'call-1'}, 'tokenCount': {'inputTokens': 0, 'outputTokens': 0}, 'usageData': None}),
			('tool-a-duplicate', {'createdAt': '2026-07-28T10:02:00Z', 'type': 2, 'toolFormerData': {'toolCallId': 'call-1'}, 'tokenCount': {'inputTokens': 0, 'outputTokens': 0}, 'usageData': None}),
			('tool-b', {'createdAt': '2026-07-28T11:00:00Z', 'type': 2, 'toolFormerData': {'toolCallId': 'call-2'}, 'tokenCount': {'inputTokens': 0, 'outputTokens': 0}, 'usageData': None}),
		]
		for bubble_id, bubble in bubbles:
			connection.execute('INSERT INTO cursorDiskKV VALUES (?, ?)', (f'bubbleId:CaseSensitive-ID:{bubble_id}', json.dumps(bubble)))
		connection.commit()
		connection.close()

	def tearDown(self):
		self.temporary_directory.cleanup()

	def test_cursor_counts_available_activity_and_filters_by_time(self):
		diagnostics = {}
		connection = COUNT_TOKENS.connect_cursor_db(self.database_path)
		try:
			threads = COUNT_TOKENS.discover_cursor_threads(connection, diagnostics)
			selected = COUNT_TOKENS.resolve_threads('cursor', ['casesensitive'], set(threads))
			after = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
			before = datetime(2026, 7, 28, 11, 0, tzinfo=timezone.utc)
			turn_count, tool_call_count, activity_count = COUNT_TOKENS.parse_cursor_usage(connection, selected[0], after, before, diagnostics)
		finally:
			connection.close()
		self.assertEqual(selected, ['CaseSensitive-ID'])
		self.assertEqual(threads['CaseSensitive-ID'], 'Cursor thread')
		self.assertEqual(turn_count, 1)
		self.assertEqual(tool_call_count, 1)
		self.assertEqual(activity_count, 3)

	def test_cursor_result_omits_unavailable_values(self):
		result = COUNT_TOKENS.make_result('cursor', 'CaseSensitive-ID', 'Cursor thread', COUNT_TOKENS.empty_unknown_counts(), None, None, turn_count=1, request_count=None, tool_call_count=2)
		self.assertEqual(result, {'provider': 'cursor', 'thread_id': 'CaseSensitive-ID', 'thread_name': 'Cursor thread', 'turn_count': 1, 'tool_call_count': 2})

	def test_total_ignores_unavailable_values(self):
		cursor_result = COUNT_TOKENS.make_result('cursor', 'cursor-id', None, COUNT_TOKENS.empty_unknown_counts(), None, None, turn_count=1, request_count=None, tool_call_count=2)
		codex_tokens = {field: 1 for field in COUNT_TOKENS.STANDARD_FIELDS}
		codex_tokens['cache_creation_input_tokens'] = 1
		event_time = datetime(2026, 7, 28, tzinfo=timezone.utc)
		codex_result = COUNT_TOKENS.make_result('codex', 'codex-id', None, codex_tokens, event_time, event_time, turn_count=1, request_count=1, tool_call_count=1)
		total = COUNT_TOKENS.total_results([cursor_result, codex_result])
		self.assertEqual(total['turn_count'], 2)
		self.assertEqual(total['tool_call_count'], 3)
		self.assertEqual(total['request_count'], 1)
		self.assertEqual(total['start'], COUNT_TOKENS.utc_text(event_time))
		self.assertEqual(total['end'], COUNT_TOKENS.utc_text(event_time))
		self.assertNotIn('token_event_count', total)
		self.assertEqual(total['tokens'], {'input': {'total': 1, 'cache_read': 1, 'cache_write': 1, 'uncached': 0}, 'output': {'total': 1, 'reasoning': 1}, 'total': 1})

	def test_total_sums_codex_tokens(self):
		first_tokens = {'input_tokens': 10, 'cached_input_tokens': 3, 'output_tokens': 4, 'reasoning_output_tokens': 1, 'total_tokens': 14}
		second_tokens = {'input_tokens': 20, 'cached_input_tokens': 5, 'output_tokens': 6, 'reasoning_output_tokens': 2, 'total_tokens': 26}
		event_time = datetime(2026, 7, 28, tzinfo=timezone.utc)
		first_result = COUNT_TOKENS.make_result('codex', 'first', None, first_tokens, event_time, event_time, turn_count=1, request_count=1, tool_call_count=1)
		second_result = COUNT_TOKENS.make_result('codex', 'second', None, second_tokens, event_time, event_time, turn_count=1, request_count=1, tool_call_count=1)
		total = COUNT_TOKENS.total_results([first_result, second_result])
		self.assertEqual(total['tokens'], {'input': {'total': 30, 'cache_read': 8, 'cache_write': 0, 'uncached': 22}, 'output': {'total': 10, 'reasoning': 3}, 'total': 40})
		self.assertNotIn('derived_tokens', total)

	def test_cursor_all_through_cli_interface(self):
		output = io.StringIO()
		with patch.object(COUNT_TOKENS, 'default_cursor_state_db', return_value=self.database_path), patch.object(sys, 'argv', ['count_tokens.py', 'cursor:all', '--json', '--detail']), redirect_stdout(output):
			exit_code = COUNT_TOKENS.main()
		result = json.loads(output.getvalue())
		self.assertEqual(exit_code, 0)
		self.assertNotIn('selection', result)
		self.assertEqual(result['threads'][0]['provider'], 'cursor')
		self.assertEqual(result['threads'][0]['thread_id'], 'CaseSensitive-ID')
		self.assertNotIn('thread', result['threads'][0])
		self.assertNotIn('thread_count', result['threads'][0])
		self.assertNotIn('request_count', result['threads'][0])
		self.assertNotIn('tokens', result['threads'][0])
		self.assertNotIn('null', json.dumps(result['threads'][0]))
		self.assertEqual(result['total']['thread_count'], 1)
		self.assertEqual(result['total']['request_count'], 0)
		self.assertNotIn('start', result['total'])
		self.assertNotIn('end', result['total'])
		self.assertEqual(result['total']['tokens'], {'input': {'total': 0, 'cache_read': 0, 'cache_write': 0, 'uncached': 0}, 'output': {'total': 0, 'reasoning': 0}, 'total': 0})
		self.assertNotIn('null', json.dumps(result['total']))
		self.assertNotIn('sum', result)

	def test_default_output_is_toon_total_only(self):
		output = io.StringIO()
		with patch.object(COUNT_TOKENS, 'default_cursor_state_db', return_value=self.database_path), patch.object(sys, 'argv', ['count_tokens.py', 'cursor:all']), redirect_stdout(output):
			exit_code = COUNT_TOKENS.main()
		self.assertEqual(exit_code, 0)
		self.assertEqual(output.getvalue(), '\n'.join([
			'thread_count: 1',
			'turn_count: 1',
			'request_count: 0',
			'tool_call_count: 2',
			'tokens:',
			'  input:',
			'    total: 0',
			'    cache_read: 0',
			'    cache_write: 0',
			'    uncached: 0',
			'  output:',
			'    total: 0',
			'    reasoning: 0',
			'  total: 0',
		]))

	def test_json_without_detail_returns_total_directly(self):
		output = io.StringIO()
		with patch.object(COUNT_TOKENS, 'default_cursor_state_db', return_value=self.database_path), patch.object(sys, 'argv', ['count_tokens.py', 'cursor:all', '--json']), redirect_stdout(output):
			exit_code = COUNT_TOKENS.main()
		result = json.loads(output.getvalue())
		self.assertEqual(exit_code, 0)
		self.assertEqual(result['thread_count'], 1)
		self.assertNotIn('threads', result)
		self.assertNotIn('total', result)

	def test_detail_toon_flattens_threads_and_uses_empty_cells(self):
		output = io.StringIO()
		with patch.object(COUNT_TOKENS, 'default_cursor_state_db', return_value=self.database_path), patch.object(sys, 'argv', ['count_tokens.py', 'cursor:all', '--detail']), redirect_stdout(output):
			exit_code = COUNT_TOKENS.main()
		self.assertEqual(exit_code, 0)
		self.assertTrue(output.getvalue().startswith('threads[1]{provider,thread_id,thread_name,turn_count,request_count,tool_call_count,start,end,input,cache_read,cache_write,uncached,output,reasoning,tokens}:\n  cursor,CaseSensitive-ID,Cursor thread,1,,2,,,,,,,,,\ntotal:\n'))
		self.assertFalse(output.getvalue().endswith('\n'))

	def test_detail_toon_tabularizes_mixed_thread_schemas(self):
		event_time = datetime(2026, 7, 28, tzinfo=timezone.utc)
		codex_tokens = {field: 1 for field in COUNT_TOKENS.STANDARD_FIELDS}
		codex_result = COUNT_TOKENS.make_result('codex', 'codex-id', None, codex_tokens, event_time, event_time, turn_count=1, request_count=1, tool_call_count=1)
		cursor_result = COUNT_TOKENS.make_result('cursor', 'cursor-id', 'Cursor', COUNT_TOKENS.empty_unknown_counts(), None, None, turn_count=2, request_count=None, tool_call_count=3)
		output = COUNT_TOKENS.encode_output_as_toon({'threads': [codex_result, cursor_result], 'total': COUNT_TOKENS.total_results([codex_result, cursor_result])})
		lines = output.splitlines()
		self.assertEqual(lines[0], 'threads[2]{provider,thread_id,thread_name,turn_count,request_count,tool_call_count,start,end,input,cache_read,cache_write,uncached,output,reasoning,tokens}:')
		self.assertEqual(lines[1], '  codex,codex-id,,1,1,1,"2026-07-28T00:00:00Z","2026-07-28T00:00:00Z",1,1,,0,1,1,1')
		self.assertEqual(lines[2], '  cursor,cursor-id,Cursor,2,,3,,,,,,,,,')
		self.assertNotIn(COUNT_TOKENS.TOON_MISSING, output)

class SelectorTests(unittest.TestCase):
	def test_all_is_provider_qualified(self):
		self.assertEqual(COUNT_TOKENS.parse_selector('codex:all'), ('codex', 'all'))
		self.assertEqual(COUNT_TOKENS.parse_selector('cursor:all'), ('cursor', 'all'))

if __name__ == '__main__':
	unittest.main()
