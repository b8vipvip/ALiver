# ALiver

ALiver 是一个**本地优先**的 AI 直播数字人控制服务端，用来统一管理：

- LiveAvatar、Tavus、AKOOL、Mock 等数字人供应商配置；
- 手工选择本次直播使用哪个数字人接口；
- 数字人会话的创建、停止和状态记录；
- 本地 AI Live Bridge 节点注册、心跳和命令下发；
- 运行日志、接口延迟、错误统计和会话审计；
- Windows 本地部署与后续抖音直播伴侣桥接。

> 当前版本是 V1 控制面。Tavus 与 AKOOL 已提供服务端 HTTP 会话适配器；LiveAvatar LITE 的媒体链路依赖 WebRTC/SDK，因此采用 Bridge 执行模式，已预留命令和插件接口。音频捕获、虚拟声卡、数字人播放器以及抖音弹幕采集将在后续 Bridge 迭代中接入。

## 架构

```text
浏览器管理后台
        │
        ▼
ALiver FastAPI 服务端
 ├─ Provider 配置与密钥加密
 ├─ Session 生命周期
 ├─ Bridge WebSocket 控制通道
 ├─ SQLite 日志与统计
 └─ Tavus / AKOOL / LiveAvatar / Mock 适配器
        │
        ▼
AI Live Bridge（Windows 本地代理）
 ├─ 进程白名单管理
 ├─ 音频采集与路由（后续）
 ├─ 数字人 SDK/播放器（后续）
 └─ 抖音直播伴侣联动（后续）
```

更完整的说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## Windows 10 快速启动

### 1. 克隆仓库

```powershell
git clone git@github.com:b8vipvip/ALiver.git
cd ALiver
```

### 2. 创建虚拟环境

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. 创建配置

```powershell
Copy-Item .env.example .env
notepad .env
```

至少把 `ALIVER_SECRET_KEY` 改成一段较长的随机字符串。

### 4. 启动服务端

```powershell
.\scripts\run_windows.ps1
```

浏览器访问：

```text
http://127.0.0.1:8765
```

### 5. 启动本地 Bridge

另开一个 PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python bridge\agent.py
```

首次运行会自动向服务端注册，并把 Bridge ID 与令牌保存到 `bridge/state.json`。

## 供应商配置

在管理后台的“供应商”区域新增配置：

### Tavus

凭据 JSON：

```json
{"api_key":"YOUR_TAVUS_API_KEY"}
```

设置 JSON：

```json
{
  "persona_id": "YOUR_PERSONA_ID",
  "replica_id": "YOUR_REPLICA_ID",
  "require_auth": true,
  "conversation_name": "ALiver Echo Session"
}
```

### AKOOL

凭据 JSON：

```json
{"api_key":"YOUR_AKOOL_API_KEY"}
```

设置 JSON：

```json
{
  "avatar_id": "YOUR_AVATAR_ID",
  "voice_id": "YOUR_VOICE_ID",
  "duration": 3600,
  "stream_type": "agora",
  "mode_type": 2,
  "language": "zh"
}
```

### LiveAvatar

LiveAvatar LITE 需要本地 Bridge 参与 WebRTC 媒体会话。设置 JSON 示例：

```json
{
  "mode": "LITE",
  "avatar_id": "YOUR_AVATAR_ID",
  "transport": "livekit",
  "connector": "liveavatar"
}
```

创建会话时必须选择在线 Bridge。V1 会把命令发送给 Bridge，并记录结果；实际 SDK 媒体连接由后续 `bridge/connectors/liveavatar.py` 实现。

## API

- `GET /api/health`
- `GET/POST /api/providers`
- `POST /api/providers/{id}/test`
- `GET/POST /api/sessions`
- `POST /api/sessions/{id}/stop`
- `GET/POST /api/bridges`
- `POST /api/bridges/{id}/commands`
- `GET /api/logs`
- `GET /api/logs/summary`
- `WS /ws/bridges/{bridge_id}?token=...`

详细请求示例见 [docs/API.md](docs/API.md)。

## 安全说明

- 供应商 API Key 使用本地密钥加密后存入 SQLite；接口不会返回原始密钥。
- 默认仅监听 `127.0.0.1`。
- 若改为 `0.0.0.0` 或放到局域网，请配置 `ALIVER_ADMIN_TOKEN` 并限制防火墙访问。
- Bridge 只允许启动 `bridge/bridge.local.json` 中预先配置的进程，不执行任意 Shell 命令。

## 当前完成度

- [x] 本地 FastAPI 管理服务端
- [x] SQLite 数据库
- [x] 管理后台
- [x] Provider 可插拔接口
- [x] Tavus 会话创建/结束
- [x] AKOOL 会话创建/关闭
- [x] LiveAvatar Bridge 执行模式骨架
- [x] Bridge 注册、心跳、WebSocket 命令
- [x] 日志与延迟统计
- [x] Windows 启动脚本
- [ ] ChatGPT 浏览器音频捕获
- [ ] 虚拟声卡路由
- [ ] LiveAvatar SDK 媒体连接
- [ ] Tavus Echo 音频帧推送
- [ ] AKOOL RTC 音频轨道
- [ ] 抖音弹幕采集与回复队列
- [ ] 抖音直播伴侣窗口/音频自动配置
