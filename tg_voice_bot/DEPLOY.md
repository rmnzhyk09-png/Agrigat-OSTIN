# Голосовой бот на Render (с клоном голоса)

Ветка `voice`, папка `tg_voice_bot/`. Задача: XTTS v2 (~2 ГБ RAM) влезть в
контейнер Render. Решение — не «впихнуть», а выжать по памяти и не тащить
модель в git.

## Как это устроено

| Что | Решение |
|---|---|
| Модель ~2 ГБ RAM | `voice_clone.py` подбирает режим: **int8** (динамическая квантизация Linear, веса ~1.0 ГБ) → bf16 → fp32. Режим проверяется пробной фразой, битый откатывается. |
| Память сверху | `MALLOC_ARENA_MAX=2`, `OMP_NUM_THREADS=2`, `MITINI_THREADS=2`, `torch.inference_mode()`, `low_cpu_mem_usage` не нужен — квантизация своё делает. |
| Перезагрузка | Модель на диске (`HF_HOME` → `/opt/render/project/src/data/models`), диск 5 ГБ — качает один раз. |
| `voice_ref.wav` в публичном git | **не** лежит в репозитории. Приезжает по команде `/ref` в Telegram, ffmpeg приводит к 24 кГц моно, лежит на постоянном диске. |
| Скорость на CPU | 40–90 c на фразу на 1 ядре. Это расплата за CPU-синтез; на GPU было бы 1–2 c. |
| Первый ответ | Модель грузится в фоне при старте (`warmup`), так что обычно уже готова. |

Тариф: **standard** (2 ГБ, $25) в режиме int8. Если в логах `MemoryError` или
`режим ... не подошёл` — `pro` (8 ГБ, $85), там fp32 влезает гарантированно.

## Переключение существующего сервиса (вручную, 5 минут)

Settings → Build & Deploy:

| Поле | Значение |
|---|---|
| Branch | `voice` |
| Root Directory | `tg_voice_bot` |
| Dockerfile Path | `./deploy/Dockerfile` |
| Docker Context | `.` |

Instance Type: **standard**. Health Check Path: `/health`.

Settings → Environment:

- `BOT_TOKEN` — **оставить прежний**, ничего не менять.
- `OPENAI_API_KEY` — добавить (тот же ключ, что был `LLM_API_KEY` в старом `.env`).
- Остальные старые переменные (`SUPABASE_*`, `DATATECH_*`, `SERPER_*`,
  `HIMERA_*`, `BLACKBIRD_*`, `SNOOP_*`, `RSS_FEEDS`, `BOT_OWNER_ID`) — можно
  удалить, голосовой бот их не читает.

Manual Deploy. В логах ждать:

```
[boot] свободно ~1900 МБ, синтез: in-process, режим auto, образец: нет
[xtts] режим int8: гружу tts_models/multilingual/multi-dataset/xtts_v2
[xtts] режим int8 работает: процесс 1450 МБ, свободно 300 МБ, за 90 с
```

Цифра «процесс NNNN МБ» — фактический RSS. Если она упирается в 2048 и в логах
есть OOM/Killed — переключай на `pro`.

## Как залить голос заказчика

1. Написать боту `/ref`.
2. Отправить голосовое сообщение голосом заказчика (до 45 c, только речь).
3. Бот ответит «Запомнила этот голос» и переключится на клон.

Образец лежит на диске Render — переживает рестарты. `/ref off` удаляет его,
`/status` показывает состояние.

## Если что-то пошло не так

| Симптом | Что делать |
|---|---|
| `MemoryError` / `Killed` в логах | Instance Type → `pro` (8 ГБ). Или оставить standard и `MITINI_MODE=fp32` не поможет — только `int8`. |
| Клон звучит хрипло | int8 режет качество. Поставить `MITINI_MODE=bf16` (ещё ~1.6 ГБ, впритык) или `fp32` на `pro`. |
| Ответ очень долгий | Это CPU. `MITINI_MAX_CHARS=200` ускоряет в ~2 раза. Настоящее лекарство — GPU-хостинг. |
| `XFGB0`/`ffmpeg` ошибки | ffmpeg есть в образе (`python:3.11-slim` + apt). |
| Бот не отвечает | `/status` в Telegram → видно режим и память. |

## Если бот молчит: webhook вместо polling

Симптом в логах:

```
TelegramConflictError: Conflict: can't use getUpdates method while webhook is active
```

Это не «второй бот с тем же токеном» (тот конфликт называется
`terminated by other getUpdates request`). Webhook и polling несовместимы,
пока он висит — бот не видит сообщений вообще.

Что делать:

1. Render → Shell → `sh deploy/webhook_reset.sh`
   Скрипт покажет `getMe` (какой именно токен у сервиса), снимет webhook и
   напечатает `url:""`. Локально (нужен VPN): `.\deploy\webhook_reset.ps1`.
2. Если в `getWebhookInfo` поле `url` непустое и меняется после каждого
   рестарта — webhook ставит кто-то ещё (другой проект/сервис с этим токеном).
   У `getWebhookInfo` есть `ip_address` и `url` — они показывают, кто именно.
3. Голосовой бот снимает webhook сам при старте (`bot.py`, `on_startup`), так
   что после следующего деплоя проблема не вернётся. В логах будет строка
   `webhook ... был активен — снесён`, если он снова появится.

## Локальный запуск (Windows)

```
set BOT_TOKEN=... && set OPENAI_API_KEY=...
python entrypoint.py
```

Локально XTTS живёт в отдельном окружении (`venv_xtts`, Python 3.11) и
`_synth_local` поднимается как subprocess — так же, как раньше. Образец берётся
из `data/voice_ref.wav` (если есть) или приезжает по `/ref`.
