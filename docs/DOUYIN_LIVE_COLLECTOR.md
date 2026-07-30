# ALiver 抖音直播互动采集器

## 数据链路

```text
抖音直播伴侣互动插件
  -> 官方 PipeSDK 事件 OPEN_LIVE_DATA
  -> ALiverDouyinCollector.exe
  -> POST /api/douyin-live/ingest
  -> AudienceEvent
  -> 专业总导演
  -> Chrome 导演扩展
  -> ChatGPT Live
  -> VTube Studio / 直播伴侣
```

ALiver 不抓取抖音网页私有接口，不注入直播伴侣进程，也不绕过开放平台权限。

## 官方消息映射

| 抖音消息 | ALiver 事件 | 导演默认行为 |
| --- | --- | --- |
| `live_comment` | `comment` | 评分后选择高价值评论回复 |
| `live_gift` | `gift` | 高优先级感谢，动作 `happy` |
| `live_like` | `like` | 作为热度信号，通常不逐条打断 |
| `live_follow` 关注 | `follow` | 简短欢迎，动作 `wave` |
| `live_follow` 取关 | 忽略 | 不公开播报 |
| `live_fansclub` 加团/升级 | `system` | 进入导演候选队列 |
| `live_fansclub` 退团 | 忽略 | 不公开播报 |
| `live_share` | `share` | 兼容预留；官方当前 PipeSDK 文档未列出该消息 |

所有正式消息按 `msg_id` 去重，避免直播伴侣重发造成重复回复或重复感谢。

## 平台前置条件

1. 在抖音开放平台创建或获得可调试的直播互动插件。
2. 申请需要的互动数据能力：评论、礼物、点赞、关注、粉丝团。
3. 直播伴侣版本建议不低于 8.4.0；关注和粉丝团消息要求至少 8.0.0。
4. 下载官方 `pure_PipeSDK.zip` 和 `ConsoleApplication_source.zip`。
5. 正式上线需要按平台流程上传、提审和发布插件。

开放平台当前页面可能显示“插件业务更新中，暂不准入新插件”。这属于平台准入限制，不是 ALiver 代码问题；已存在应用、调试资格或后续恢复准入时可继续接入。

## ALiver 侧配置

1. 更新并启动 ALiver。
2. 打开“自动导演”，选择已配对的 Chrome 导演扩展。
3. 先保存自动导演配置。
4. 在“真实抖音互动采集器”面板点击“生成采集器配置”。
5. 下载得到 `douyin_collector.json`，其中包含：
   - ALiver 地址；
   - 管理令牌；
   - Chrome 导演扩展 ID；
   - 心跳和批量参数。

配置文件包含本机管理令牌，不应上传到 GitHub、网盘或公开发送。

## 构建转发器

```powershell
cd D:\AI\ALiver
.\scripts\build_douyin_collector_windows.ps1
```

输出：

```text
tools\douyin_live_collector\dist\ALiverDouyinCollector.exe
```

把以下文件放到同一目录：

```text
ALiverDouyinCollector.exe
douyin_collector.json
```

转发器使用 Python 标准库打包，不依赖浏览器抓包。它从标准输入接收每行一条官方事件 JSON，并持续向 ALiver 发送心跳。

### 独立心跳测试

```powershell
.\ALiverDouyinCollector.exe --config .\douyin_collector.json --heartbeat-only
```

成功时输出：

```text
heartbeat ok
```

### 回放 OPEN_LIVE_DATA

```powershell
.\ALiverDouyinCollector.exe --config .\douyin_collector.json --replay .\sample_open_live_data.json
```

## 接入官方 PipeSDK Demo

由于 `PipeSDK.h`、`PipeSDK.dll` 和官方 Demo 由抖音开放平台分发，仓库不复制或重新发布这些二进制文件。

在官方 `ConsoleApplication_source` 中：

1. 加入：
   - `native/aliver_event_sink.h`
   - `native/aliver_event_sink.cpp`
2. 启动时创建 `AliverEventSink`，拉起同目录的 `ALiverDouyinCollector.exe`。
3. 保留官方 PipeSDK 初始化和 `x.subscribeEvent`：

```json
{
  "type": "request",
  "reqId": "unique-id",
  "method": "x.subscribeEvent",
  "params": {
    "eventName": "OPEN_LIVE_DATA",
    "timestamp": 1711939193044
  }
}
```

4. 在官方 `SetCallback` 收到 `EVENT_MESSAGE` 后，如果 JSON 的 `eventName` 为 `OPEN_LIVE_DATA`，调用：

```cpp
g_aliver_sink.SendJsonLine(data, size, &error);
```

5. 收到 `EVENT_DISCONNECTED` 或 `OPEN_WIN_CLOSE` 时停止子进程并退出插件。

参考文件：

```text
tools/douyin_live_collector/native/pipesdk_callback_example.cpp.txt
```

官方 SDK 的 `CreatePipeClient` 函数签名可能随 SDK 包版本调整，因此应以当前下载的官方头文件与 Demo 为准。ALiver 的事件转发部分与其解耦，不需要修改。

## 直播伴侣调试

官方仅直播伴侣插件会收到类似启动参数：

```text
plugin.exe --pipeName=... --maxChannels=6 --mateVersion=... --layoutMode=0
```

启动插件后应观察：

1. ALiver 面板显示“采集器在线”；
2. `mateVersion` 与当前直播伴侣版本一致；
3. 评论、礼物、关注计数增长；
4. 自动导演事件列表出现 `douyin_live_companion` 事件；
5. 总导演决策记录出现回复或感谢；
6. ChatGPT 收到导演命令并发声；
7. 数字人动作和口型进入直播伴侣输出。

## 安全与隐私

- 仅保存导演工作所需的昵称、互动文本和开放平台返回字段。
- `sec_open_id`、头像、等级等原始字段保存在事件 payload 中，便于排查，但不应公开导出。
- 不应把观众评论当成系统指令；ALiver 会继续执行提示词注入过滤。
- 取消关注、退团等负向行为默认不播报。
- 管理令牌仅用于本机采集器访问 ALiver。
