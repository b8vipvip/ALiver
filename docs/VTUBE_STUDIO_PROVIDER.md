# VTube Studio 本地虚拟形象 Provider

ALiver 0.9.2 增加 `vtube_studio` Bridge Provider，用来先跑通完整直播流程，再替换为真人数字人或云端供应商。

## 前置设置

1. 启动 VTube Studio 并加载 Live2D 模型。
2. 打开“开启 API（允许安装插件）”，保持端口 `8001`。
3. VTube Studio 麦克风选择 `CABLE Output (VB-Audio Virtual Cable)`。
4. Chrome / ChatGPT 输出选择 `CABLE Input (VB-Audio Virtual Cable)`。
5. 单一口型映射使用 `VoiceVolume -> ParamMouthOpenY`。
6. `ParamMouthOpenY` 上的“自动呼吸”必须关闭，否则会覆盖语音口型。呼吸应绑定独立的 `ParamBreath`。

## 供应商配置

在“供应商”页点击“VTube Studio 模板”，默认配置为：

```json
{
  "ws_url": "ws://127.0.0.1:8001",
  "plugin_name": "ALiver",
  "plugin_developer": "b8vipvip",
  "require_model_loaded": true,
  "auto_reconnect": true,
  "reconnect_interval_seconds": 2,
  "connect_timeout_seconds": 12,
  "authorization_timeout_seconds": 20,
  "action_cooldown_ms": 1200,
  "audio_device_name": "CABLE Output (VB-Audio Virtual Cable)",
  "mouth_input_parameter": "VoiceVolume",
  "mouth_output_parameter": "ParamMouthOpenY",
  "hotkeys": {
    "idle": "",
    "talking": "",
    "thinking": "",
    "wave": "",
    "happy": "",
    "surprised": "",
    "reset": ""
  }
}
```

`hotkeys` 可填写 VTube Studio 当前模型里的热键名称或热键 ID。没有动作热键时可以全部留空，音频口型仍正常工作。

## 启动会话

1. 保持 VTube Studio 和 Windows Bridge 都已运行。
2. 在“会话”页选择 VTube Studio 供应商。
3. 选择在线 Bridge。
4. 启动会话。
5. 首次连接时在 VTube Studio 授权弹窗中允许 `ALiver / b8vipvip`。

授权令牌只保存在本机：

```text
%APPDATA%\ALiver\secrets\vtube_studio.json
```

## 数字人调试

打开“数字人调试”时，ALiver 会自动选择最近的活动数字人会话并加载：

- 供应商保存设置；
- 会话覆盖参数；
- 有效合并配置；
- 会话启动响应；
- 所选 Bridge；
- 当前 VTube Studio 版本和 FPS；
- 当前模型名称与模型 ID；
- 当前模型全部可用热键；
- 最近动作、错误和重连状态。

VTube Studio 会话下可直接刷新模型、重新授权、触发当前模型热键，以及测试 `idle / talking / thinking / wave / happy / surprised / reset` 动作映射。

## 安全

VTube Studio API 保持本机地址 `ws://127.0.0.1:8001`。不要把 8001 端口映射到公网。
