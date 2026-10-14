FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0

# ffmpeg нужен для конвертации голосовых (ogg -> wav 16k моно) и для edge-tts/gTTS
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/render/project/src

COPY deploy/requirements.txt .
RUN pip install -r requirements.txt

COPY bot.py voice_clone.py entrypoint.py ./

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["python", "entrypoint.py"]
