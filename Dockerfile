FROM python:3.12-slim

WORKDIR /app

# Распаковка RAR внутри архивов: rarfile использует libarchive (bsdtar).
# git + ca-certificates нужны для клонирования Blackbird и скачивания списка сайтов.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libarchive-tools git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Зависимости бота отдельным слоем — кэшируется
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Blackbird OSINT (https://github.com/p1ngul1n0/blackbird) — reverse username/email search.
# Ставим его в отдельный venv, чтобы его жёсткие пины зависимостей
# (aiohttp/reportlab/requests и т.д.) не конфликтовали с ботом.
# Список WhatsMyName (wmn-data.json) в репозитории Blackbird отсутствует —
# качаем при сборке, чтобы бот работал офлайн через --no-update.
RUN git clone --depth 1 https://github.com/p1ngul1n0/blackbird /app/blackbird \
    && python -m venv /app/blackbird-venv \
    && /app/blackbird-venv/bin/pip install --no-cache-dir -r /app/blackbird/requirements.txt \
    && python -c "import urllib.request, os; os.makedirs('/app/blackbird/data', exist_ok=True); urllib.request.urlretrieve('https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json', '/app/blackbird/data/wmn-data.json')"

ENV BLACKBIRD_DIR=/app/blackbird
ENV BLACKBIRD_PYTHON=/app/blackbird-venv/bin/python
ENV BLACKBIRD_FILTER=cat=social
ENV BLACKBIRD_TIMEOUT=120

# Код бота
COPY bot/ ./bot/
COPY catalog.json .

# База и сессии — в volume
RUN mkdir -p /app/data
VOLUME /app/data

CMD ["python", "-m", "bot.main"]
