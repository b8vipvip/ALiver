# AI Live Bridge

Bridge 是运行在 Windows 直播电脑上的本地代理。当前支持：

- 首次启动自动注册；
- WebSocket 长连接与心跳；
- 白名单进程启动/停止；
- Windows WASAPI 设备扫描；
- GPT_OUT 虚拟扬声器回放捕获、实时电平、静音诊断和测试 WAV；
- GPT_IN 虚拟扬声器测试音输出；
- 双虚拟声卡自动推荐、保存和回灌检查；
- Provider 会话命令骨架；
- LiveAvatar LITE 插件预留。

## 启动

```powershell
python bridge\agent.py
```

首次启动会复制 `bridge.example.json` 为 `bridge.local.json`。请按电脑实际路径修改 Chrome 和抖音直播伴侣的位置。

音频路由配置保存在 `bridge/audio_routes.json`。详细说明见 `docs/AUDIO_ROUTING.md`。

Bridge 不接受任意命令，只能启动配置文件中定义的进程 ID。
