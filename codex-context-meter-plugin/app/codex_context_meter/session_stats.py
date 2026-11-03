from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CHARS_PER_TOKEN = 4
BLOCK_OVERHEAD = 4
ROLE_OVERHEAD = 4
INITIAL_TAIL_BYTES = 8 * 1024 * 1024
MAX_TAIL_BYTES = 64 * 1024 * 1024
MAX_SESSION_FILES = 128
CATALOG_CACHE_SECONDS = 1.0
IMAGE_ITEM_TYPES = {"input_image", "image", "image_url"}


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _parse_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000 if number > 10_000_000_000 else number
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text_tokens(value: str | None) -> int:
    if not value:
        return 0
    return (len(value) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def _json_tokens(value: Any) -> int:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(value)
    return _text_tokens(text)


def _estimate_content(blocks: Any) -> int:
    if isinstance(blocks, str):
        return _text_tokens(blocks) + BLOCK_OVERHEAD
    if not isinstance(blocks, list):
        return 0
    tokens = 0
    for block in blocks:
        if not isinstance(block, dict):
            tokens += _text_tokens(str(block)) + BLOCK_OVERHEAD
            continue
        kind = block.get("type")
        if kind in {"text", "input_text", "output_text", "reasoning_text"}:
            tokens += _text_tokens(block.get("text")) + BLOCK_OVERHEAD
        elif kind == "image":
            tokens += _json_tokens(block) + BLOCK_OVERHEAD
        else:
            tokens += _json_tokens(block) + BLOCK_OVERHEAD
    return tokens


def estimate_model_visible_item(payload: Any) -> int:
    """Estimate one Response item using the DeepSeek Harness density rule."""
    if not isinstance(payload, dict):
        return 0
    kind = payload.get("type")
    if kind == "message":
        return ROLE_OVERHEAD + _estimate_content(payload.get("content"))
    if kind == "reasoning":
        content = payload.get("content")
        if isinstance(content, list):
            text = "".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict)
            )
        else:
            text = str(content or "")
        return ROLE_OVERHEAD + _text_tokens(text)
    if kind in {"function_call", "custom_tool_call"}:
        return (
            BLOCK_OVERHEAD
            + _text_tokens(str(payload.get("name") or ""))
            + _text_tokens(str(payload.get("arguments") or payload.get("input") or ""))
        )
    if kind in {"function_call_output", "custom_tool_call_output"}:
        output = payload.get("output")
        if isinstance(output, str):
            return BLOCK_OVERHEAD + _text_tokens(output)
        return BLOCK_OVERHEAD + _json_tokens(output)
    if kind == "web_search_call":
        return BLOCK_OVERHEAD + _json_tokens(payload.get("action"))
    return 0


def _tail_lines(path: Path, max_bytes: int) -> list[str]:
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        start = max(0, size - max_bytes)
        stream.seek(start)
        data = stream.read(size - start)
    if start:
        newline = data.find(b"\n")
        if newline < 0:
            return []
        data = data[newline + 1 :]
    return data.decode("utf-8", errors="replace").splitlines()


def _load_json(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class SessionSnapshot:
    thread_id: str
    title: str
    model: str
    anchor_ordinal: int
    anchor_tokens: int
    surface_delta_tokens: int
    projected_tokens: int
    context_window: int
    remaining_tokens: int
    percent: float
    exact: bool
    stale: bool
    total_input_tokens: int
    total_cached_input_tokens: int
    total_cache_write_input_tokens: int
    total_output_tokens: int
    cache_hit_rate: float | None
    updated_at: float | None
    file_path: str
    request_payload_bytes: int = 0
    image_count: int = 0
    image_payload_bytes: int = 0
    max_success_payload_bytes: int = 0
    min_failure_payload_bytes: int = 0
    request_too_large: bool = False
    request_error: str = ""
    workspace_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _FileResult:
    snapshot: SessionSnapshot | None
    modified_ns: int
    size: int


@dataclass
class _PayloadState:
    offset: int = 0
    modified_ns: int = 0
    pending: bytes = b""
    request_payload_bytes: int = 0
    image_count: int = 0
    image_payload_bytes: int = 0
    max_success_payload_bytes: int = 0
    min_failure_payload_bytes: int = 0
    request_too_large: bool = False
    request_error: str = ""


class SessionStatsTracker:
    """Read one Codex session and project its next prompt from the last usage anchor."""

    def __init__(self, codex_home: str | Path | None = None):
        home = codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex"
        self.codex_home = Path(home)
        self.sessions_root = self.codex_home / "sessions"
        self.index_path = self.codex_home / "session_index.jsonl"
        self.global_state_path = self.codex_home / ".codex-global-state.json"
        self._cache: dict[str, _FileResult] = {}
        self._payload_cache: dict[str, _PayloadState] = {}
        self._workspace_cache: dict[str, str] = {}
        self._index_cache: tuple[int, dict[str, str], dict[str, float]] | None = None
        self._activity_cache: tuple[int, dict[str, float]] | None = None
        self._session_files_cache: tuple[float, list[Path]] | None = None
        self._catalog_cache: (
            tuple[
                float,
                list[tuple[str, str, float]],
                dict[str, Path],
            ]
            | None
        ) = None

    def _session_index(self) -> tuple[dict[str, str], dict[str, float]]:
        try:
            stat = self.index_path.stat()
        except OSError:
            return {}, {}
        key = stat.st_mtime_ns
        if self._index_cache and self._index_cache[0] == key:
            return self._index_cache[1], self._index_cache[2]
        titles: dict[str, str] = {}
        updated: dict[str, float] = {}
        try:
            with self.index_path.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    item = _load_json(line)
                    if not item or not isinstance(item.get("id"), str):
                        continue
                    thread_id = item["id"]
                    if isinstance(item.get("thread_name"), str):
                        titles[thread_id] = item["thread_name"]
                    timestamp = _parse_timestamp(item.get("updated_at"))
                    if timestamp is not None:
                        updated[thread_id] = max(updated.get(thread_id, 0.0), timestamp)
        except OSError:
            pass
        self._index_cache = (key, titles, updated)
        return titles, updated

    def _activity_times(self) -> dict[str, float]:
        try:
            stat = self.global_state_path.stat()
        except OSError:
            return {}
        key = stat.st_mtime_ns
        if self._activity_cache and self._activity_cache[0] == key:
            return self._activity_cache[1]
        times: dict[str, float] = {}
        try:
            state = json.loads(self.global_state_path.read_text(encoding="utf-8"))
            atom = state.get("electron-persisted-atom-state", {})
            raw = atom.get("thread-user-activity-times-v1", {})
            if isinstance(raw, dict):
                for compound, milliseconds in raw.items():
                    try:
                        sender, thread_id = json.loads(compound)
                    except (TypeError, ValueError):
                        continue
                    if sender != "local" or not isinstance(thread_id, str):
                        continue
                    if isinstance(milliseconds, (int, float)):
                        times[thread_id] = max(times.get(thread_id, 0.0), milliseconds / 1000)
        except (OSError, ValueError, TypeError):
            pass
        self._activity_cache = (key, times)
        return times

    def session_files(self, force: bool = False) -> list[Path]:
        now = time.monotonic()
        if (
            not force
            and self._session_files_cache
            and now - self._session_files_cache[0] < CATALOG_CACHE_SECONDS
        ):
            return list(self._session_files_cache[1])
        if not self.sessions_root.is_dir():
            return []
        files: list[tuple[int, Path]] = []
        for current_root, dirs, names in os.walk(self.sessions_root):
            depth = len(Path(current_root).relative_to(self.sessions_root).parts)
            if depth > 5:
                dirs[:] = []
                continue
            for name in names:
                if not name.endswith(".jsonl"):
                    continue
                path = Path(current_root) / name
                try:
                    files.append((path.stat().st_mtime_ns, path))
                except OSError:
                    continue
        files.sort(reverse=True)
        paths = [path for _, path in files[:MAX_SESSION_FILES]]
        self._session_files_cache = (now, paths)
        return list(paths)

    @staticmethod
    def thread_id_from_path(path: Path) -> str:
        match = path.name.rsplit("-", 5)
        if len(match) == 6:
            return "-".join(match[-5:]).removesuffix(".jsonl")
        return path.stem[-36:]

    def _catalog(
        self,
        force: bool = False,
    ) -> tuple[list[tuple[str, str, float]], dict[str, Path]]:
        now = time.monotonic()
        if (
            not force
            and self._catalog_cache
            and now - self._catalog_cache[0] < CATALOG_CACHE_SECONDS
        ):
            return list(self._catalog_cache[1]), dict(self._catalog_cache[2])
        titles, index_times = self._session_index()
        activity = self._activity_times()
        rows: dict[str, float] = {}
        paths: dict[str, Path] = {}
        for path in self.session_files(force=force):
            thread_id = self.thread_id_from_path(path)
            paths[thread_id] = path
            try:
                modified = path.stat().st_mtime
            except OSError:
                modified = 0.0
            rows[thread_id] = max(
                modified,
                index_times.get(thread_id, 0.0),
                activity.get(thread_id, 0.0),
            )
        recent = [
            (thread_id, titles.get(thread_id) or thread_id[:8], timestamp)
            for thread_id, timestamp in sorted(rows.items(), key=lambda item: item[1], reverse=True)
            if thread_id in paths
        ]
        self._catalog_cache = (now, recent, paths)
        return list(recent), dict(paths)

    def recent_threads(self, force: bool = False) -> list[tuple[str, str, float]]:
        recent, _ = self._catalog(force=force)
        return recent

    def latest_thread_id(self) -> str | None:
        recent = self.recent_threads()
        return recent[0][0] if recent else None

    def thread_id_for_title(
        self,
        title: str,
        rows: list[tuple[str, str, float]] | None = None,
        force: bool = False,
    ) -> str | None:
        normalized = title.strip()
        if not normalized:
            return None
        compact = "".join(normalized.split()).casefold()
        rows = rows if rows is not None else self.recent_threads(force=force)
        matches = [row for row in rows if row[1] == normalized]
        if not matches:
            matches = [
                row
                for row in rows
                if "".join(row[1].split()).casefold() == compact
            ]
        if not matches:
            matches = [
                row
                for row in rows
                if len(compact) >= 6
                and (
                    compact in "".join(row[1].split()).casefold()
                    or "".join(row[1].split()).casefold() in compact
                )
            ]
        return max(matches, key=lambda row: row[2])[0] if matches else None

    def find_session_file(self, thread_id: str) -> Path | None:
        _, paths = self._catalog()
        path = paths.get(thread_id)
        if path is not None:
            return path
        _, paths = self._catalog(force=True)
        return paths.get(thread_id)

    def snapshot(self, thread_id: str | None = None, pinned: bool = False) -> SessionSnapshot | None:
        thread_id = thread_id or self.latest_thread_id()
        if not thread_id:
            return None
        path = self.find_session_file(thread_id)
        if not path:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        cached = self._cache.get(str(path))
        if cached and cached.modified_ns == stat.st_mtime_ns and cached.size == stat.st_size:
            return cached.snapshot
        snapshot = self._inspect(path, thread_id, stat.st_mtime)
        if snapshot is not None:
            payload = self._payload_stats(path, stat)
            snapshot = replace(
                snapshot,
                request_payload_bytes=payload.request_payload_bytes,
                image_count=payload.image_count,
                image_payload_bytes=payload.image_payload_bytes,
                max_success_payload_bytes=payload.max_success_payload_bytes,
                min_failure_payload_bytes=payload.min_failure_payload_bytes,
                request_too_large=payload.request_too_large,
                request_error=payload.request_error,
                workspace_path=self._session_workspace(path),
            )
        self._cache[str(path)] = _FileResult(snapshot, stat.st_mtime_ns, stat.st_size)
        return snapshot

    def _session_workspace(self, path: Path) -> str:
        key = str(path)
        if key in self._workspace_cache:
            return self._workspace_cache[key]
        workspace = ""
        try:
            with path.open("rb") as stream:
                prefix = stream.read(256 * 1024)
            for raw in prefix.splitlines():
                try:
                    item = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(item, dict) or item.get("type") != "session_meta":
                    continue
                payload = item.get("payload")
                if isinstance(payload, dict) and isinstance(payload.get("cwd"), str):
                    workspace = payload["cwd"]
                break
        except OSError:
            pass
        self._workspace_cache[key] = workspace
        return workspace

    def _payload_stats(self, path: Path, stat: os.stat_result) -> _PayloadState:
        key = str(path)
        state = self._payload_cache.get(key)
        if (
            state is not None
            and state.offset == stat.st_size
            and state.modified_ns == stat.st_mtime_ns
        ):
            return state
        if (
            state is None
            or stat.st_size < state.offset
            or (
                stat.st_size == state.offset
                and stat.st_mtime_ns != state.modified_ns
            )
        ):
            state = _PayloadState()
            self._payload_cache[key] = state

        try:
            with path.open("rb") as stream:
                stream.seek(state.offset)
                chunk = stream.read()
        except OSError:
            return state
        state.offset += len(chunk)
        state.modified_ns = stat.st_mtime_ns
        buffered = state.pending + chunk
        lines = buffered.split(b"\n")
        state.pending = lines.pop()
        for raw in lines:
            self._consume_payload_line(raw, state)
        return state

    @staticmethod
    def _image_payload(url: Any) -> int:
        if isinstance(url, dict):
            url = url.get("url")
        if not isinstance(url, str):
            return 0
        return len(url.encode("utf-8"))

    @classmethod
    def _consume_payload_value(cls, value: Any, state: _PayloadState) -> None:
        if isinstance(value, dict):
            if value.get("type") in IMAGE_ITEM_TYPES:
                state.image_count += 1
                state.image_payload_bytes += cls._image_payload(
                    value.get("image_url") or value.get("url")
                )
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    cls._consume_payload_value(nested, state)
        elif isinstance(value, list):
            for nested in value:
                cls._consume_payload_value(nested, state)

    @classmethod
    def _consume_payload_line(cls, raw: bytes, state: _PayloadState) -> None:
        if (
            b'"response_item"' not in raw
            and b'"token_usage_record"' not in raw
            and b'"task_complete"' not in raw
        ):
            return
        try:
            item = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(item, dict):
            return
        kind = item.get("type")
        payload = item.get("payload")
        if kind == "response_item":
            state.request_payload_bytes += len(raw) + 1
            cls._consume_payload_value(payload, state)
            return
        if kind == "token_usage_record":
            state.max_success_payload_bytes = max(
                state.max_success_payload_bytes,
                state.request_payload_bytes,
            )
            return
        if kind != "event_msg" or not isinstance(payload, dict):
            return
        if payload.get("type") != "task_complete":
            return
        error = payload.get("error")
        if not error:
            state.request_too_large = False
            state.request_error = ""
            state.max_success_payload_bytes = max(
                state.max_success_payload_bytes,
                state.request_payload_bytes,
            )
            if (
                state.min_failure_payload_bytes > 0
                and state.request_payload_bytes >= state.min_failure_payload_bytes
            ):
                state.min_failure_payload_bytes = 0
            return
        message = str(error.get("message") if isinstance(error, dict) else error)
        if "413" in message or "too large" in message.casefold():
            state.request_too_large = True
            state.request_error = "413"
            state.min_failure_payload_bytes = (
                state.request_payload_bytes
                if state.min_failure_payload_bytes <= 0
                else min(
                    state.min_failure_payload_bytes,
                    state.request_payload_bytes,
                )
            )

    def _inspect(self, path: Path, thread_id: str, modified: float) -> SessionSnapshot | None:
        lines: list[str] = []
        size = INITIAL_TAIL_BYTES
        while size <= MAX_TAIL_BYTES:
            lines = _tail_lines(path, size)
            if any(
                '"type":"event_msg"' in line
                and '"type":"token_count"' in line
                and '"total_token_usage"' in line
                for line in lines
            ):
                break
            if size >= path.stat().st_size:
                break
            size *= 2
        if not lines:
            return None

        parsed: list[dict[str, Any] | None] = [_load_json(line) for line in lines]
        anchor_index = -1
        anchor_record: dict[str, Any] | None = None
        for index in range(len(parsed) - 1, -1, -1):
            item = parsed[index]
            if not item or item.get("type") != "event_msg":
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            last = info.get("last_token_usage")
            total = info.get("total_token_usage")
            if not isinstance(last, dict) or not isinstance(total, dict):
                continue
            anchor_index = index
            anchor_record = item
            break
        if anchor_record is None:
            return None

        anchor_payload = anchor_record["payload"]
        info = anchor_payload["info"]
        last_usage = info["last_token_usage"]
        total_usage = info["total_token_usage"]
        anchor_tokens = _int(last_usage.get("input_tokens"))
        context_window = _int(info.get("model_context_window"))
        if context_window <= 0:
            return None

        response_id = ""
        for index in range(anchor_index - 1, -1, -1):
            item = parsed[index]
            if not item or item.get("type") != "token_usage_record":
                continue
            payload = item.get("payload")
            if isinstance(payload, dict) and isinstance(payload.get("response_id"), str):
                response_id = payload["response_id"]
            break

        output_start = anchor_index + 1
        if response_id:
            for index in range(anchor_index - 1, -1, -1):
                item = parsed[index]
                if not item or item.get("type") != "response_item":
                    continue
                payload = item.get("payload")
                if (
                    isinstance(payload, dict)
                    and payload.get("type") == "reasoning"
                    and payload.get("encrypted_content") == response_id
                ):
                    output_start = index
                    break

        if output_start == anchor_index + 1:
            # Some providers omit the reasoning item that carries response_id. In
            # that case retain the latest contiguous model-output block before the
            # usage record, stopping at a user prompt or tool result that was
            # already included in the anchor request.
            candidate = anchor_index
            while candidate > 0:
                previous = parsed[candidate - 1]
                if not previous:
                    candidate -= 1
                    continue
                if previous.get("type") in {"token_usage_record", "event_msg", "turn_context"}:
                    candidate -= 1
                    continue
                if previous.get("type") != "response_item":
                    break
                payload = previous.get("payload")
                kind = payload.get("type") if isinstance(payload, dict) else None
                role = payload.get("role") if isinstance(payload, dict) else None
                if kind in {"reasoning", "function_call", "custom_tool_call"} or (
                    kind == "message" and role == "assistant"
                ):
                    candidate -= 1
                    continue
                break
            output_start = min(output_start, candidate)

        delta = 0
        stale = False
        model = ""
        latest_timestamp = _parse_timestamp(anchor_record.get("timestamp"))
        for item in parsed[:anchor_index]:
            if not item or item.get("type") != "turn_context":
                continue
            payload = item.get("payload")
            if isinstance(payload, dict) and isinstance(payload.get("model"), str):
                model = payload["model"]
        for item in parsed[output_start:]:
            if not item:
                continue
            timestamp = _parse_timestamp(item.get("timestamp"))
            if timestamp is not None:
                latest_timestamp = max(latest_timestamp or 0.0, timestamp)
            if item.get("type") == "turn_context":
                payload = item.get("payload")
                if isinstance(payload, dict) and isinstance(payload.get("model"), str):
                    model = payload["model"]
                continue
            if item.get("type") == "event_msg":
                payload = item.get("payload")
                if isinstance(payload, dict):
                    event_type = str(payload.get("type") or "").lower()
                    if "compact" in event_type:
                        stale = True
                continue
            if item.get("type") != "response_item":
                continue
            payload = item.get("payload")
            delta += estimate_model_visible_item(payload)

        projected = max(0, min(context_window, anchor_tokens + delta))
        remaining = max(0, context_window - projected)
        percent = min(100.0, projected / context_window * 100.0) if context_window else 0.0
        total_input = _int(total_usage.get("input_tokens"))
        cached_input = _int(total_usage.get("cached_input_tokens"))
        cache_hit_rate = (
            min(1.0, cached_input / total_input)
            if total_input > 0 and cached_input > 0
            else 0.0 if total_input > 0 else None
        )
        titles, _ = self._session_index()
        return SessionSnapshot(
            thread_id=thread_id,
            title=titles.get(thread_id) or thread_id[:8],
            model=model,
            anchor_ordinal=_int(anchor_record.get("ordinal")),
            anchor_tokens=anchor_tokens,
            surface_delta_tokens=delta,
            projected_tokens=projected,
            context_window=context_window,
            remaining_tokens=remaining,
            percent=percent,
            exact=delta == 0 and not stale,
            stale=stale,
            total_input_tokens=total_input,
            total_cached_input_tokens=cached_input,
            total_cache_write_input_tokens=_int(total_usage.get("cache_write_input_tokens")),
            total_output_tokens=_int(total_usage.get("output_tokens")),
            cache_hit_rate=cache_hit_rate,
            updated_at=latest_timestamp,
            file_path=str(path),
        )
