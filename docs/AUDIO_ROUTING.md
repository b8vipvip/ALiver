# GPT_IN / GPT_OUT 双虚拟声卡路由

ALiver 0.3 将音频链路拆成两个完全隔离的虚拟通道：

```text
GPT_OUT：Chrome / ChatGPT Live 语音 → 虚拟扬声器 → ALiver Loopback → 数字人
GPT_IN ：弹幕 TTS / 连线音频 → ALiver 写入虚拟扬声器 → 虚拟麦克风 → ChatGPT Live
```

两条链路不能使用同一个虚拟声卡，否则 ChatGPT 的回答会重新进入自己的麦克风，形成回灌和自言自语。

## 推荐设备

安装两组独立设备，例如：

- `VB-CABLE`：作为 GPT_OUT；
- `VB-CABLE A` 或 `VB-CABLE B`：作为 GPT_IN。

也可以使用 VoiceMeeter 的不同 VAIO 通道，但不要把 ToDesk Virtual Audio 当作长期固定路由。

## Windows 配置

1. 在 ALiver“音频路由”页面扫描设备。
2. 选择 GPT_OUT 的 Loopback，例如 `CABLE Input ... [Loopback]`。
3. 选择 GPT_IN 的写入设备，例如 `CABLE-A Input ...`。
4. 保存路由。
5. Windows 10：设置 → 系统 → 声音 → 应用音量和设备首选项，将 Chrome 输出改为 GPT_OUT 对应的虚拟扬声器。
6. ChatGPT Live 的麦克风选择 ALiver 显示的 GPT_IN 配对虚拟麦克风，例如 `CABLE-A Output ...`。

物理麦克风不参与该方案。

## 测试

- GPT_OUT：启动自动测试，让 ChatGPT Live 说话；ALiver 应出现电平并生成 WAV。
- GPT_IN：发送 2 秒测试音；ChatGPT Live 的麦克风电平应有变化。

路由配置保存在本地 `bridge/audio_routes.json`，该文件不会提交到 GitHub。
