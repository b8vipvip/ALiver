# Simli 客观音画诊断

ALiver 0.7.1 不再只用播放器调度误差判断同步，而是同时采集真实音频能量、口部区域运动、视频 PTS、视频到达时间和实际显示时间。

## 本次修复

旧同步器直接使用音频轨与视频轨的首个绝对 PTS 差值。WebRTC/RTP 两条轨道的时钟起点可能不同，因此可能错误地让视频等待数秒。旧同步器还会直接按异常视频 PTS 的间隔播放；当 PTS 推算只有约 15 FPS、实际却以约 30 FPS 到达时，画面会表现为约 0.5 倍慢放。

0.7.1 改为：

- 音频与视频都完成预缓冲后同时启动；
- 忽略跨音频轨、视频轨的绝对 PTS 起点差；
- 视频播放时钟固定为 Simli 标准 30 FPS；
- PTS 只用于诊断，不再直接决定动作播放速度；
- 音频输出仍为主时钟，过晚视频帧继续自动丢弃。

Simli Python SDK 的公开说明中，返回视频标准帧率为 30 FPS，音频为 48 kHz 双声道。

## 自动检测

进入“会话”页面，在“Simli 客观音画诊断 / LIVE_OUT”中点击“开始 12 秒自动检测”。检测期间让数字人连续说 8～12 秒。

结果中的方向约定：

- 正偏差：口型晚于声音；
- 负偏差：口型早于声音；
- `video_playback_speed_ratio=1.0`：视频时间轴与真实时间一致；
- `0.5`：视频约为半速；
- `2.0`：视频约为两倍速。

## 核心指标

- `first_onset_offset_ms`：首次非静音语音与首次明显口部运动的时间差。
- `estimated_lip_sync_offset_ms`：音频能量包络和口部运动包络的互相关最佳偏差。
- `correlation_confidence`：`high`、`medium`、`low` 或 `insufficient`。
- `source_pts_fps`：根据 Simli 视频帧 PTS 推算出的帧率。
- `receive_fps`：Bridge 实际收到视频帧的速率。
- `render_fps_recent`：OpenCV 窗口实际显示帧率。
- `video_playback_speed_ratio`：视频时间轴速度与真实时间之比。
- `scheduler_lateness_ms`：显示帧相对音频主时钟的调度迟到量。

## 事件日志

每个会话会生成两种文件：

```text
bridge\diagnostics\simli-<session>-<time>.jsonl
bridge\diagnostics\simli-<session>-<time>.report.json
```

JSONL 会记录：

- `renderer_initialized`
- `first_video_received`
- `tracks_prebuffered`
- `audio_playback_started`
- `first_non_silent_audio`
- `first_video_rendered`
- `first_mouth_motion`
- `sync_snapshot`
- `diagnostic_started`
- `diagnostic_completed`
- `renderer_closing`

自动检测返回值也会带最近 30 条事件，便于直接从控制台判断真实发生顺序。

## 判断标准

- 首次起点偏差在正负 120 ms 内：良好；
- 120～250 ms：需要关注；
- 超过 250 ms：明显不同步；
- 视频速度比在 0.90～1.10：正常；
- PTS FPS 与接收 FPS 差异明显：通常表示媒体时间戳不能直接用于播放节奏；
- 相关性置信度不足时，应使用更长、停顿变化更明显的语句重新检测。
