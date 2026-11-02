# Codex 单会话上下文监控

这是一个独立的 Codex 伴随悬浮组件，只显示当前一个会话：

- 圆环：最近一次 provider usage 锚点加上之后新增模型可见内容的估算，占当前模型上下文窗口的百分比。
- 剩余量：上下文窗口减去当前估算占用量。
- 缓存命中率：当前会话累计 `cached_input_tokens / input_tokens`。

## 启动

双击 `启动Codex上下文监控.cmd`，或运行：

```powershell
pythonw.exe .\codex_context_meter.pyw
```

只要 Codex 主窗口存在且没有最小化，组件就会持续显示并跟随 Codex 窗口右上角。
切换到其它应用、打开浮层或覆盖窗口不会让它消失；只有关闭或最小化 Codex 才隐藏。
默认读取 Codex 侧栏当前真正选中的会话；切换聊天后会立即切换统计目标，而不是等待新消息。

- 左键拖动：以按下时光标在组件内的落点作为抓取中心，使用约 16ms 的原生
  `SetWindowPos` 移动；位置限制在 Codex 客户区内，拖动期间暂停统计重绘。
- 宿主跟随：独立 `pythonw.exe` 子进程读取 Codex 与悬浮窗最新位置，通过
  `SWP_ASYNCWINDOWPOS` 移动现有窗口，不经过 Tk 消息循环，也不强制整窗重绘。
- 数据刷新：后台统计约 180ms 检查一次，界面约 100ms 汇总一次；无变化时不重建图像。
- 会话切换：点击 Codex 后通过命名事件唤醒现有 UIA 监听器，不再重启 PowerShell。
- 外观：原生窗口按卡片圆角裁剪，并关闭 DWM 非客户区阴影，避免外围光晕。
- 左键单击：解除手动锁定并恢复自动跟随当前选中会话。
- 右键单击：查看精确上下文/余量/缓存数字、选择具体会话、恢复自动跟随，或退出组件。

## 计量口径

借鉴 DeepSeek Harness 的“provider 锚点 + 表层增量”结构：

```text
projected = last_token_usage.input_tokens
          + estimate(response items after the usage anchor)
```

锚点来自 Codex 的 `token_count` 事件，是 provider 返回的精确提示词用量。锚点之后尚未产生新 usage 的模型可见消息、工具调用和工具结果，按 DeepSeek Harness 的固定密度规则估算：

```text
ceil(UTF-8 字符数 / 4) + 内容块开销 + 角色开销
```

缓存命中率始终使用当前单个会话的累计 provider usage，不跨会话汇总。

## 请求体与 413

上下文 token 限制和 HTTP 请求体限制是两套独立预算。图片通常以 base64 data URI
保存在会话中；图片较多时，即使 token 占用远低于上下文窗口，也可能触发
`413 Request Entity Too Large`。

插件额外统计：

- 当前会话图片数量和图片 URL 载荷大小。
- 模型可见 `response_item` 的估算请求体大小。
- 本会话已经成功过的最大请求体和已经失败过的最小请求体。
- 最近一次任务是否以 413 结束。

这些信息显示在悬浮窗底部和右键菜单中，不改变上下文圆环的 token 口径。

provider 请求体限制会随上游网关、路由、编码方式和本次实际序列化结果变化，
所以插件不会跨会话套用固定硬上限。会话已有成功/失败证据时优先使用该会话
边界；没有证据时才使用 32MiB 预警、42MiB 严重预警的保守预算。严重预警时
可通过右键菜单“生成交接报告”保存当前指标与 Next Steps 模板。

## 限制

- 锚点后的新增内容只能估算，界面会以 `~` 标记。
- 发生压缩后，旧锚点可能失效；组件会显示“等待下一次 usage 校准”，随后由下一次 provider usage 自动恢复。
- token 窗口未满仍可能因图片 base64 导致的请求体过大而收到 413。
- 自动跟随通过 Windows UI Automation 读取 Codex 侧栏的当前选中项，再按标题解析
  `thread_id`。只有右键菜单“选择会话”会建立持久锁定；左键单击立即恢复自动跟随。
