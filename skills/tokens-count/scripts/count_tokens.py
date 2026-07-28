#!/usr/bin/env python3
"""Count locally recorded token usage and activity for Codex threads."""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
STANDARD_FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens')
DERIVED_FIELDS = ('cache_read_input_tokens', 'cache_write_input_tokens', 'uncached_input_tokens')
READ_FIELDS = ('cache_read_input_tokens', 'cached_input_tokens')
WRITE_FIELDS = ('cache_write_input_tokens', 'cache_creation_input_tokens', 'cache_write_tokens', 'cache_creation_tokens')
THREAD_ID_RE = re.compile('([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\\.jsonl$', re.IGNORECASE)

class CountError(Exception):
	pass

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

def parse_selector(raw: str) -> str:
	provider, separator, thread_id = raw.partition(':')
	provider = provider.lower()
	thread_id = thread_id.strip().lower()
	if not separator or provider != 'codex' or (not thread_id):
		raise CountError(f'invalid thread selector {raw!r}; expected codex:<thread_id>')
	return thread_id

def resolve_threads(requested: list[str], available: set[str]) -> list[str]:
	resolved: list[str] = []
	for raw in requested:
		value = raw.lower()
		matches = [thread_id for thread_id in available if thread_id.startswith(value)]
		if not matches:
			raise CountError(f'Codex thread not found: {raw}')
		if value in available:
			match = value
		elif len(matches) == 1:
			match = matches[0]
		else:
			preview = ', '.join(sorted(matches)[:5])
			raise CountError(f'ambiguous Codex thread prefix {raw!r}: {preview}')
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

def usage_delta(current: dict[str, int], previous: dict[str, int]) -> dict[str, int]:
	delta: dict[str, int] = {}
	for key, value in current.items():
		prior = previous.get(key, 0)
		delta[key] = value - prior if value >= prior else value
	return delta

def parse_codex_usage(paths: list[Path], after: datetime | None, before: datetime | None, diagnostics: dict[str, int]) -> tuple[dict[str, int], int, int, int, datetime | None, datetime | None]:
	totals = empty_codex_counts()
	request_count = 0
	turn_count = 0
	tool_call_count = 0
	first_event: datetime | None = None
	last_event: datetime | None = None
	seen_requests: set[str] = set()
	seen_turns: set[str] = set()
	seen_tool_calls: set[str] = set()
	for path in paths:
		previous_total: dict[str, int] = {}
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
						included, _ = event_in_window(event, after, before, diagnostics, 'codex_turns_without_usable_timestamp')
						if not included:
							continue
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
					for key, value in incremental.items():
						totals[key] = totals.get(key, 0) + value
					request_count += 1
					if timestamp is not None:
						first_event = timestamp if first_event is None else min(first_event, timestamp)
						last_event = timestamp if last_event is None else max(last_event, timestamp)
		except OSError as exc:
			raise CountError(f'cannot read {path}: {exc}') from exc
	return (totals, request_count, turn_count, tool_call_count, first_event, last_event)

def derived_counts(raw: dict[str, int | None]) -> tuple[dict[str, int | None], dict[str, str | None]]:
	read_source = next((field for field in READ_FIELDS if isinstance(raw.get(field), int)), None)
	write_source = next((field for field in WRITE_FIELDS if isinstance(raw.get(field), int)), None)
	cache_read = raw.get(read_source) if read_source else None
	cache_write = raw.get(write_source) if write_source else None
	input_tokens = raw.get('input_tokens')
	uncached = None
	if isinstance(input_tokens, int) and isinstance(cache_read, int):
		uncached = max(0, input_tokens - cache_read)
	return ({'cache_read_input_tokens': cache_read, 'cache_write_input_tokens': cache_write, 'uncached_input_tokens': uncached}, {'cache_read_input_tokens': read_source, 'cache_write_input_tokens': write_source, 'uncached_input_tokens': f'input_tokens - {read_source}' if read_source is not None else None})

def make_result(thread_id: str, thread_name: str | None, source_count: int, raw: dict[str, int | None], event_count: int, first_event: datetime | None, last_event: datetime | None, turn_count: int, request_count: int, tool_call_count: int) -> dict[str, Any]:
	derived, sources = derived_counts(raw)
	return {'provider': 'codex', 'thread': f'codex:{thread_id}', 'thread_id': thread_id, 'thread_name': thread_name, 'thread_count': 1, 'turn_count': turn_count, 'request_count': request_count, 'tool_call_count': tool_call_count, 'source_file_count': source_count, 'token_event_count': event_count, 'first_token_event': utc_text(first_event), 'last_token_event': utc_text(last_event), 'tokens': dict(sorted(raw.items())), 'derived_tokens': derived, 'derived_sources': sources}

def sum_field(results: list[dict[str, Any]], section: str, field: str) -> int | None:
	values: list[int] = []
	for result in results:
		mapping = result[section]
		if field in mapping:
			value = mapping[field]
		else:
			value = 0
		if not isinstance(value, int) or isinstance(value, bool):
			return None
		values.append(value)
	return sum(values)

def sum_results(results: list[dict[str, Any]]) -> dict[str, Any]:
	token_fields = set(STANDARD_FIELDS)
	for result in results:
		token_fields.update(result['tokens'])
	tokens = {field: sum_field(results, 'tokens', field) for field in sorted(token_fields)}
	derived = {field: sum_field(results, 'derived_tokens', field) for field in DERIVED_FIELDS}
	first_values = [parse_datetime(result['first_token_event']) for result in results if result['first_token_event']]
	last_values = [parse_datetime(result['last_token_event']) for result in results if result['last_token_event']]
	return {'thread_count': len(results), 'turn_count': sum((result['turn_count'] for result in results)), 'request_count': sum((result['request_count'] for result in results)), 'tool_call_count': sum((result['tool_call_count'] for result in results)), 'source_file_count': sum((result['source_file_count'] for result in results)), 'token_event_count': sum((result['token_event_count'] for result in results)), 'first_token_event': utc_text(min(first_values)) if first_values else None, 'last_token_event': utc_text(max(last_values)) if last_values else None, 'tokens': tokens, 'derived_tokens': derived, 'derived_sources': {field: f'sum of per-thread derived_tokens.{field}' if value is not None else None for field, value in derived.items()}}

def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description='Count locally recorded Codex tokens and activity per thread.')
	selectors = parser.add_mutually_exclusive_group(required=True)
	selectors.add_argument('--thread', action='append', metavar='codex:THREAD_ID', help='select a Codex thread by full ID or unique prefix; repeatable')
	selectors.add_argument('--thread-all', action='store_true', help='select every Codex thread with usable token records')
	parser.add_argument('--after', type=parse_datetime, metavar='DATETIME', help='include events at or after this ISO 8601 datetime')
	parser.add_argument('--before', type=parse_datetime, metavar='DATETIME', help='include events before this ISO 8601 datetime')
	return parser

def main() -> int:
	parser = build_parser()
	args = parser.parse_args()
	if args.after is not None and args.before is not None and (args.after >= args.before):
		parser.error('--after must be earlier than --before')
	diagnostics: dict[str, int] = defaultdict(int)
	requested_threads: list[str] = []
	if not args.thread_all:
		try:
			for raw in args.thread:
				requested_threads.append(parse_selector(raw))
		except CountError as exc:
			parser.error(str(exc))
	results: list[dict[str, Any]] = []
	try:
		codex_home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser()
		codex_threads = discover_codex_threads(codex_home, diagnostics)
		if not codex_threads:
			raise CountError(f'no Codex session JSONL files found under {codex_home}')
		codex_names = load_codex_names(codex_home, diagnostics)
		selected = sorted(codex_threads) if args.thread_all else resolve_threads(requested_threads, set(codex_threads))
		for thread_id in selected:
			raw, request_count, turn_count, tool_call_count, first_event, last_event = parse_codex_usage(codex_threads[thread_id], args.after, args.before, diagnostics)
			if args.thread_all and request_count == 0:
				continue
			results.append(make_result(thread_id, codex_names.get(thread_id), len(codex_threads[thread_id]), raw, request_count, first_event, last_event, turn_count=turn_count, request_count=request_count, tool_call_count=tool_call_count))
	except CountError as exc:
		parser.error(str(exc))
	if args.thread_all:
		results.sort(key=lambda result: result['last_token_event'] or '', reverse=True)
	output: dict[str, Any] = {'selection': {'mode': 'all' if args.thread_all else 'explicit', 'after_inclusive': utc_text(args.after), 'before_exclusive': utc_text(args.before), 'thread_count': len(results)}, 'threads': results}
	if len(results) > 1:
		output['sum'] = sum_results(results)
	nonzero_diagnostics = {key: value for key, value in sorted(diagnostics.items()) if value}
	if nonzero_diagnostics:
		output['diagnostics'] = nonzero_diagnostics
	print(json.dumps(output, indent=2, ensure_ascii=False))
	return 0
if __name__ == '__main__':
	sys.exit(main())
