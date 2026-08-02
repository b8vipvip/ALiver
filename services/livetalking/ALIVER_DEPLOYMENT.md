# ALiver LiveTalking 云 GPU 部署

本目录是 ALiver 内置的 LiveTalking 源码快照。上游提交和许可证记录在 `UPSTREAM.json`、`UPSTREAM_LICENSE` 中。模型权重、Avatar 数据、录制文件和大媒体未提交到 Git 仓库，需要部署时单独放入挂载目录。

## 最终音视频链路

```text
ChatGPT 原生语音
  -> ALiver DSP
  -> CABLE-B Output -----------------------> 直播伴侣唯一音频
              \
               -> 48kHz DSP PCM
                   -> Bridge 连续重采样为 16kHz mono
                   -> WSS /api/aliver/pcm
                   -> LiveTalking 口型推理
                   -> WebRTC video-only ----> 直播伴侣视频
```

LiveTalking 默认不返回 WebRTC 音频轨。不要在直播伴侣中同时采集 LiveTalking 页面声音和 `CABLE-B Output`，否则会重新出现双声音或回声。

## 1. 云端准备

要求：

- Linux x86-64；
- NVIDIA 驱动和 NVIDIA Container Toolkit；
- Docker Compose v2；
- TCP 8010；
- WebRTC 所需 UDP 端口；
- 域名和 HTTPS/WSS 反向代理用于公网正式使用。

将以下内容放入本目录：

```text
models/wav2lip.pth
data/avatars/<avatar_id>/...
```

复制环境模板：

```bash
cp .env.aliver.example .env.aliver
```

生成 Token：

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
```

把结果写入 `.env.aliver` 的 `ALIVER_STREAM_TOKEN`。Token 不要提交到 Git。

## 2. 构建和启动

```bash
docker compose -f docker-compose.aliver.yml build
docker compose -f docker-compose.aliver.yml up -d
docker compose -f docker-compose.aliver.yml logs -f
```

健康检查：

```bash
curl http://127.0.0.1:8010/api/aliver/health
```

正常结果应包含：

```json
{
  "ok": true,
  "service": "livetalking-aliver-pcm",
  "authentication_configured": true,
  "audio": {
    "format": "s16le",
    "sample_rate": 16000,
    "channels": 1,
    "frame_ms": 20,
    "bytes_per_frame": 640
  },
  "video_only_default": true
}
```

## 3. 建立 WebRTC 数字人会话

先用 LiveTalking WebRTC 页面或 ALiver 后续的 LiveTalking 预览器调用 `/offer`，请求参数需包含：

```json
{
  "type": "offer",
  "sdp": "...",
  "video_only": true,
  "avatar": "wav2lip256_avatar1"
}
```

响应会返回：

```json
{
  "type": "answer",
  "sdp": "...",
  "sessionid": "123456"
}
```

将该 `sessionid` 配置到 Windows Bridge。WebRTC 页面必须持续保持连接；页面关闭后，对应 session 会被清理。

## 4. Windows Bridge 配置

可通过 ALiver Bridge 命令配置，也可以先复制示例文件：

```powershell
Copy-Item .\bridge\livetalking_cloud.example.json .\bridge\livetalking_cloud.local.json
```

编辑：

```json
{
  "enabled": true,
  "ws_url": "wss://your-domain.example/api/aliver/pcm",
  "token": "与云端 ALIVER_STREAM_TOKEN 相同",
  "session_id": "123456",
  "verify_tls": true,
  "max_queue_ms": 400
}
```

Bridge 支持命令：

```text
audio.livetalking.configure
audio.livetalking.start
audio.livetalking.stop
audio.livetalking.status
audio.livetalking.interrupt
```

启用 DSP 后，Bridge 只复制 `processed` 处理后音频，不会发送原声。48 kHz 双声道会连续转换为 16 kHz 单声道，并按每帧 20 ms、640 字节发送。

## 5. 反向代理

`/api/aliver/pcm` 是 WebSocket。Nginx 必须保留 Upgrade：

```nginx
location / {
    proxy_pass http://127.0.0.1:8010;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

正式公网环境使用 TLS，让 Bridge 连接 `wss://`。不要关闭 `verify_tls`，除非仅在可信内网临时测试。

## 6. 队列和打断

服务端默认最多保留约 400 ms 输入音频。积压超过上限时丢弃最旧帧，避免口型延迟不断增长。可使用：

```text
ALIVER_PCM_MAX_QUEUE_MS=200..2000
```

中断时 Bridge 清空本地待发帧并发送 `interrupt`；云端清理输入、特征、渲染和 WebRTC 队列。状态接口：

```bash
curl -H "Authorization: Bearer $ALIVER_STREAM_TOKEN" \
  http://127.0.0.1:8010/api/aliver/streams
```

关注：

- `queue_depth_ms`
- `received_frames`
- `accepted_frames`
- `dropped_frames`
- `interrupts`
- `last_error`

## 7. 更新上游源码

在 ALiver 仓库根目录运行：

```bash
python scripts/vendor_livetalking.py
python scripts/apply_livetalking_aliver_patch.py
```

第一条命令重新生成上游源码快照和 SHA 元数据；第二条命令重放 ALiver 维护的补丁。随后必须重新跑完整 CI 和 GPU 实机验证。

## 尚未由 CI 验证的部分

普通 CI 只验证协议、源码结构、Bridge 重采样和 Python 语法，不会加载 Wav2Lip/MuseTalk 权重，也不会验证具体云 GPU、WebRTC NAT 或端到端音画延迟。部署后必须进行一次 GPU 实机验收。
