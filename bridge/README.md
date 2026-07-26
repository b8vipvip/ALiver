# AI Live Bridge

Bridge 是运行在 Windows 直播电脑上的本地代理。V1 已支持：

- 首次启动自动注册；
- WebSocket 长连接；
- 心跳和系统信息；
- 白名单进程启动/停止；
- Provider 会话命令骨架；
- LiveAvatar LITE 插件预留。

## 启动

```powershell
python bridge\agent.py
```

首次启动会复制 `bridge.example.json` 为 `bridge.local.json`。请按电脑实际路径修改 Chrome 和抖音直播伴侣的位置。

Bridge 不接受任意命令，只能启动配置文件中定义的进程 ID。
