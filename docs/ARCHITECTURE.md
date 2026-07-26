# ALiver V1 架构

## 目标

ALiver 将“管理控制面”和“本地实时媒体面”拆开：

- **服务端控制面**：供应商密钥、会话生命周期、日志、统计、Provider 选择。
- **Windows Bridge**：浏览器音频、虚拟声卡、WebRTC SDK、播放器和直播伴侣联动。

即便服务端也部署在本机，仍保持这个边界，后续才能平滑迁移到局域网或云端。

## 服务端组件

```text
FastAPI
├─ Provider Registry
│  ├─ MockProvider
│  ├─ TavusProvider
│  ├─ AkoolProvider
│  └─ LiveAvatarProvider (Bridge execution)
├─ Session Service
├─ Bridge Hub (WebSocket)
├─ Encrypted Credential Store
├─ SQLite
├─ Event Log / Latency Summary
└─ Vanilla JS Admin UI
```

## Provider 执行模式

### server_http

适用于服务端可以直接创建/结束会话的接口：

- Tavus：`POST /v2/conversations`，结束会话时调用对应 end 接口。
- AKOOL：创建/关闭 Streaming Avatar session。
- Mock：本地测试。

### bridge

适用于必须在直播电脑上建立 WebRTC 或访问本地音频设备的接口：

- LiveAvatar LITE。

服务端先写入会话记录，再通过 Bridge Hub 下发 `provider.start_session`。Bridge 返回启动结果，服务端更新状态和日志。

## Bridge 通信协议

服务端命令：

```json
{
  "type": "command",
  "command_id": "uuid",
  "command_type": "provider.start_session",
  "payload": {}
}
```

Bridge 结果：

```json
{
  "type": "result",
  "command_id": "uuid",
  "ok": true,
  "data": {"status": "active"}
}
```

## 后续媒体面

```text
Douyin 弹幕/连线声音
        ↓
Comment & Guest Audio Adapter
        ↓
内部 TTS / 虚拟麦克风 GPT_IN
        ↓
ChatGPT Live 浏览器
        ↓
WASAPI 捕获 GPT_OUT
        ↓
Provider Connector
        ↓
同步数字人音视频
        ↓
本地无边框播放器
        ↓
抖音直播伴侣窗口捕获
```

后续连接器保持插件化：

```text
bridge/connectors/
├─ liveavatar.py
├─ tavus_echo.py
├─ akool_rtc.py
└─ mock_player.py
```

## 安全边界

- API Key 只存服务端加密数据库。
- 浏览器管理 API 可选 `X-ALiver-Token`。
- Bridge 使用独立随机令牌和哈希校验。
- Bridge 的进程控制只能引用本地白名单 `process_id`。
- 不支持远程任意 Shell。
