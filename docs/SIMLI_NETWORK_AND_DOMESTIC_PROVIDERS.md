# Simli 网络路由与国内数字人备用供应商

本文对应 ALiver 0.9.1 / Bridge 0.7.1。

## 1. Simli 是否必须使用美国 IP

ALiver 使用以下两类 Simli 连接：

1. `https://api.simli.ai/compose/token`：普通 HTTPS API，用 API Key 和 Face ID 获取会话信息。
2. Simli SDK 建立的 LiveKit/WebRTC 媒体连接：传输返回音频和视频，实际媒体节点通常位于 `*.livekit.cloud`。

Simli 官方接口文档描述了 API Key、`/compose/token`、LiveKit 和 TURN/ICE 接口，但没有写明调用方必须使用美国 IP。

因此：

- 本地网络能直连 `api.simli.ai` 时，HTTPS API 可以不走美国代理。
- 能取得 token 不代表 WebRTC 媒体一定稳定；LiveKit UDP、ICE、TURN 还需要单独验证。
- 中国大陆直连质量没有官方 SLA 保证，应使用 ALiver 链路诊断实测丢包、RTT、Jitter Buffer 和 Decode FPS。

## 2. ALiver 的 Simli 网络模式

Simli 供应商设置新增：

```json
{
  "network_mode": "direct_env"
}
```

可选值：

- `inherit`：继承当前 Python 进程代理环境。
- `no_proxy`：将 Simli/LiveKit 域名加入 `NO_PROXY`，保留其他代理环境变量。
- `direct_env`：加入 `NO_PROXY`，同时清除 Bridge 进程中的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`。

`direct_env` 只能控制应用层环境代理，不能越过 v2rayN 的 TUN、透明代理或系统级路由。

## 3. v2rayN TUN 模式必须配置路由直连

建议把以下规则放在兜底代理规则之前：

```json
{
  "type": "field",
  "domain": [
    "domain:simli.ai",
    "domain:livekit.cloud"
  ],
  "outboundTag": "direct"
}
```

实际会话可能通过 ICE/TURN 返回其他主机或 IP。运行会话后应查看 ALiver 链路诊断中的：

- `livekit_host`
- candidate type
- UDP / TCP / TURN
- RTT、丢包、Jitter Buffer

如出现额外厂商主机，应将它加入 v2rayN 直连规则后重新测试。

推荐对照测试：

```text
A. 当前美国代理线路
B. Simli/LiveKit 域名直连
C. 另一条代理或网络线路
```

每轮使用相同的 20～30 秒标准测试，比较：

- 视频丢包中位数和 P95
- RTT P95 / Max
- Jitter Buffer P95
- LiveKit Decode FPS
- 返回音频 Buffer P95

## 4. 低延迟待机媒体裁剪

ALiver 0.9.1 默认配置：

```json
{
  "low_latency_idle_trim": true,
  "idle_trim_arm_seconds": 0.8,
  "idle_trim_target_audio_ms": 420,
  "idle_trim_target_video_ms": 500
}
```

当 GPT_OUT 从静音切换到新一轮讲话时，Bridge 会在把新语音送入 Simli 前：

1. 裁掉旧的待机返回音频，把 Python 音频队列压到约 420 ms。
2. 裁掉对应的旧视频 look-ahead。
3. 重新锚定视频时钟。
4. 再发送本轮 ChatGPT 语音。

链路诊断会显示裁剪次数、累计裁剪音频毫秒、裁掉的视频帧和裁剪后的 Buffer。

## 5. 国内备用供应商状态

当前已注册以下 Provider/Bridge 预留适配层：

| Provider type | 供应商 | 当前状态 | 推荐用途 |
|---|---|---|---|
| `tencent_digital_human` | 腾讯云智能数智人 | 配置校验、会话编排和 Bridge scaffold | 下一家完整接入 |
| `aliyun_avatar` | 阿里云万相数字人 | 配置校验、会话编排和 Bridge scaffold | 云端 RTC 第二备份 |
| `baidu_xiling` | 百度曦灵数字人 | 配置校验、会话编排和 Bridge scaffold | Windows 本地渲染路线 |

控制台里的“测试连接”目前只校验字段，不会假装已经连接厂商 RTC/SDK。Bridge 启动这三类会话时会返回 `awaiting_manual` 和 `reserved_bridge_connector`。

## 6. 下一家最推荐腾讯云智能数智人

原因：

- 官方提供直播场景的音频/文本驱动能力。
- 有长连接/WebSocket 音频驱动接口。
- 支持 WebRTC、TRTC、RTMP 等云端渲染输出。
- 与当前 `GPT_OUT PCM → Bridge → 数字人 → 直播软件` 架构最接近。

计划中的完整连接器：

```text
GPT_OUT PCM
  → Tencent audio drive WebSocket
  → Tencent cloud render
  → WebRTC/TRTC receive
  → ALiver local window / media output
  → OBS / 抖音直播伴侣
```

第二推荐阿里云万相数字人，其 Avatar Dialog 能通过 WebSocket 输入单声道 PCM，并通过阿里云 RTC 返回实时数字人视频。

百度曦灵的 Windows 端渲染 SDK 很适合本地窗口捕获，但需要对应产品权限、SDK 包、数字人资产和 GPU 环境，接入成本通常高于先做一个云端 RTC Provider。

## 7. 官方资料

- Simli LiveKit integration: `https://docs.simli.com/api-reference/livekit-integration`
- Simli compose token: `https://docs.simli.com/api-reference/compose-token`
- Simli ICE/TURN: `https://docs.simli.com/api-reference/compose-ice`
- v2rayN routing: `https://github.com/2dust/v2rayN/wiki/`（Routing / PAC Mode）
- 腾讯云智能数智人：`https://cloud.tencent.com/document/product/1240/`
- 阿里云万相数字人：`https://help.aliyun.com/zh/model-studio/avatar-dialog-api`
- 百度曦灵数字人：`https://cloud.baidu.com/doc/AI_DH/`
