# Ветка voice: этот Dockerfile собирает ГОЛОСОВОГО бота (папка tg_voice_bot/).
# На ветке main здесь OSINT-бот — там свой Dockerfile, они не мешают друг другу.
FROM python:3.11-slim

# MALLOC_ARENA_MAX=2 — glibc иначе плодит арены по числу потоков и съедает
# сотни мегабайт на ровном месте. libgomp1 нужен torch'у (libgomp.so.1),
# build-essential/python3-dev — страховка, если coqui-tts соберётся из исходников.
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    COQUI_TOS_AGREED=1 \
    MITINI_TTS_INPROCESS=1 \
    MITINI_THREADS=2 \
    MALLOC_ARENA_MAX=2 \
    OMP_NUM_THREADS=2 \
    TOKENIZERS_PARALLELISM=false \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HOME=/app/data/models

# ffmpeg — конвертация голосовых и ogg; libsndfile — librosa/звук XTTS
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git curl ca-certificates libsndfile1 libgomp1 libglib2.0-0 \
        build-essential python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Без пина: так pip ставит пару torch 2.14.1+cpu / torchaudio 2.11.0+cpu,
# под которую совместим torchcodec 0.17 из requirements. Пины тут ломали сборку.
RUN pip install torch torchaudio \
        --index-url https://download.pytorch.org/whl/cpu

COPY tg_voice_bot/deploy/requirements.txt ./requirements-voice.txt
RUN pip install -r requirements-voice.txt

COPY tg_voice_bot/bot.py tg_voice_bot/voice_clone.py tg_voice_bot/tts_daemon.py \
     tg_voice_bot/entrypoint.py ./

# voice_ref.wav сюда НЕ копируется: это голос живого человека, а репозиторий
# публичный. Образец приезжает по команде /ref в Telegram и лежит на диске.
RUN mkdir -p /app/data/models
VOLUME /app/data

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=600s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["python", "entrypoint.py"]
