# ALiver ChatGPT Controller

该扩展把 ALiver 服务端的导演文字命令发送到当前打开的 `chatgpt.com` 对话。

## 安装

1. 在 Chrome 打开 `chrome://extensions`。
2. 打开右上角“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择本仓库的 `chrome_extension` 文件夹。
5. 首次安装后建议刷新已经打开的 ChatGPT 标签页。

从 `0.1.1` 开始，扩展发送命令前会检查 ChatGPT 页面的接收端；接收端缺失时会自动注入 `content.js` 并重试一次。因此扩展升级或重新加载后，通常不再依赖手动刷新 ChatGPT 页面。

## 配对

1. 先启动 ALiver 服务端并确认 `http://127.0.0.1:8765` 可访问。
2. 点击 Chrome 工具栏中的 ALiver 扩展图标。
3. 服务端地址填写 `http://127.0.0.1:8765`。
4. 管理令牌填写 `.env` 中的 `ALIVER_ADMIN_TOKEN`，与 ALiver 网页右上角使用的令牌相同。
5. 点击“配对并连接”。
6. 回到 ALiver 控制台“导演”页面，确认扩展显示为 `online`。

## 使用

在 ALiver 的“导演”页面选择扩展，输入导演指令后发送。命令状态会依次变为：

```text
queued -> dispatched -> completed
```

扩展离线时命令会保留在队列中，重新连接后自动发送。若 ChatGPT 正在回答，默认不会插入新消息；可在控制台勾选“正在回答时也发送”。

## 故障排查

- `No ChatGPT tab is open`：先打开 `https://chatgpt.com/`。
- `Receiving end does not exist`：更新到扩展 `0.1.1`；在 `chrome://extensions` 点击该扩展的“重新加载”，然后重试。新版会自动补注入页面控制脚本。
- `page controller injection failed`：确认当前标签页地址是 `chatgpt.com`，再刷新该标签页并重试。
- `composer was not found`：确认当前页面能看到文字输入框，并重新加载扩展。
- 扩展显示离线：确认 ALiver 服务端仍在运行，扩展弹窗点击“重新连接”。
