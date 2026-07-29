# VTube Studio 一键自然动作配置向导

ALiver 0.9.7 在现有 VTube Studio 口型链路上增加程序化自然动作引擎。它通过 VTube Studio Public API 持续注入标准追踪参数，不要求用户先在 VTube Studio 中逐个创建动作热键。

## 一键配置流程

1. 启动 VTube Studio，并加载 Live2D 模型。
2. 启动 ALiver Server 和 Windows Bridge。
3. 启动 VTube Studio 数字人会话。
4. 打开“数字人调试”。
5. 在“VTube Studio 一键自然动作配置向导”中点击“一键扫描并启用”。

向导会扫描：

- VTube Studio 标准输入参数；
- 当前模型 Live2D 参数；
- 当前模型表情文件；
- 已有模型热键。

随后自动启用：

- 静音时的轻微头部、身体位置变化；
- ChatGPT 语音开始时的说话姿态和更明显的自然动作；
- 思考、问候、开心、惊讶和恢复动作；
- 可识别表情文件时的表情叠加。

## 语音自动联动

动作引擎读取 VTube Studio 的 `VoiceVolume` 参数。超过配置阈值时进入 `talking`，连续静音达到保持时间后恢复 `idle`。

该逻辑独立于口型映射：

```text
GPT_OUT 音频
├─ VoiceVolume → ParamMouthOpenY：嘴部口型
└─ VoiceVolume → ALiver Motion Engine：头部、位置、眼神和表情动作
```

## 支持的语义动作

- `idle`：自然待机；
- `talking`：语音期间自然说话动作；
- `thinking`：歪头、视线上移等思考动作；
- `wave`：明显的问候摇摆动作；
- `happy`：微笑和轻微弹跳；
- `surprised`：抬眉、睁眼和后仰感；
- `reset`：停止临时动作并回到自然待机。

导演系统后续可以直接调用这些统一语义动作，不需要知道具体模型参数名。

## 与 VTube Studio 热键的关系

程序化动作和模型已有热键可以叠加：

```text
语义动作
├─ ALiver 程序化参数动作
└─ 可选的 VTube Studio 现有 motion3/表情热键
```

如果模型已有真正的手臂挥手 motion3，可继续把它映射到 `wave`；ALiver 会在程序化问候摇摆的同时触发该热键。

## 限制

VTube Studio Public API 可以读取和注入参数、读取和触发热键、激活已有表情，但不能凭空生成模型未制作的骨骼、手臂参数或 motion3 动画。因此：

- 没有手臂绑定的模型无法通过 API 生成真正的挥手；
- `wave` 默认是头部和身体的问候摇摆；
- 模型没有映射标准追踪参数时，对应动作幅度可能很小或不可见；
- 需要真实手臂动作时，仍应选用自带动作的模型或增加 motion3 资源。

## 默认配置

```json
{
  "motion_engine": {
    "enabled": false,
    "preset": "gentle",
    "fps": 15,
    "auto_speech": true,
    "voice_parameter": "VoiceVolume",
    "speech_threshold": 0.08,
    "speech_hold_ms": 500,
    "idle_intensity": 0.55,
    "talking_intensity": 0.85,
    "action_intensity": 1.0,
    "expressions_enabled": true,
    "expression_map": {
      "thinking": "",
      "happy": "",
      "surprised": ""
    }
  }
}
```

配置由向导保存到当前 VTube Studio 供应商，并立即应用到当前运行会话。下次启动该供应商会话时自动恢复。
