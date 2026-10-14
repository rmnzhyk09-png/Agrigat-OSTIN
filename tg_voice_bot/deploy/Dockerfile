FROM python:3.11-slim

# MALLOC_ARENA_MAX=2 — glibc иначе плодит арены по числу потоков и съедает
# сотни мегабайт на ровном месте. OMP_NUM_THREADS=2 — XTTS на CPU всё равно
# дальше 2 потоков не ускоряется, а память на арены растёт.
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
    HF_HOME=/opt/render/project/src/data/models

# ffmpeg — конвертация голосовых и ogg; libsndfile — librosa/звук XTTS
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git curl ca-certificates libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/render/project/src

RUN pip install torch==2.8.0 torchaudio==2.8.0 \
        --index-url https://download.pytorch.org/whl/cpu

COPY deploy/requirements.txt .
RUN pip install -r requirements.txt

COPY bot.py voice_clone.py tts_daemon.py entrypoint.py ./

# voice_ref.wav сюда НЕ копируется: это голос живого человека, репозиторий публичный.
# Образец приезжает по команде /ref в Telegram и живёт на постоянном диске.
RUN mkdir -p data/models

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=600s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["python", "entrypoint.py"]
