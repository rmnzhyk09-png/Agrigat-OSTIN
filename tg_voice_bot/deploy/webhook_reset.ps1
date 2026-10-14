# Локальный вариант deploy/webhook_reset.sh (нужен VPN).
# .\deploy\webhook_reset.ps1
$ErrorActionPreference = 'Continue'

if (-not $env:BOT_TOKEN) {
    Write-Output 'BOT_TOKEN не задан: $env:BOT_TOKEN = "123456:ABC"'
    exit 1
}
$base = "https://api.telegram.org/bot$($env:BOT_TOKEN)"

Write-Output 'кто я:'
(Invoke-WebRequest -Uri "$base/getMe" -UseBasicParsing -TimeoutSec 20).Content
Write-Output ''
Write-Output 'webhook до:'
(Invoke-WebRequest -Uri "$base/getWebhookInfo" -UseBasicParsing -TimeoutSec 20).Content
Write-Output ''
Write-Output 'удаляю webhook:'
(Invoke-WebRequest -Uri "$base/deleteWebhook?drop_pending_updates=true" -Method POST -UseBasicParsing -TimeoutSec 20).Content
Write-Output ''
Write-Output 'webhook после (ожидаем url:""):'
(Invoke-WebRequest -Uri "$base/getWebhookInfo" -UseBasicParsing -TimeoutSec 20).Content
Write-Output ''
