# LiveTalking 云端控制台

本功能把 LiveTalking 云端配置、video-only 浏览器源、`sessionid` 自动接管和 Windows Bridge PCM 控制接入 ALiver 控制台。

## 入口

启动 ALiver Server 和 Windows Bridge 后，打开主控制台：

```text
http://127.0.0.1:8765/
```

点击顶部的 **LiveTalking 云端**，或直接打开：

```text
http://127.0.0.1:8765/api/livetalking-console
```

## 首次配置

在页面中填写：

- LiveTalking 云端地址，例如 `https://gpu.example.com`
- 与云端 `ALIVER_STREAM_TOKEN` 相同的 PCM Token
- Avatar ID
- 当前在线的 Windows Bridge
- TLS 校验、队列长度和重连参数

保存后，ALiver 会：

1. 使用 `secret_key` 加密 PCM Token 和 viewer key；
2. 将配置写入已被 Git 忽略的 `data/livetalking_cloud_console.json`；
3. 生成一个受限的本地浏览器源 URL；
4. 不在管理 API、Bridge 状态或日志中返回明文 PCM Token。

如果以后再次保存而 Token 输入框留空，原 Token 会继续保留。需要删除时勾选“清除已保存的 PCM Token”。

## 浏览器源工作流

控制台生成的 URL 类似：

```text
http://127.0.0.1:8765/api/livetalking-viewer?key=...
```

把它添加到抖音直播伴侣或 OBS 的浏览器源中。页面会自动完成：

```text
本地 ALiver viewer
        ↓
嵌入云端 aliver.html
        ↓
建立 video-only WebRTC
        ↓
云端返回 LiveTalking sessionid
        ↓
postMessage 交给本地 viewer
        ↓
viewer 调用 ALiver Server
        ↓
ALiver Server 下发 Windows Bridge 命令
        ↓
audio.livetalking.start
        ↓
DSP processed PCM → 云端当前 session
```

浏览器源 URL 中只有受限 viewer key，没有 ALiver 管理令牌和 PCM Token。viewer key 只能读取已保存的公开显示配置，并把新 `sessionid` 绑定到已经固定的 Bridge 和云端配置。

viewer key 泄漏或需要废弃旧浏览器源时，在控制台点击“更换 viewer key”，然后替换直播伴侣中的旧 URL。

## 音频原则

直播伴侣中必须保持：

```text
音频：CABLE-B Output
视频：LiveTalking 浏览器源
```

不要采集 LiveTalking 页面音频。云端页面和本地 viewer 都按 video-only 方式工作，避免与 CABLE-B 形成双声音、延迟回声或叠音。

## 状态与控制

控制台支持：

- 云端 `/api/aliver/health` 健康检查
- 带 Bearer Token 的 `/api/aliver/streams` 活动流检查
- Bridge 配置、启动、停止、状态读取和 interrupt
- 当前连接状态
- 已发送帧数
- 本地丢弃帧数
- 本地发送队列深度
- 最近错误和云端返回消息

预览页与直播伴侣浏览器源会分别创建 WebRTC session。最后打开的浏览器源会自动把 Bridge 切换到它的新 session，这符合正式直播时以直播伴侣源为准的行为。

## 公网部署要求

云端 Nginx 或访问层必须允许本地 ALiver viewer 通过 iframe 打开 `aliver.html`。不要发送阻止嵌入的 `X-Frame-Options: DENY/SAMEORIGIN`，并正确配置 CSP `frame-ancestors`。

公网仍需完成：

- HTTPS/WSS
- `/offer` 和预览页访问保护
- WebRTC UDP 防火墙
- 必要时部署 TURN
- GPU 模型与 Avatar 实机验证
- 音画延迟测量与 CABLE-B 音频偏移校准
