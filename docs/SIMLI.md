# ALiver × Simli 实时数字人

ALiver 使用 Windows Bridge 捕获已配置的 `GPT_OUT` 回放设备，把 ChatGPT Live 的 PCM16 音频转换为 Simli 所需的 16 kHz 单声道格式，并在本机显示实时数字人窗口。

## 数据流

```text
ChatGPT Live 语音
  → Chrome 输出到 CABLE Input
  → Windows Bridge 捕获 GPT_OUT Loopback
  → 48 kHz 立体声 PCM16 转 16 kHz 单声道 PCM16
  → Simli
  → 本地窗口 ALiver Simli Avatar
  → 抖音直播伴侣 / OBS 窗口捕获
```

Simli 返回的音频默认播放到 Windows 默认输出设备。直播软件可捕获数字人窗口，并按实际推流方案采集该音频设备。

## 安装

更新代码后，在 Windows PowerShell 中执行：

```powershell
cd D:\AI\ALiver
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Windows 会安装 `simli-ai`、`opencv-python` 和项目已有的 `PyAudioWPatch`。

## 创建供应商

控制台进入“供应商”，点击“填入 Simli 推荐模板”，填写：

```json
{
  "api_key": "你的 Simli API Key"
}
```

设置示例：

```json
{
  "face_id": "你的 Face ID",
  "transport": "livekit",
  "model": "fasttalk",
  "handle_silence": true,
  "max_session_length": 3600,
  "max_idle_time": 300,
  "window_title": "ALiver Simli Avatar",
  "window_size": [720, 720],
  "always_on_top": false,
  "play_return_audio": true
}
```

推荐优先使用 `livekit`，网络环境稳定后也可以测试 `p2p`。API Key 会加密保存在 ALiver 数据库中，供应商列表只显示密钥字段名。

## 测试与启动

1. 先在“音频路由”确认 GPT_OUT 测试能检测到 ChatGPT 语音。
2. 在供应商列表点击“测试连接”。
3. 进入“会话”，选择 Simli 供应商和在线 Windows Bridge。
4. 启动会话后会弹出 `ALiver Simli Avatar` 窗口。
5. 让 ChatGPT Live 说话，观察数字人口型和 Bridge 状态。
6. 在抖音直播伴侣中使用“窗口捕获”添加该窗口。

启动 Simli 会话时不要同时运行 ALiver 的 GPT_OUT 录音测试；两者会争用同一个 WASAPI Loopback 设备。

## 常见问题

### `Missing credentials.api_key`

供应商凭据 JSON 没有填写 `api_key`。

### `Missing settings.face_id`

供应商设置 JSON 没有填写 Simli Face ID。

### `GPT_OUT route is not configured`

先在“音频路由”扫描并保存 GPT_OUT；Chrome 输出应指向对应的基础 VB-CABLE 扬声器。

### 缺少 `simli-ai` 或 OpenCV

重新执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 有声音但数字人不动

确认 ChatGPT 的语音确实经过 GPT_OUT，并检查 Bridge 页面 `simli_sessions` 中的 `last_input_dbfs`、`sent_chunks` 和 `error`。

### 远程部署安全

当前推荐 ALiver 服务端与 Bridge 都运行在同一台本机。若以后将服务端部署到远程主机，必须为服务端和 Bridge WebSocket 配置 HTTPS/WSS，避免供应商会话凭据在不安全网络中传输。
