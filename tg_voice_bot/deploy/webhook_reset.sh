#!/bin/sh
# Сброс webhook на боте.
#
# Нужен, когда в логах идёт:
#   TelegramConflictError: Conflict: can't use getUpdates method while webhook is active
# Webhook и long-polling несовместимы: пока он висит, getUpdates не работает.
#
# Запуск в Render: Shell -> sh deploy/webhook_reset.sh
# Локально (PowerShell): .\deploy\webhook_reset.ps1
set -e

if [ -z "$BOT_TOKEN" ]; then
  echo "BOT_TOKEN не задан. В Render Shell переменные сервиса доступны как есть,"
  echo "локально — задай вручную: BOT_TOKEN=123456:ABC sh deploy/webhook_reset.sh"
  exit 1
fi

echo "кто я:"
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getMe"
echo

echo "webhook до:"
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
echo

echo "удаляю webhook:"
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
echo

echo "webhook после (ожидаем url:\"\"):"
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
echo
