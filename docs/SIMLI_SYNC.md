# Simli 音画同步与 LIVE_OUT

ALiver 0.7.0 把 Simli 返回的音频作为主时钟。音频先进入缓冲区，视频帧根据 PyAV 时间戳等待播放；过晚的视频帧会被丢弃，避免出现“嘴巴先动完，声音随后才播放”。

## 推荐音频结构

```text
Chrome / ChatGPT Live
  -> CABLE Input（基础 VB-CABLE，GPT_OUT）
  -> ALiver Bridge 捕获并发送给 Simli
  -> Simli 返回同步音频和视频
  -> 视频：ALiver Simli Avatar 窗口
  -> 音频：CABLE-B Input（LIVE_OUT）
  -> 抖音直播伴侣采集 CABLE-B Output
```

GPT_OUT、GPT_IN 和 LIVE_OUT 应使用三条不同的虚拟通道：

- GPT_OUT：基础 VB-CABLE
- GPT_IN：VB-CABLE A
- LIVE_OUT：VB-CABLE B

购买/下载 VB-CABLE A+B 后，A 和 B 是两个独立驱动。如果当前只安装了 A，在 `VBCABLE_B_Driver_Pack*` 文件夹中以管理员身份运行 `VBCABLE_Setup_x64.exe`，点击 Install Driver，然后重启 Windows。

## Simli 设置

现有供应商不需要删除重建。未填写的新设置会自动使用默认值：

```json
{
  "play_return_audio": true,
  "auto_live_out": true,
  "audio_output_device_name": "",
  "sync_prebuffer_ms": 350,
  "video_delay_ms": 0,
  "late_video_drop_ms": 180
}
```

字段说明：

- `auto_live_out`：自动寻找 `CABLE-B Input`、名称包含 `LIVE_OUT` 的设备或 VoiceMeeter AUX。
- `audio_output_device_name`：指定播放端设备名称；留空时自动选择。
- `sync_prebuffer_ms`：会话开始时积累的音频缓冲，默认 350ms。
- `video_delay_ms`：视频相对音频的人工微调。正数让视频稍晚，负数让视频稍早。
- `late_video_drop_ms`：视频比音频落后超过该值时丢弃旧帧。

也可以在启动会话的“本次覆盖设置 JSON”中临时测试：

```json
{
  "sync_prebuffer_ms": 450,
  "video_delay_ms": 80
}
```

## 直播伴侣设置

1. 画面来源选择窗口捕获：`ALiver Simli Avatar`。
2. 音频输入选择录制端：`CABLE-B Output (VB-Audio Cable B)`。
3. 不要同时采集 Windows 系统声音、基础 `CABLE Output` 或 Chrome 原始声音，否则会出现双声、回声或不同步。
4. 数字人窗口不要最小化。

## 状态判断

会话页新增“Simli 音画同步 / LIVE_OUT”面板，主要字段：

- `sync_health`: `good` / `warning` / `bad`
- `av_offset_ms`: 当前视频相对音频的偏差
- `render_fps`: 实际显示帧率
- `audio_output_device`: 当前 LIVE_OUT 播放设备
- `audio_buffer_ms`: 音频缓冲量
- `video_frames_dropped`: 为追赶音频而丢弃的视频帧数

通常 `av_offset_ms` 保持在正负 80ms 内即可认为同步良好。

## 微调方向

- 嘴巴仍比声音早：把 `video_delay_ms` 增加 50～100。
- 声音比嘴巴早：把 `video_delay_ms` 减少 50～100，例如 `-80`。
- 网络偶发卡顿：把 `sync_prebuffer_ms` 增加到 450～600。
- 延迟过大但稳定：逐步降低 `sync_prebuffer_ms`，每次减少 50。

每次修改设置后，需要停止并重新启动 Simli 会话。

## 没有 CABLE B 时

同步器会回退到 Windows 默认扬声器。此模式可用于本机验证口型和声音是否同步，但不适合作为正式直播音频隔离方案。安装 CABLE B 后，无需改代码，重启 Bridge 并重新启动 Simli 会话即可自动选中。
