# Codex Context Meter Plugin

Codex 个人插件：随 Codex 启动单会话上下文圆环、缓存命中率悬浮组件，并随聊天切换更新目标。

## 安装

前置条件：Python 3.11+、Pillow、Codex CLI、Node.js 18+ 和 Windows PowerShell 5.1+。

在仓库根目录运行：

```powershell
git clone https://github.com/RSmfOffice/codex-context-meter.git
cd codex-context-meter
python -m pip install -r requirements.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\codex-context-meter-plugin\scripts\install.ps1
```

安装器会：

1. 将根目录 Python 源码自动同步到插件 `app/`。
2. 将插件复制到 `%USERPROFILE%\plugins\codex-context-meter`。
3. 写入个人 marketplace 条目并执行 `codex plugin add`。
4. 注册登录级计划任务 `Codex Context Meter`。
5. 启动 VBS 快路径和 supervisor；前者直接拉起组件，后者负责健康检查与恢复。

从发布压缩包安装时，请在解压目录按相同方式运行安装器。首次安装后重启一次
Codex，让 MCP 工具和插件界面进入当前进程。

## 生命周期

- 计划任务在用户登录时并行启动悬浮组件和 supervisor，首屏不等待 PowerShell 初始化。
- Codex 关闭后 supervisor 停止对应悬浮组件，下次 Codex 启动时重新拉起。
- 其它普通窗口获得焦点时，悬浮组件仍保持显示。
- 只有 Codex 窗口关闭或最小化时隐藏。

## MCP 工具

- `context_meter_status`：读取当前 `thread_id`、上下文占用、余量、缓存命中率及新鲜度。
- `context_meter_show`：清除本次会话暂停标记并恢复组件。
- `context_meter_stop`：暂停组件，直到当前 Codex 进程退出。

## 卸载

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "$env:USERPROFILE\plugins\codex-context-meter\scripts\uninstall.ps1"
```

卸载会停止悬浮组件、注销计划任务并移除插件，安装目录会移动到带时间戳的 `.removed-*` 目录，便于恢复。
