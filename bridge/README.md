# AI Live Bridge

Bridge 是运行在 Windows 直播电脑上的本地代理。当前已支持：

- 首次启动自动注册；
- WebSocket 长连接；
- 心跳和系统信息；
- 白名单进程启动/停止；
- Windows WASAPI 音频设备扫描；
- 扬声器回放或输入设备实时捕获；
- 实时 RMS、Peak、dBFS 电平上报；
- 保存 1～60 秒 WAV 测试文件；
- Provider 会话命令骨架；
- LiveAvatar LITE 插件预留。

## 启动

```powershell
python bridge\agent.py
```

首次启动会复制 `bridge.example.json` 为 `bridge.local.json`。请按电脑实际路径修改 Chrome 和抖音直播伴侣的位置。

## 音频捕获

安装或更新项目依赖后，Windows 会安装 `PyAudioWPatch`，用于 WASAPI Loopback：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

在 ALiver 控制台打开“音频”页面：

1. 选择在线 Bridge；
2. 扫描音频设备；
3. 优先选择名称带 `[Loopback]` 的设备；
4. 点击“开始捕获”；
5. 让 ChatGPT Live 说一句话并观察实时电平；
6. 停止后到 `bridge\captures` 播放测试 WAV。

WASAPI Loopback 会捕获选中输出设备上的全部声音。为了只捕获 ChatGPT，建议后续安装 VB-CABLE，并把 Chrome 单独输出到该虚拟设备。

Bridge 不接受任意命令，只能执行已实现的控制命令，或启动配置文件中定义的进程 ID。
