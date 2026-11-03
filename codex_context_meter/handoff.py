from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


HANDOFF_VERSION = 2
MAX_FIELD_CHARS = 20_000
MAX_LIST_ITEMS = 30
MAX_ITEM_CHARS = 2_000
DISCOVERY_START = "<!-- CODEX_CONTEXT_METER_HANDOFF:START -->"
DISCOVERY_END = "<!-- CODEX_CONTEXT_METER_HANDOFF:END -->"


def _text(value: Any, fallback: str = "") -> str:
    if not isinstance(value, str):
        return fallback
    return value.strip()[:MAX_FIELD_CHARS]


def _items(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip()[:MAX_ITEM_CHARS])
        if len(result) >= MAX_LIST_ITEMS:
            break
    return result


def _chain_id(value: str, fallback: str) -> str:
    source = value.strip() or fallback.strip() or "default"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", source).strip("-._").lower()
    slug = slug[:48] or "chain"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def _markdown_list(values: Iterable[str], empty: str = "无") -> str:
    items = list(values)
    return "\n".join(f"- {item}" for item in items) if items else f"- {empty}"


def _payload_mb(value: Any) -> str:
    try:
        number = max(0, int(value or 0))
    except (TypeError, ValueError):
        number = 0
    return f"{number / 1_000_000:.1f}MB" if number else "未观测"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).resolve()


def _safe_workspace(value: Any) -> Path | None:
    if isinstance(value, Path):
        raw_value = str(value)
    elif isinstance(value, str):
        raw_value = value
    else:
        return None
    if not raw_value.strip():
        return None
    try:
        path = Path(raw_value).expanduser().resolve()
    except (OSError, ValueError):
        return None
    if not path.is_dir() or path.parent == path:
        return None
    blocked = {
        Path.home().resolve(),
        _codex_home(),
    }
    for name in ("LOCALAPPDATA", "APPDATA", "ProgramFiles", "WINDIR"):
        raw = os.environ.get(name)
        if raw:
            try:
                blocked.add(Path(raw).resolve())
            except (OSError, ValueError):
                pass
    return None if path in blocked else path


def _upsert_managed_block(path: Path, block: str) -> None:
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        existing = ""
    wrapped = f"{DISCOVERY_START}\n{block.rstrip()}\n{DISCOVERY_END}"
    pattern = re.compile(
        re.escape(DISCOVERY_START) + r".*?" + re.escape(DISCOVERY_END),
        re.DOTALL,
    )
    if pattern.search(existing):
        content = pattern.sub(lambda _match: wrapped, existing, count=1)
    elif existing.strip():
        content = existing.rstrip() + "\n\n" + wrapped + "\n"
    else:
        content = "# Handoff\n\n" + wrapped + "\n"
    _atomic_write(path, content)


def _publish_discovery(
    *,
    workspace: Path | None,
    chain_id: str,
    report_path: Path,
    chain_context_path: Path,
    index_path: Path,
    latest: dict[str, Any],
    status: str,
    summary: str,
    next_action: str,
) -> dict[str, Any]:
    warnings = []
    if workspace:
        mirror_root = workspace / ".codex" / "handoffs" / "codex-context-meter"
        local_pointer = workspace / "HANDOFF.md"
        prefix = ".codex/handoffs/codex-context-meter"
    else:
        mirror_root = _codex_home() / "handoffs" / "codex-context-meter"
        local_pointer = _codex_home() / "handoffs" / "HANDOFF.md"
        prefix = str(mirror_root)
    try:
        mirror_root.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            mirror_root / "chain-context.md",
            chain_context_path.read_text(encoding="utf-8"),
        )
        _atomic_write(
            mirror_root / "latest-handoff.md",
            report_path.read_text(encoding="utf-8"),
        )
        mirror_index = json.loads(index_path.read_text(encoding="utf-8"))
        mirror_index["canonicalChainContextPath"] = str(chain_context_path)
        mirror_index["localChainContext"] = str(mirror_root / "chain-context.md")
        mirror_index["localLatestHandoff"] = str(mirror_root / "latest-handoff.md")
        _atomic_write(
            mirror_root / "index.json",
            json.dumps(mirror_index, ensure_ascii=False, indent=2) + "\n",
        )
        mirror_latest = dict(latest)
        mirror_latest["canonicalChainContextPath"] = str(chain_context_path)
        mirror_latest["localChainContext"] = str(mirror_root / "chain-context.md")
        mirror_latest["localLatestHandoff"] = str(mirror_root / "latest-handoff.md")
        _atomic_write(
            mirror_root / "latest.json",
            json.dumps(mirror_latest, ensure_ascii=False, indent=2) + "\n",
        )
        block = "\n".join(
            [
                "# Codex Context Meter 活动交接",
                "",
                f"- 交接链：`{chain_id}`",
                f"- 状态：`{status}`",
                f"- 更新：{latest['createdAt']}",
                f"- 完整历史链：`{prefix}/chain-context.md`",
                f"- 最新快照：`{prefix}/latest-handoff.md`",
                f"- 索引：`{prefix}/index.json`",
                f"- 原始工作目录：`{workspace or '无项目工作目录'}`",
                "",
                "新会话开始前必须完整读取完整历史链；内容较长时分段读完全部历史，"
                "不能只读最新快照或摘要替代。",
                "",
                f"当前目标：{summary}",
                "",
                f"下一步：{next_action}",
            ]
        )
        _upsert_managed_block(local_pointer, block)
    except OSError as error:
        warnings.append(f"workspace mirror unavailable: {type(error).__name__}: {error}")

    global_pointer = _codex_home() / "handoffs" / "HANDOFF.md"
    global_block = "\n".join(
        [
            "# Codex Context Meter 最近交接",
            "",
            f"- 交接链：`{chain_id}`",
            f"- 工作目录：`{workspace or '无项目工作目录'}`",
            f"- 状态：`{status}`",
            f"- 项目内入口：`{workspace / 'HANDOFF.md' if workspace else '无'}`",
            f"- 权威完整链：`{chain_context_path}`",
            f"- 权威索引：`{index_path}`",
            f"- 完整链镜像：`{mirror_root / 'chain-context.md'}`",
            f"- 最新快照镜像：`{mirror_root / 'latest-handoff.md'}`",
            "",
            "仅在当前工作目录与记录一致，或用户明确要求恢复该任务时读取。",
            "读取时必须完整读取完整链档案，不得遗漏旧交接。",
        ]
    )
    try:
        _upsert_managed_block(global_pointer, global_block)
    except OSError as error:
        warnings.append(f"global pointer unavailable: {type(error).__name__}: {error}")
    return {
        "workspace": str(workspace) if workspace else None,
        "pointerPath": str(local_pointer),
        "mirrorRoot": str(mirror_root),
        "globalPointerPath": str(global_pointer),
        "warnings": warnings,
    }


def create_handoff(
    *,
    directory: str | Path,
    chain_name: str,
    thread_id: str | None,
    thread_title: str,
    summary: str,
    status: str = "active",
    completed: Any = None,
    pending: Any = None,
    decisions: Any = None,
    files: Any = None,
    blockers: Any = None,
    verification: Any = None,
    do_not_carry: Any = None,
    next_action: str = "",
    reason: str = "",
    metrics: dict[str, Any] | None = None,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(directory).expanduser().resolve()
    workspace = _safe_workspace(workspace_root)
    chain_id = _chain_id(chain_name, thread_title)
    chain_dir = root / "chains" / chain_id
    latest_path = chain_dir / "latest.json"
    index_path = chain_dir / "index.json"
    chain_context_path = chain_dir / "chain-context.md"
    previous: dict[str, Any] = {}
    try:
        loaded = json.loads(latest_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            previous = loaded
    except (OSError, ValueError):
        pass
    previous_index: dict[str, Any] = {}
    try:
        loaded_index = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(loaded_index, dict):
            previous_index = loaded_index
    except (OSError, ValueError):
        pass
    previous_entries = [
        entry
        for entry in previous_index.get("handoffs", [])
        if isinstance(entry, dict) and isinstance(entry.get("relativePath"), str)
    ]
    known_paths = {entry["relativePath"] for entry in previous_entries}
    for existing_report in sorted(chain_dir.glob("hf-*.md")):
        relative = existing_report.relative_to(root).as_posix()
        if relative in known_paths:
            continue
        previous_entries.append(
            {
                "handoffId": existing_report.stem,
                "relativePath": relative,
                "path": str(existing_report),
                "createdAt": None,
                "status": "historical",
                "threadId": None,
                "threadTitle": "历史交接（索引补全）",
                "previousHandoff": None,
            }
        )
    previous_entries.sort(key=lambda entry: entry["relativePath"])

    now = datetime.now(timezone.utc)
    created_at = now.isoformat().replace("+00:00", "Z")
    handoff_id = f"hf-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    report_path = chain_dir / f"{handoff_id}.md"
    previous_path = _text(previous.get("relativePath"), "") or None
    metrics = metrics if isinstance(metrics, dict) else {}

    completed_items = _items(completed)
    pending_items = _items(pending)
    decision_items = _items(decisions)
    file_items = _items(files)
    blocker_items = _items(blockers)
    verification_items = _items(verification)
    retained_changes = _items(do_not_carry)
    status_text = _text(status, "active")
    reason_text = _text(reason, "manual")
    summary_text = _text(summary, "待确认：继续前必须先明确当前目标。")
    next_text = _text(next_action, "待确认下一步。")
    title_text = _text(thread_title, "未识别会话")
    chain_context_relative = chain_context_path.relative_to(root).as_posix()
    handoff_count = len(previous_entries) + 1

    metadata = [
        "---",
        f"handoff_version: {HANDOFF_VERSION}",
        f"handoff_id: {json.dumps(handoff_id, ensure_ascii=False)}",
        f"chain_id: {json.dumps(chain_id, ensure_ascii=False)}",
        f"created_at: {json.dumps(created_at, ensure_ascii=False)}",
        f"status: {json.dumps(status_text, ensure_ascii=False)}",
        f"reason: {json.dumps(reason_text, ensure_ascii=False)}",
        f"thread_id: {json.dumps(thread_id or '', ensure_ascii=False)}",
        f"thread_title: {json.dumps(title_text, ensure_ascii=False)}",
        f"handoff_count: {handoff_count}",
        f"coverage_complete: true",
        f"chain_context: {json.dumps(chain_context_relative, ensure_ascii=False)}",
        (
            f"previous_handoff: {json.dumps(previous_path, ensure_ascii=False)}"
            if previous_path
            else "previous_handoff: null"
        ),
        f"supersedes: {json.dumps(previous_path or '', ensure_ascii=False)}",
        "---",
    ]
    metric_rows = [
        ("上下文", f"{int(metrics.get('projectedTokens') or 0):,} / {int(metrics.get('contextWindow') or 0):,} token"),
        ("上下文占比", f"{float(metrics.get('percent') or 0):.2f}%"),
        ("剩余 token", f"{int(metrics.get('remainingTokens') or 0):,}"),
        ("缓存命中率", f"{float(metrics.get('cacheHitRate') or 0):.2f}%"),
        ("估算请求体", _payload_mb(metrics.get("requestPayloadBytes"))),
        ("本会话已验证请求体", _payload_mb(metrics.get("maxSuccessfulRequestBytes"))),
        ("本会话已知失败请求体", _payload_mb(metrics.get("minFailedRequestBytes"))),
        ("图片", f"{int(metrics.get('imageCount') or 0)} 张 / {_payload_mb(metrics.get('imagePayloadBytes'))}"),
        ("载荷风险", _text(metrics.get("payloadRisk"), "unknown")),
        ("最近 413", "是" if metrics.get("requestTooLarge") else "否"),
    ]
    body = [
        *metadata,
        "",
        f"# 交接快照 · {title_text}",
        "",
        "> 本文件是当前任务的快速快照。连续转接时必须同时完整读取同目录下"
        "`chain-context.md`；该文件累计全部历史交接，任何上文都不得省略。",
        "",
        "## 1. 当前目标",
        "",
        summary_text,
        "",
        "## 2. 当前状态",
        "",
        f"- 状态：`{status_text}`",
        f"- 原因：`{reason_text}`",
        f"- thread_id：`{thread_id or 'unknown'}`",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        *[f"| {name} | {value} |" for name, value in metric_rows],
        "",
        "## 3. 有效决策与约束",
        "",
        _markdown_list(decision_items),
        "",
        "## 4. 已完成并验证",
        "",
        _markdown_list(completed_items),
        "",
        "### 验证记录",
        "",
        _markdown_list(verification_items),
        "",
        "## 5. 未完成事项",
        "",
        _markdown_list(pending_items),
        "",
        "## 6. 下一步",
        "",
        next_text,
        "",
        "## 7. 关键文件与产物",
        "",
        _markdown_list(file_items),
        "",
        "## 8. 风险与阻塞",
        "",
        _markdown_list(blocker_items),
        "",
        "## 9. 状态变更与历史保留",
        "",
        "以下记录不再作为当前执行指令，但不得从交接链中删除：",
        "",
        _markdown_list(retained_changes, "无"),
        "",
        "## 10. 历史交接覆盖",
        "",
        f"- 当前链累计交接数：{handoff_count}",
        f"- 完整链档案：`{chain_context_relative}`",
        "- 新会话必须先读取完整链档案，再执行当前下一步。",
        "- 历史交接中的事实、决策、已验证产物和失败记录均须保留并核对。",
        "",
        "## 11. 新会话恢复提示",
        "",
        f"继续交接链 `{chain_id}`。先完整读取 `{chain_context_path}`，"
        "不得跳过或仅用摘要替代此前交接。若内容较长，必须分段读完全部历史。",
        "",
        f"当前目标：{summary_text}",
        "",
        f"下一步：{next_text}",
        "",
    ]
    report_text = "\n".join(body)
    _atomic_write(report_path, report_text)
    relative_path = report_path.relative_to(root).as_posix()
    latest = {
        "handoffVersion": HANDOFF_VERSION,
        "chainId": chain_id,
        "handoffId": handoff_id,
        "relativePath": relative_path,
        "path": str(report_path),
        "createdAt": created_at,
        "status": status_text,
        "threadId": thread_id,
        "threadTitle": title_text,
        "previousHandoff": previous_path,
        "handoffCount": handoff_count,
        "coverageComplete": True,
        "chainContext": chain_context_relative,
        "chainContextPath": str(chain_context_path),
        "indexPath": str(index_path),
    }
    entries = list(previous_entries)
    entries = [
        entry
        for entry in entries
        if entry.get("relativePath") != relative_path
    ]
    entries.append(
        {
            "handoffId": handoff_id,
            "relativePath": relative_path,
            "path": str(report_path),
            "createdAt": created_at,
            "status": status_text,
            "threadId": thread_id,
            "threadTitle": title_text,
            "previousHandoff": previous_path,
        }
    )
    index = {
        "handoffVersion": HANDOFF_VERSION,
        "chainId": chain_id,
        "updatedAt": created_at,
        "latestHandoff": relative_path,
        "chainContext": chain_context_relative,
        "coverageComplete": True,
        "handoffCount": len(entries),
        "handoffs": entries,
    }
    report_files = sorted(
        (
            item
            for item in chain_dir.glob("hf-*.md")
            if item.is_file()
        ),
        key=lambda item: (item.name, item.stat().st_mtime_ns),
    )
    archive = [
        "# 交接链完整上下文",
        "",
        f"- 交接链：`{chain_id}`",
        f"- 更新时间：{created_at}",
        f"- 累计交接数：{len(report_files)}",
        "- 本文件是完整保留版，不替代、不省略任何历史交接。",
        "",
    ]
    for item in report_files:
        archive += [
            "---",
            "",
            f"<!-- source: {item.relative_to(root).as_posix()} -->",
            "",
            item.read_text(encoding="utf-8").rstrip(),
            "",
        ]
    _atomic_write(chain_context_path, "\n".join(archive))
    _atomic_write(index_path, json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(latest_path, json.dumps(latest, ensure_ascii=False, indent=2) + "\n")
    discovery = _publish_discovery(
        workspace=workspace,
        chain_id=chain_id,
        report_path=report_path,
        chain_context_path=chain_context_path,
        index_path=index_path,
        latest=latest,
        status=status_text,
        summary=summary_text,
        next_action=next_text,
    )
    return {
        "ok": True,
        "path": str(report_path),
        "latestPath": str(latest_path),
        "handoffId": handoff_id,
        "chainId": chain_id,
        "previousHandoff": previous_path,
        "handoffCount": len(entries),
        "chainContextPath": str(chain_context_path),
        "indexPath": str(index_path),
        "coverageComplete": True,
        **discovery,
    }


def _default_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "CodexContextMeter"


def _read_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file")
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("input must be a JSON object")
        data_dir = _default_data_dir()
        state_file = Path(args.state_file) if args.state_file else data_dir / "widget-state.json"
        state = _read_state(state_file)
        result = create_handoff(
            directory=data_dir / "handoffs",
            chain_name=_text(payload.get("chainId"), _text(state.get("title"), "default")),
            thread_id=_text(state.get("threadId"), "") or None,
            thread_title=_text(state.get("title") or state.get("activeThreadTitle"), "未识别会话"),
            summary=_text(payload.get("summary"), ""),
            status=_text(payload.get("status"), "active"),
            completed=payload.get("completed"),
            pending=payload.get("pending"),
            decisions=payload.get("decisions"),
            files=payload.get("files"),
            blockers=payload.get("blockers"),
            verification=payload.get("verification"),
            do_not_carry=payload.get("doNotCarry"),
            next_action=_text(payload.get("nextAction"), ""),
            reason=_text(payload.get("reason"), "manual"),
            metrics=state,
            workspace_root=state.get("workspacePath"),
        )
        sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
        return 0
    except Exception as error:
        sys.stderr.write(f"{type(error).__name__}: {error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
