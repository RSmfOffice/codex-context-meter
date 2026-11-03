# Handoff Format and Discovery

Current schema: `handoff_version: 2`.

## Discovery order

Codex agents must use this order before starting a related or resumed task:

1. Current workspace `HANDOFF.md`, managed block between
   `CODEX_CONTEXT_METER_HANDOFF:START` and `CODEX_CONTEXT_METER_HANDOFF:END`.
2. Current workspace `.codex/handoffs/codex-context-meter/`.
3. If no valid project/workspace handoff exists, `%CODEX_HOME%\handoffs\HANDOFF.md`;
   when `CODEX_HOME` is unset, use `%USERPROFILE%\.codex`.

Project-local files always take precedence over the global pointer. A global pointer
whose `workspacePath` does not match the current workspace must not be applied
automatically.

## Chain files

The canonical chain is stored under
`%LOCALAPPDATA%\CodexContextMeter\handoffs\chains\<chainId>\`:

- `hf-*.md`: immutable handoff snapshots.
- `latest.json`: latest snapshot and cumulative counts.
- `index.json`: ordered index of every snapshot with `coverageComplete=true`.
- `chain-context.md`: the full concatenation of all snapshots.

Project/workspace mirrors contain:

- `chain-context.md`: full history for local reading.
- `latest-handoff.md`: latest snapshot.
- `latest.json` and `index.json`: local discovery metadata.

## Resume protocol

1. Read the managed `HANDOFF.md` block.
2. Read `latest.json` and `index.json`.
3. Read `chain-context.md` in full. If it is large, read it in chunks until EOF.
4. Treat the latest snapshot as current state, but retain previous facts,
   decisions, completed work, verification, failures, and superseded items.
5. Do not apply an unrelated old handoff to a new task.

## Standard snapshot sections

Every `hf-*.md` contains YAML metadata followed by:

1. Current objective
2. Current status
3. Active decisions and constraints
4. Completed and verified work
5. Pending items
6. Next action
7. Key files and artifacts
8. Risks and blockers
9. State changes and retained history
10. Handoff coverage
11. Resume prompt

## MCP tool

Use `context_meter_handoff` with a stable `chainId` across conversations.

Required fields:

- `chainId`
- `summary`
- `nextAction`

Optional fields:

- `status`
- `reason`
- `completed`
- `pending`
- `decisions`
- `files`
- `blockers`
- `verification`
- `doNotCarry`

The tool returns the current snapshot, full-chain archive, index, project pointer,
and global fallback pointer.
