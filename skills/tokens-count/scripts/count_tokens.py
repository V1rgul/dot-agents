#!/usr/bin/env python3
"""Count locally recorded token usage and activity for Codex and Cursor threads."""
from __future__ import annotations
import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

STANDARD_FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens')
READ_FIELDS = ('cache_read_input_tokens', 'cached_input_tokens')
WRITE_FIELDS = ('cache_write_input_tokens', 'cache_creation_input_tokens', 'cache_write_tokens', 'cache_creation_tokens')
PROVIDERS = ('codex', 'cursor')
CURSOR_HUMAN_BUBBLE_TYPE = 1
THREAD_ID_RE = re.compile('([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\\.jsonl$', re.IGNORECASE)
TOON_MISSING = '__TOKENS_COUNT_MISSING_8F0B3C57__'
DESCRIPTION = "Count Codex tokens and Codex/Cursor activity from local data; today's activity is the default."
DETAIL_FIELDS = (
	'provider',
	'thread_id',
	'thread_name',
	'models',
	'turn_count',
	'request_count',
	'tool_call_count',
	'start',
	'end',
	'input',
	'cache_read',
	'cache_write',
	'uncached',
	'output',
	'reasoning',
	'tokens',
)
DEFAULT_DETAIL_FIELDS = ('provider', 'thread_id', 'thread_name', 'models', 'tokens')
DETAIL_FIELD_DESCRIPTIONS = {
	'provider': 'Data source for the row. Providers: Codex, Cursor.',
	'thread_id': 'Provider-local thread identifier. Providers: Codex, Cursor.',
	'thread_name': 'Thread title when recorded locally. Providers: Codex, Cursor.',
	'models': 'Distinct effective models recorded in the selected range. Providers: Codex.',
	'turn_count': 'Number of user turns. Providers: Codex, Cursor.',
	'request_count': 'Number of model requests with recorded token usage. Providers: Codex.',
	'tool_call_count': 'Number of unique tool calls. Providers: Codex, Cursor.',
	'start': 'Timestamp of the first request with recorded token usage in the selected range. Providers: Codex.',
	'end': 'Timestamp of the last request with recorded token usage in the selected range. Providers: Codex.',
	'input': 'Total input tokens, including tokens read from or written to cache. Providers: Codex.',
	'cache_read': 'Input tokens read from cache. Providers: Codex.',
	'cache_write': 'Input tokens written to cache. Providers: Codex.',
	'uncached': 'Input tokens neither read from nor written to cache; computed as max(0, input - cache_read - cache_write). Providers: Codex.',
	'output': 'Total output tokens, including reasoning. Providers: Codex.',
	'reasoning': 'Output tokens used for reasoning; included in output. Providers: Codex.',
	'tokens': 'Total input and output tokens; reported directly rather than computed from input and output. Providers: Codex.',
}

class CountError(Exception):
	pass

class UsageError(Exception):
	pass

class AxiArgumentParser(argparse.ArgumentParser):
	def error(self, message: str) -> None:
		raise UsageError(message)

def parse_datetime(value: str) -> datetime:
	text = value.strip()
	if text.endswith(('Z', 'z')):
		text = text[:-1] + '+00:00'
	try:
		parsed = datetime.fromisoformat(text)
	except ValueError as exc:
		raise argparse.ArgumentTypeError(f'invalid ISO 8601 datetime: {value!r}') from exc
	if parsed.tzinfo is None:
		parsed = parsed.astimezone()
	return parsed.astimezone(timezone.utc)

def utc_text(value: datetime | None) -> str | None:
	if value is None:
		return None
	return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

def numeric_counts(value: Any) -> dict[str, int]:
	if not isinstance(value, dict):
		return {}
	return {key: count for key, count in value.items() if isinstance(key, str) and isinstance(count, int) and (not isinstance(count, bool)) and (count >= 0)}

def parse_selector(raw: str) -> tuple[str, str]:
	provider, separator, thread_id = raw.partition(':')
	provider = provider.lower()
	thread_id = thread_id.strip()
	if not separator or provider not in PROVIDERS or (not thread_id):
		raise CountError(f'invalid thread selector {raw!r}; expected codex:<thread_id> or cursor:<thread_id>')
	return (provider, thread_id)

def resolve_threads(provider: str, requested: list[str], available: set[str]) -> list[str]:
	resolved: list[str] = []
	provider_name = provider.capitalize()
	for raw in requested:
		value = raw.casefold()
		matches = [thread_id for thread_id in available if thread_id.casefold().startswith(value)]
		exact = next((thread_id for thread_id in matches if thread_id.casefold() == value), None)
		if not matches:
			raise CountError(f'{provider_name} thread not found: {raw}')
		if exact is not None:
			match = exact
		elif len(matches) == 1:
			match = matches[0]
		else:
			preview = ', '.join(sorted(matches, key=str.casefold)[:5])
			raise CountError(f'ambiguous {provider_name} thread prefix {raw!r}: {preview}')
		if match not in resolved:
			resolved.append(match)
	return resolved

def in_window(timestamp: datetime, after: datetime | None, before: datetime | None) -> bool:
	return (after is None or timestamp >= after) and (before is None or timestamp < before)

def event_in_window(event: dict[str, Any], after: datetime | None, before: datetime | None, diagnostics: dict[str, int], missing_timestamp_key: str) -> tuple[bool, datetime | None]:
	timestamp: datetime | None = None
	timestamp_raw = event.get('timestamp')
	if isinstance(timestamp_raw, str):
		try:
			timestamp = parse_datetime(timestamp_raw)
		except argparse.ArgumentTypeError:
			pass
	if timestamp is None:
		if after is not None or before is not None:
			diagnostics[missing_timestamp_key] += 1
			return (False, None)
		return (True, None)
	return (in_window(timestamp, after, before), timestamp)

def empty_codex_counts() -> dict[str, int]:
	return {field: 0 for field in STANDARD_FIELDS}

def empty_unknown_counts() -> dict[str, None]:
	return {field: None for field in STANDARD_FIELDS}

def thread_id_from_file(path: Path) -> str | None:
	try:
		with path.open('r', encoding='utf-8') as stream:
			for line_number, line in enumerate(stream, start=1):
				if line_number > 50:
					break
				try:
					event = json.loads(line)
				except json.JSONDecodeError:
					continue
				if event.get('type') != 'session_meta':
					continue
				payload = event.get('payload')
				if isinstance(payload, dict) and isinstance(payload.get('id'), str):
					return payload['id'].lower()
	except OSError:
		return None
	match = THREAD_ID_RE.search(path.name)
	return match.group(1).lower() if match else None

def discover_codex_threads(codex_home: Path, diagnostics: dict[str, int]) -> dict[str, list[Path]]:
	grouped: dict[str, list[Path]] = defaultdict(list)
	seen_paths: set[Path] = set()
	for directory in (codex_home / 'sessions', codex_home / 'archived_sessions'):
		if not directory.is_dir():
			continue
		for path in directory.rglob('*.jsonl'):
			resolved = path.resolve()
			if resolved in seen_paths:
				continue
			seen_paths.add(resolved)
			thread_id = thread_id_from_file(path)
			if thread_id is None:
				diagnostics['codex_files_without_thread_id'] += 1
				continue
			grouped[thread_id].append(path)
	return grouped

def load_codex_names(codex_home: Path, diagnostics: dict[str, int]) -> dict[str, str]:
	names: dict[str, str] = {}
	index_path = codex_home / 'session_index.jsonl'
	if not index_path.is_file():
		return names
	try:
		with index_path.open('r', encoding='utf-8') as stream:
			for line in stream:
				try:
					entry = json.loads(line)
				except json.JSONDecodeError:
					diagnostics['codex_malformed_index_lines'] += 1
					continue
				thread_id = entry.get('id')
				name = entry.get('thread_name')
				if isinstance(thread_id, str) and isinstance(name, str) and name:
					names[thread_id.lower()] = name
	except OSError as exc:
		raise CountError(f'cannot read {index_path}: {exc}') from exc
	return names

def default_cursor_state_db() -> Path:
	if os.name == 'nt':
		app_data = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
		return app_data / 'Cursor' / 'User' / 'globalStorage' / 'state.vscdb'
	if sys.platform == 'darwin':
		return Path.home() / 'Library' / 'Application Support' / 'Cursor' / 'User' / 'globalStorage' / 'state.vscdb'
	config_home = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
	return config_home / 'Cursor' / 'User' / 'globalStorage' / 'state.vscdb'

def connect_cursor_db(path: Path) -> sqlite3.Connection:
	if not path.is_file():
		raise CountError(f'Cursor state database not found: {path}')
	try:
		connection = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True)
		connection.execute('PRAGMA query_only = ON')
		return connection
	except (OSError, sqlite3.Error) as exc:
		raise CountError(f'cannot open Cursor state database {path}: {exc}') from exc

def cursor_name_from_value(value: Any, diagnostics: dict[str, int]) -> str | None:
	if not isinstance(value, (str, bytes)):
		return None
	try:
		metadata = json.loads(value)
	except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
		diagnostics['cursor_malformed_metadata_records'] += 1
		return None
	if not isinstance(metadata, dict):
		return None
	for field in ('name', 'subtitle'):
		name = metadata.get(field)
		if isinstance(name, str) and name:
			return name
	return None

def discover_cursor_threads(connection: sqlite3.Connection, diagnostics: dict[str, int]) -> dict[str, str | None]:
	threads: dict[str, str | None] = {}
	try:
		for thread_id, value in connection.execute('SELECT composerId, value FROM composerHeaders'):
			if not isinstance(thread_id, str) or not thread_id:
				diagnostics['cursor_headers_without_thread_id'] += 1
				continue
			threads[thread_id] = cursor_name_from_value(value, diagnostics)
		prefix = 'composerData:'
		for key, value in connection.execute('SELECT key, value FROM cursorDiskKV WHERE key >= ? AND key < ?', (prefix, 'composerData;')):
			if not isinstance(key, str) or not key.startswith(prefix):
				continue
			thread_id = key[len(prefix):]
			if not thread_id:
				continue
			name = cursor_name_from_value(value, diagnostics)
			if thread_id not in threads or threads[thread_id] is None:
				threads[thread_id] = name
	except sqlite3.Error as exc:
		raise CountError(f'cannot read Cursor thread metadata: {exc}') from exc
	return threads

def parse_cursor_usage(connection: sqlite3.Connection, thread_id: str, after: datetime | None, before: datetime | None, diagnostics: dict[str, int]) -> tuple[int, int, int]:
	turn_count = 0
	activity_count = 0
	seen_tool_calls: set[str] = set()
	prefix = f'bubbleId:{thread_id}:'
	try:
		rows = connection.execute('SELECT key, value FROM cursorDiskKV WHERE key >= ? AND key < ?', (prefix, f'{prefix}\U0010ffff'))
		for key, value in rows:
			try:
				bubble = json.loads(value)
			except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
				diagnostics['cursor_malformed_bubble_records'] += 1
				continue
			if not isinstance(bubble, dict):
				diagnostics['cursor_malformed_bubble_records'] += 1
				continue
			included, _ = event_in_window({'timestamp': bubble.get('createdAt')}, after, before, diagnostics, 'cursor_bubbles_without_usable_timestamp')
			if not included:
				continue
			activity_count += 1
			bubble_type = bubble.get('type')
			if isinstance(bubble_type, int) and not isinstance(bubble_type, bool) and bubble_type == CURSOR_HUMAN_BUBBLE_TYPE:
				turn_count += 1
			tool_data = bubble.get('toolFormerData')
			if not isinstance(tool_data, dict):
				continue
			call_id = tool_data.get('toolCallId')
			identity = call_id if isinstance(call_id, str) and call_id else key
			seen_tool_calls.add(identity)
	except sqlite3.Error as exc:
		raise CountError(f'cannot read Cursor thread {thread_id}: {exc}') from exc
	return (turn_count, len(seen_tool_calls), activity_count)

def usage_delta(current: dict[str, int], previous: dict[str, int]) -> dict[str, int]:
	delta: dict[str, int] = {}
	for key, value in current.items():
		prior = previous.get(key, 0)
		delta[key] = value - prior if value >= prior else value
	return delta

def parse_codex_usage(paths: list[Path], after: datetime | None, before: datetime | None, diagnostics: dict[str, int]) -> tuple[dict[str, int], int, int, int, datetime | None, datetime | None, list[str]]:
	totals = empty_codex_counts()
	request_count = 0
	turn_count = 0
	tool_call_count = 0
	first_event: datetime | None = None
	last_event: datetime | None = None
	seen_requests: set[str] = set()
	seen_turns: set[str] = set()
	seen_tool_calls: set[str] = set()
	models: set[str] = set()
	for path in paths:
		previous_total: dict[str, int] = {}
		active_model: str | None = None
		try:
			with path.open('r', encoding='utf-8') as stream:
				for line in stream:
					try:
						event = json.loads(line)
					except json.JSONDecodeError:
						diagnostics['codex_malformed_json_lines'] += 1
						continue
					payload = event.get('payload')
					if event.get('type') == 'turn_context' and isinstance(payload, dict):
						model = payload.get('model')
						if isinstance(model, str) and model:
							active_model = model
						included, _ = event_in_window(event, after, before, diagnostics, 'codex_turns_without_usable_timestamp')
						if not included:
							continue
						if active_model is not None:
							models.add(active_model)
						turn_id = payload.get('turn_id')
						identity = f'turn:{turn_id}' if isinstance(turn_id, str) and turn_id else json.dumps([event.get('timestamp'), payload], sort_keys=True, separators=(',', ':'))
						if identity not in seen_turns:
							seen_turns.add(identity)
							turn_count += 1
						continue
					if event.get('type') == 'response_item' and isinstance(payload, dict):
						item_type = payload.get('type')
						if not isinstance(item_type, str) or not item_type.endswith('_call'):
							continue
						included, _ = event_in_window(event, after, before, diagnostics, 'codex_tool_calls_without_usable_timestamp')
						if not included:
							continue
						call_id = payload.get('call_id')
						if not isinstance(call_id, str) or not call_id:
							call_id = payload.get('id')
						identity = f'{item_type}:{call_id}' if isinstance(call_id, str) and call_id else json.dumps([event.get('timestamp'), payload], sort_keys=True, separators=(',', ':'))
						if identity not in seen_tool_calls:
							seen_tool_calls.add(identity)
							tool_call_count += 1
						continue
					if event.get('type') != 'event_msg' or not isinstance(payload, dict) or payload.get('type') != 'token_count':
						continue
					info = payload.get('info')
					if not isinstance(info, dict):
						continue
					current_total = numeric_counts(info.get('total_token_usage'))
					incremental = numeric_counts(info.get('last_token_usage'))
					if not incremental and current_total:
						incremental = usage_delta(current_total, previous_total)
						diagnostics['codex_cumulative_fallback_events'] += 1
					if current_total:
						previous_total = current_total
					if not incremental:
						diagnostics['codex_token_events_without_usage'] += 1
						continue
					included, timestamp = event_in_window(event, after, before, diagnostics, 'codex_events_without_usable_timestamp')
					if not included:
						continue
					timestamp_raw = event.get('timestamp')
					identity = json.dumps([timestamp_raw, current_total, incremental], sort_keys=True, separators=(',', ':'))
					if identity in seen_requests:
						diagnostics['codex_deduplicated_events'] += 1
						continue
					seen_requests.add(identity)
					if active_model is not None:
						models.add(active_model)
					for key, value in incremental.items():
						totals[key] = totals.get(key, 0) + value
					request_count += 1
					if timestamp is not None:
						first_event = timestamp if first_event is None else min(first_event, timestamp)
						last_event = timestamp if last_event is None else max(last_event, timestamp)
		except OSError as exc:
			raise CountError(f'cannot read {path}: {exc}') from exc
	return (totals, request_count, turn_count, tool_call_count, first_event, last_event, sorted(models, key=str.casefold))

def input_counts(raw: dict[str, int | None]) -> dict[str, int | None]:
	read_source = next((field for field in READ_FIELDS if isinstance(raw.get(field), int)), None)
	write_source = next((field for field in WRITE_FIELDS if isinstance(raw.get(field), int)), None)
	cache_read = raw.get(read_source) if read_source else None
	cache_write = raw.get(write_source) if write_source else None
	input_tokens = raw.get('input_tokens')
	uncached = None
	if isinstance(input_tokens, int) and isinstance(cache_read, int):
		uncached = max(0, input_tokens - cache_read - (cache_write if isinstance(cache_write, int) else 0))
	return {'total': input_tokens, 'cache_read': cache_read, 'cache_write': cache_write, 'uncached': uncached}

def make_tokens(raw: dict[str, int | None]) -> dict[str, Any]:
	input_tokens = {field: value for field, value in input_counts(raw).items() if value is not None}
	output_tokens = {field: value for field, value in {'total': raw.get('output_tokens'), 'reasoning': raw.get('reasoning_output_tokens')}.items() if value is not None}
	tokens: dict[str, Any] = {}
	if input_tokens:
		tokens['input'] = input_tokens
	if output_tokens:
		tokens['output'] = output_tokens
	if raw.get('total_tokens') is not None:
		tokens['total'] = raw['total_tokens']
	return tokens

def make_result(provider: str, thread_id: str, thread_name: str | None, raw: dict[str, int | None], first_event: datetime | None, last_event: datetime | None, turn_count: int | None, request_count: int | None, tool_call_count: int | None, models: list[str] | None = None) -> dict[str, Any]:
	result: dict[str, Any] = {'provider': provider, 'thread_id': thread_id, 'thread_name': thread_name, 'turn_count': turn_count, 'request_count': request_count, 'tool_call_count': tool_call_count, 'start': utc_text(first_event), 'end': utc_text(last_event)}
	if models:
		result['models'] = models
	tokens = make_tokens(raw)
	if tokens:
		result['tokens'] = tokens
	return {field: value for field, value in result.items() if value is not None}

def sum_scalar(results: list[dict[str, Any]], field: str) -> int:
	values: list[int] = []
	for result in results:
		value = result.get(field)
		if isinstance(value, int) and not isinstance(value, bool):
			values.append(value)
	return sum(values)

def sum_token_field(results: list[dict[str, Any]], *path: str) -> int:
	values: list[int] = []
	for result in results:
		value: Any = result.get('tokens', {})
		for field in path:
			if not isinstance(value, dict):
				value = None
				break
			value = value.get(field)
		if isinstance(value, int) and not isinstance(value, bool):
			values.append(value)
	return sum(values)

def total_results(results: list[dict[str, Any]]) -> dict[str, Any]:
	tokens = {'input': {field: sum_token_field(results, 'input', field) for field in ('total', 'cache_read', 'cache_write', 'uncached')}, 'output': {field: sum_token_field(results, 'output', field) for field in ('total', 'reasoning')}, 'total': sum_token_field(results, 'total')}
	start_values = [parse_datetime(result['start']) for result in results if 'start' in result]
	end_values = [parse_datetime(result['end']) for result in results if 'end' in result]
	total: dict[str, Any] = {'thread_count': len(results), 'turn_count': sum_scalar(results, 'turn_count'), 'request_count': sum_scalar(results, 'request_count'), 'tool_call_count': sum_scalar(results, 'tool_call_count')}
	if start_values:
		total['start'] = utc_text(min(start_values))
	if end_values:
		total['end'] = utc_text(max(end_values))
	total['tokens'] = tokens
	return total

def nested_value(value: dict[str, Any], *path: str) -> Any:
	current: Any = value
	for field in path:
		if not isinstance(current, dict) or field not in current:
			return TOON_MISSING
		current = current[field]
	return current

def flatten_thread_for_toon(thread: dict[str, Any], fields: tuple[str, ...] = DETAIL_FIELDS) -> dict[str, Any]:
	models = nested_value(thread, 'models')
	if isinstance(models, list):
		models = ','.join(models)
	values = {
		'provider': nested_value(thread, 'provider'),
		'thread_id': nested_value(thread, 'thread_id'),
		'thread_name': nested_value(thread, 'thread_name'),
		'models': models,
		'turn_count': nested_value(thread, 'turn_count'),
		'request_count': nested_value(thread, 'request_count'),
		'tool_call_count': nested_value(thread, 'tool_call_count'),
		'start': nested_value(thread, 'start'),
		'end': nested_value(thread, 'end'),
		'input': nested_value(thread, 'tokens', 'input', 'total'),
		'cache_read': nested_value(thread, 'tokens', 'input', 'cache_read'),
		'cache_write': nested_value(thread, 'tokens', 'input', 'cache_write'),
		'uncached': nested_value(thread, 'tokens', 'input', 'uncached'),
		'output': nested_value(thread, 'tokens', 'output', 'total'),
		'reasoning': nested_value(thread, 'tokens', 'output', 'reasoning'),
		'tokens': nested_value(thread, 'tokens', 'total'),
	}
	return {field: values[field] for field in fields}

def project_thread_for_json(thread: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
	projected: dict[str, Any] = {}
	for field in fields:
		if field in ('provider', 'thread_id', 'thread_name', 'models', 'turn_count', 'request_count', 'tool_call_count', 'start', 'end'):
			if field in thread:
				projected[field] = thread[field]
			continue
		token_path = {
			'input': ('input', 'total'),
			'cache_read': ('input', 'cache_read'),
			'cache_write': ('input', 'cache_write'),
			'uncached': ('input', 'uncached'),
			'output': ('output', 'total'),
			'reasoning': ('output', 'reasoning'),
			'tokens': ('total',),
		}[field]
		value = nested_value(thread, 'tokens', *token_path)
		if value == TOON_MISSING:
			continue
		tokens = projected.setdefault('tokens', {})
		if len(token_path) == 1:
			tokens[token_path[0]] = value
		else:
			tokens.setdefault(token_path[0], {})[token_path[1]] = value
	return projected

def encode_output_as_toon(output: dict[str, Any], fields: tuple[str, ...] = DETAIL_FIELDS) -> str:
	try:
		from toon_format import encode as encode_toon
	except ImportError as exc:
		raise CountError('TOON output support is not installed') from exc
	toon_output = output
	if isinstance(output.get('threads'), list):
		toon_output = {**output, 'threads': [flatten_thread_for_toon(thread, fields) for thread in output['threads']]}
	return encode_toon(toon_output, {'delimiter': '|'}).replace(TOON_MISSING, '')

def parse_fields(raw: str | None) -> tuple[str, ...]:
	if raw is None:
		return DEFAULT_DETAIL_FIELDS
	if raw.strip().casefold() == 'all':
		return DETAIL_FIELDS
	fields = tuple(dict.fromkeys(field.strip() for field in raw.split(',') if field.strip()))
	if not fields:
		raise UsageError('--fields requires a comma-separated field list or all')
	unknown = [field for field in fields if field not in DETAIL_FIELDS]
	if unknown:
		raise UsageError(f"unknown detail field(s): {', '.join(unknown)}")
	return fields

def build_parser() -> argparse.ArgumentParser:
	parser = AxiArgumentParser(
		description=DESCRIPTION,
		formatter_class=argparse.RawDescriptionHelpFormatter,
		epilog='\n'.join((
			'Examples:',
			'  count_tokens.py',
			'  count_tokens.py codex:all cursor:all --after 2026-07-28T00:00:00Z',
			'  count_tokens.py codex:<thread_id> --detail --fields all --json',
		)),
	)
	parser.add_argument('threads', nargs='*', metavar='PROVIDER:THREAD_ID', help='full ID, unique prefix, or provider:all; defaults to codex:all cursor:all for today')
	parser.add_argument('--after', type=parse_datetime, metavar='DATETIME', help='include events at or after this ISO 8601 datetime')
	parser.add_argument('--before', type=parse_datetime, metavar='DATETIME', help='include events before this ISO 8601 datetime')
	parser.add_argument('--detail', action='store_true', help='include a minimal per-thread array and the full total; see --detail --help')
	parser.add_argument('--fields', metavar='LIST', help='comma-separated thread fields, or all; requires --detail')
	parser.add_argument('--json', action='store_true', help='encode output as JSON instead of TOON')
	return parser

def format_detail_help() -> str:
	width = max(len(field) for field in DETAIL_FIELDS)
	lines = [
		'Detail fields:',
		*(f'  {field.ljust(width)}  {DETAIL_FIELD_DESCRIPTIONS[field]}' for field in DETAIL_FIELDS),
		'',
		f'Default fields: {", ".join(DEFAULT_DETAIL_FIELDS)}',
		'Unavailable values are blank in TOON and omitted from JSON.',
		'',
	]
	return '\n'.join(lines)

def collapsed_script_path() -> str:
	script = Path(__file__).resolve()
	home = Path.home().resolve()
	try:
		return str(Path('~') / script.relative_to(home))
	except ValueError:
		return str(script)

def command_prefix() -> str:
	path = collapsed_script_path()
	return f'python "{path}"' if ' ' in path else f'python {path}'

def today_window() -> tuple[datetime, datetime]:
	now = datetime.now().astimezone()
	start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
	return (start.astimezone(timezone.utc), now.astimezone(timezone.utc))

def requested_threads(selectors: list[str]) -> dict[str, list[str]]:
	requested_by_provider: dict[str, list[str]] = defaultdict(list)
	for raw in selectors:
		provider, thread_id = parse_selector(raw)
		requested_by_provider[provider].append(thread_id)
	for provider, provider_threads in requested_by_provider.items():
		if any(thread_id.casefold() == 'all' for thread_id in provider_threads) and len(provider_threads) != 1:
			raise UsageError(f'cannot combine {provider}:all with specific {provider} thread selectors')
	return requested_by_provider

def collect_results(
	requested_by_provider: dict[str, list[str]],
	after: datetime | None,
	before: datetime | None,
	*,
	best_effort: bool = False,
	failures: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
	diagnostics: dict[str, int] = defaultdict(int)
	results: list[dict[str, Any]] = []
	for provider, provider_threads in requested_by_provider.items():
		select_all = provider_threads[0].casefold() == 'all'
		try:
			if provider == 'codex':
				codex_home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser()
				codex_threads = discover_codex_threads(codex_home, diagnostics)
				if not codex_threads:
					raise CountError(f'no Codex session JSONL files found under {codex_home}')
				codex_names = load_codex_names(codex_home, diagnostics)
				selected = sorted(codex_threads) if select_all else resolve_threads(provider, provider_threads, set(codex_threads))
				provider_results: list[dict[str, Any]] = []
				for thread_id in selected:
					raw, request_count, turn_count, tool_call_count, first_event, last_event, models = parse_codex_usage(codex_threads[thread_id], after, before, diagnostics)
					if select_all and request_count == 0:
						continue
					provider_results.append(make_result(provider, thread_id, codex_names.get(thread_id), raw, first_event, last_event, turn_count=turn_count, request_count=request_count, tool_call_count=tool_call_count, models=models))
				if select_all:
					provider_results.sort(key=lambda result: result.get('end') or '', reverse=True)
				results.extend(provider_results)
				continue
			cursor_db_path = default_cursor_state_db()
			connection = connect_cursor_db(cursor_db_path)
			try:
				cursor_threads = discover_cursor_threads(connection, diagnostics)
				if not cursor_threads:
					raise CountError(f'no Cursor threads found in {cursor_db_path}')
				selected = sorted(cursor_threads, key=str.casefold) if select_all else resolve_threads(provider, provider_threads, set(cursor_threads))
				provider_results = []
				for thread_id in selected:
					turn_count, tool_call_count, activity_count = parse_cursor_usage(connection, thread_id, after, before, diagnostics)
					if select_all and activity_count == 0:
						continue
					provider_results.append(make_result(provider, thread_id, cursor_threads.get(thread_id), empty_unknown_counts(), None, None, turn_count=turn_count, request_count=None, tool_call_count=tool_call_count))
				if select_all:
					provider_results.sort(key=lambda result: ((result.get('thread_name') or '').casefold(), result['thread_id'].casefold()))
				results.extend(provider_results)
			finally:
				connection.close()
		except CountError as exc:
			if not best_effort:
				raise
			if failures is not None:
				failures.append({'provider': provider, 'error': str(exc)})
	return results

def detailed_help() -> list[str]:
	prefix = command_prefix()
	return [
		f'Run `{prefix} <provider>:<thread_id> <provider>:<thread_id>` for a total across selected threads',
		'Add `--fields all` for activity counts, start/end timestamps, and input, cache, output, reasoning, and total token counts',
	]

def time_range_help() -> str:
	return 'Add `--after <datetime>` and/or `--before <datetime>` to select another time range'

def home_output(results: list[dict[str, Any]], detail: bool, fields: tuple[str, ...], json_mode: bool, failures: list[dict[str, str]], suggest_time_range: bool) -> dict[str, Any]:
	prefix = command_prefix()
	output: dict[str, Any] = {
		'bin': collapsed_script_path(),
		'description': DESCRIPTION,
	}
	if detail:
		output['threads'] = [project_thread_for_json(thread, fields) for thread in results] if json_mode else results
	output['total'] = total_results(results)
	if failures:
		output['unavailable'] = failures
	if not json_mode:
		if detail:
			output['help'] = detailed_help()
		else:
			output['help'] = [
				f'Run `{prefix} --detail` for per-thread titles and totals',
				f'Run `{prefix} codex:<thread_id>` for one Codex thread total',
			]
		if suggest_time_range:
			output['help'].append(time_range_help())
	return output

def emit_output(output: dict[str, Any], *, json_mode: bool, fields: tuple[str, ...]) -> None:
	if json_mode:
		sys.stdout.write(json.dumps(output, indent=2, ensure_ascii=False))
		return
	sys.stdout.write(encode_output_as_toon(output, fields))

def emit_error(message: str, help_text: str, *, json_mode: bool) -> None:
	output = {'error': message, 'help': help_text}
	if json_mode:
		sys.stdout.write(json.dumps(output, indent=2, ensure_ascii=False))
		return
	try:
		sys.stdout.write(encode_output_as_toon(output))
	except CountError:
		sys.stdout.write(json.dumps(output, ensure_ascii=False))

def valid_flags_help() -> str:
	return f'Run `{command_prefix()} --help`; valid flags: --after, --before, --detail, --fields, --json, --help'

def main(argv: list[str] | None = None) -> int:
	arguments = list(sys.argv[1:] if argv is None else argv)
	json_mode = '--json' in arguments
	parser = build_parser()
	if '--help' in arguments or '-h' in arguments:
		sys.stdout.write(format_detail_help() if '--detail' in arguments else parser.format_help())
		return 0
	for removed, replacement in (('--thread-all', 'use codex:all or cursor:all'), ('--thread', 'pass codex:<thread_id> or cursor:<thread_id> positionally')):
		if any(argument == removed or argument.startswith(f'{removed}=') for argument in arguments):
			emit_error(f'{removed} was removed', replacement, json_mode=json_mode)
			return 2
	try:
		args = parser.parse_args(arguments)
		if args.after is not None and args.before is not None and args.after >= args.before:
			raise UsageError('--after must be earlier than --before')
		if args.fields is not None and not args.detail:
			raise UsageError('--fields requires --detail')
		fields = parse_fields(args.fields)
		time_range_supplied = args.after is not None or args.before is not None
		home = not args.threads
		if not time_range_supplied:
			args.after, args.before = today_window()
		if home:
			selectors = ['codex:all', 'cursor:all']
		else:
			selectors = args.threads
		requested = requested_threads(selectors)
	except (UsageError, CountError) as exc:
		emit_error(str(exc), valid_flags_help(), json_mode=json_mode)
		return 2
	try:
		failures: list[dict[str, str]] = []
		results = collect_results(requested, args.after, args.before, best_effort=home, failures=failures)
		if home:
			output = home_output(results, args.detail, fields, args.json, failures, not time_range_supplied)
		elif args.detail:
			output = {
				'threads': [project_thread_for_json(thread, fields) for thread in results] if args.json else results,
				'total': total_results(results),
			}
			if not args.json:
				output['help'] = detailed_help()
				if not time_range_supplied:
					output['help'].append(time_range_help())
		else:
			output = total_results(results)
			if not args.json and not time_range_supplied:
				output['help'] = [time_range_help()]
		emit_output(output, json_mode=args.json, fields=fields)
		return 0
	except CountError as exc:
		help_text = f'Run `{command_prefix()} --json` if TOON output is unavailable' if 'TOON output' in str(exc) else f'Confirm the selected local data exists, then rerun `{command_prefix()} {" ".join(selectors)}`'
		emit_error(str(exc), help_text, json_mode=args.json)
		return 1

if __name__ == '__main__':
	sys.exit(main())
