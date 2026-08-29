# Деплой бота на сервер

Бот на зарубежном сервере работает 24/7 без VPN и прокси: api.telegram.org,
Bluesky и остальные источники там не заблокированы.

## Вариант 0: Render.com (бесплатно, без сервера)

Готовый архив: `deploy_render.zip` (чистая копия проекта без ключей).

1. **GitHub**: создай пустой репозиторий → «uploading an existing file» →
   распакуй архив и перетащи все файлы (папку `bot` целиком) → Commit
2. **Render.com**: New → **Web Service** → подключи репозиторий
   - Environment: **Docker** (Render сам увидит Dockerfile и render.yaml)
   - Instance Type: **Free**
   - Environment Variables → добавить:
     - `BOT_TOKEN` = токен от @BotFather
     - `RSS_FEEDS` = `https://habr.com/ru/rss/best/daily/,https://lenta.ru/rss/news`
   - Create Web Service
3. **Не засыпал**: Render free засыпает через 15 мин без входящих запросов.
   Зайди на uptimerobot.com (бесплатно) → Add New Monitor → HTTP(s) →
   URL = `https://твой-бот.onrender.com/health` → интервал 10 минут
4. Готово: в логах Render должно быть `Подключено к Telegram как @...`

Нюансы Render Free: старт после сна ~30–60 сек; 750 часов/месяц хватает
на один сервис 24/7; слабый CPU (сборка отчётов чуть медленнее).
PROXY_URL на Render не нужен.

## Вариант 1: Oracle Cloud Free Tier (бесплатно навсегда)

1. Зарегистрируйся: https://cloud.oracle.com → Start for free
   (нужны email, телефон и банковская карта для проверки — **деньги не списывают**
   на Always Free; из РФ регистрация бывает капризной, может понадобиться
   зарубежная карта)
2. Создай виртуалку: Compute → Create Instance
   - Image: **Ubuntu 22.04**
   - Shape: **VM.Standard.A1.Flex** (Always Free: 1–4 OCPU, до 24 ГБ RAM)
   - SSH keys: **Generate key pair** → скачай ОБА файла (приватный `.key`!)
   - Разрешить порт: в Security List добавить Ingress Rule TCP 22 (SSH) — он уже есть
3. После создания скопируй **Public IP** виртуалки
4. Передай мне: Public IP + приватный SSH-ключ (файл) — я залью и запущу бота

Или сам, по шагам (Ubuntu):

```bash
# на сервере (ssh -i ключ ubuntu@IP):
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker ubuntu && exit

# залить проект со своего ПК (в папке unified-monitor):
scp -i ключ -r bot catalog.json requirements.txt Dockerfile docker-compose.yml ubuntu@IP:~/bot/
ssh -i ключ ubuntu@IP
cd bot
nano .env          # вписать BOT_TOKEN=... (и убрать PROXY_URL)
docker compose up -d --build
docker logs -f unified-monitor-bot   # смотреть логи
```

## Вариант 2: дешёвый VPS (~150–300 ₽/мес)

Любой хостер с зарубежной локацией (aeza.is, justhost, timeweb — локация
Франкфурт/Амстердам). Заказываешь Ubuntu 22.04, получаешь IP и root-пароль
(или ключ) — дальше те же команды, что выше.

## Проверка после деплоя

```bash
docker logs unified-monitor-bot
# должно быть: "Подключено к Telegram как @..." и "Start polling"
```

Напиши боту /start в Telegram — если отвечает, всё работает.

## Важно
- На сервере `PROXY_URL` в .env не нужен — оставь пустым
- Один токен = один бот: на сервере и на ПК одновременно запускать нельзя
  (будет Conflict)
- Файл `.env` с токеном не выкладывай в git
