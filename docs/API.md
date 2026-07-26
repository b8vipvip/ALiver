# ALiver API 示例

以下命令默认服务在 `http://127.0.0.1:8765`。

## 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
```

## 创建 Tavus 配置

```powershell
$body = @{
  name = "Tavus Echo"
  provider_type = "tavus"
  credentials = @{ api_key = "YOUR_KEY" }
  settings = @{
    persona_id = "YOUR_PERSONA_ID"
    replica_id = "YOUR_REPLICA_ID"
    require_auth = $true
    conversation_name = "ALiver Echo"
  }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/api/providers `
  -ContentType application/json `
  -Body $body
```

## 创建会话

```powershell
$body = @{
  provider_config_id = "PROVIDER_UUID"
  overrides = @{}
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/api/sessions `
  -ContentType application/json `
  -Body $body
```

## 给 Bridge 下发 Ping

```powershell
$body = @{
  command_type = "ping"
  payload = @{}
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/api/bridges/BRIDGE_UUID/commands `
  -ContentType application/json `
  -Body $body
```

若设置了 `ALIVER_ADMIN_TOKEN`，在管理 API 请求中增加：

```powershell
-Headers @{ "X-ALiver-Token" = "YOUR_TOKEN" }
```
