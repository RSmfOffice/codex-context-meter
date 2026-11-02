# Contributing

1. 使用 Windows、Python 3.11+ 和 Pillow。
2. 修改根目录 `codex_context_meter/` 后运行 `scripts/sync-runtime.ps1`。
3. 提交前运行 `scripts/check.ps1`。
4. 不要提交 `%LOCALAPPDATA%` 下的运行数据、日志、认证文件或会话内容。
5. 行为变化需要同时更新 README、CHANGELOG 和相关说明。

插件运行副本位于 `codex-context-meter-plugin/app/`，必须与根目录源码保持一致。
