# Перевод Render-сервиса на голосового бота

Ветка `voice`. Код в `tg_voice_bot/`, деплой на существующий Web Service.

## Что изменилось против оригинала (mitini)

- **Убран XTTS-клон голоса** (`voice_ref.wav`, coqui-tts, torch). Модель ~2 ГБ
  RAM — в Free не влезала. Синтез идёт через `edge-tts` → при блокировке
  региона (403) автоматически `gTTS`. Голоса: `/voice soft|calm|warm`.
  `/voice clone` вернёт «клон не настроен».
- Добавлен `entrypoint.py`: HTTP `/health` + polling Telegram в одном процессе
  (Renderу нужен порт, а `bot.py` его не слушал).
- Образ лёгкий: `python:3.12-slim` + ffmpeg, без torch. Сборка 2–4 минуты.
- `voice_ref.wav` в репозиторий **не** кладём — голос третьего лица в публичном
  git. Если вернём клон — репозиторий сделать приватным.

## Переключение сервиса в Render (вручную, 5 минут)

Settings → Build & Deploy:

| Поле | Значение |
|---|---|
| Branch | `voice` |
| Root Directory | `tg_voice_bot` |
| Dockerfile Path | `./deploy/Dockerfile` |
| Docker Context | `.` (или пусто) |

Settings → Environment:

- `BOT_TOKEN` — **оставить прежний** (тот же, что у OSINT-бота). Менять не нужно.
- `OPENAI_API_KEY` — добавить (тот же ключ, что `LLM_API_KEY` в старом `.env`).
- Старые переменные (`SUPABASE_*`, `DATATECH_*`, `SERPER_*`, `HIMERA_*`,
  `BLACKBIRD_*`, `SNOOP_*`, `RSS_FEEDS`, `BOT_OWNER_ID`) можно удалить —
  голосовый бот их не читает.

Instance Type: **Free** (512 МБ хватает). Health Check Path: `/health`.

Manual Deploy → смотреть лог: `python-tts` стартует, `health: http://0.0.0.0:8080/health`.

## Обязательно после деплоя

1. **UptimeRobot** → New Monitor → HTTP(s) → `https://<сервис>.onrender.com/health`,
   интервал 10 минут. Без этого Free-засыпает через 15 минут простоя и polling
   встаёт. Настройки Render уже прописаны в `render.yaml` (`plan: free`,
   `healthCheckPath: /health`) — но переменные с `sync: false` вводятся руками.
2. **Один токен = один polling.** Если локально запущен старый OSINT-бот —
   остановить, иначе `Conflict: terminated by other getUpdates request`.

## Локальный запуск

```
set BOT_TOKEN=... && set OPENAI_API_KEY=... && python entrypoint.py
```

`.env` рядом тоже читается. Проверка без Telegram: открыть `/health`.
