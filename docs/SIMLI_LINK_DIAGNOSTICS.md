# Simli 实时链路诊断

ALiver 0.9.0 / Bridge 0.7.0 增加“链路诊断”页，用客观数据判断数字人卡顿、慢动作、音画延迟究竟来自网络、LiveKit/SDK 帧消费、本地渲染还是音频缓冲。

## 自动采样

Simli 会话启动后，Bridge 每约 2 秒自动采样一次。关闭会话时会自动写出最终报告，不需要手动开始录日志。

采样文件：

```text
bridge\diagnostics\link\link-<session-id>-<时间>.jsonl
bridge\diagnostics\link\link-<session-id>-<时间>.report.json
```

`bridge\logs\bundles` 生成的故障包会自动包含 `bridge\diagnostics`，因此这些链路数据也会自动进入 ZIP。

## 网络 / WebRTC

当 Simli 使用 LiveKit transport 时，Bridge 尝试从 LiveKit `Room.get_rtc_stats()` 获取：

- RTT
- 视频/音频 Jitter
- Jitter Buffer 平均延迟
- 区间丢包率
- 视频/音频接收码率
- 视频解码 FPS
- WebRTC 解码丢帧
- ICE Candidate Pair 状态
- UDP / TCP / TURN 路径

如果当前 SDK 或连接模式无法提供某个字段，页面显示“—”，不会把缺失数据伪装成 0。

## 三级视频吞吐

页面重点比较三层 FPS：

```text
LiveKit 解码 FPS
      ↓
ALiver 实际取帧 FPS
      ↓
OpenCV 窗口渲染 FPS
```

判断示例：

```text
LiveKit 25 FPS → ALiver 12 FPS → Render 12 FPS
```

优先判断为 LiveKit→ALiver 的帧消费瓶颈。

```text
LiveKit 25 FPS → ALiver 24 FPS → Render 12 FPS
```

优先判断为 ALiver 本地渲染瓶颈。

如果 LiveKit 本身只有约 10 FPS，同时 RTT/Jitter/丢包较高，则优先检查跨网/WebRTC 网络。

## 音频

页面同时记录：

- GPT_OUT 当前 dBFS
- 送往 Simli 的本地音频队列
- Simli 返回音频缓冲时长
- Windows waveOut pending 时长
- 音频欠载次数
- 输出设备后端和延迟

大量 `audio_underflows` 通常对应听感上的卡带、断续；返回缓冲或 waveOut pending 持续过大则会造成明显延迟。

## 时间线

Bridge 自动记录：

- GPT_OUT 首次检测到有效语音
- 首次有效语音送入 Simli
- Simli 首次返回非静音语音
- 首个视频帧显示
- 首次明显口部运动

这样可以直接计算每一段的真实墙钟延迟，不需要凭主观听感猜测。

## 标准链路测试

1. 启动 Simli 会话。
2. 确认 Chrome 导演扩展已经绑定用于 ChatGPT Live 的目标会话。
3. 打开“链路诊断”。
4. 选择 Bridge 和导演扩展。
5. 点击“运行 20 秒标准链路测试”。
6. 系统自动让 ChatGPT 说一段约 15 秒的固定文本，并持续采样。
7. 测试完成后查看自动瓶颈结论。

建议至少让会话运行 20～30 秒后再判断网络和 FPS，第一次区间样本没有上一帧计数基线，部分区间指标会显示“—”。

## 自动判断阈值

当前第一版规则主要使用：

- RTT 150 ms：偏高；250 ms：高
- Jitter 30 ms：偏高；60 ms：高
- 区间丢包 2%：偏高；5%：高
- LiveKit 解码 FPS 与 ALiver 接收 FPS 相差超过约 28%：帧消费异常
- ALiver 接收 FPS 与渲染 FPS 相差超过约 28%：本地渲染异常
- 本地视频队列 20 帧：开始积压；60 帧：严重积压
- 返回音频缓冲 400 ms：偏大；800 ms：严重
- waveOut pending 250 ms：播放队列偏大

这些阈值用于定位方向，不代替供应商 SLA 或专业网络测试。
