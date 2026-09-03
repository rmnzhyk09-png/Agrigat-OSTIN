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

# Build-time helper: применяет к Blackbird фильтр заблокированных доменов
# (запускается ниже, ПОСЛЕ clone Blackbird). Копируем заранее — слой кэшируется.
COPY blackbird_patch.py .

# Blackbird OSINT (https://github.com/p1ngul1n0/blackbird) — reverse username/email search.
# Ставим его в отдельный venv, чтобы его жёсткие пины зависимостей
# (aiohttp/reportlab/requests и т.д.) не конфликтовали с ботом.
# Каждый шаг терпимый: недоступность GitHub/raw НЕ рушит сборку —
# бот соберётся без Blackbird (функция /blackbird просто не появится).
RUN git clone --depth 1 https://github.com/p1ngul1n0/blackbird /app/blackbird || echo "WARN: Blackbird clone не удался"
RUN test -d /app/blackbird && python -m venv /app/blackbird-venv || echo "WARN: Blackbird venv не создан"
RUN test -d /app/blackbird && git clone --depth 1 https://github.com/snooppr/snoop /app/snoop || echo "WARN: Snoop clone не удался"
RUN test -x /app/blackbird-venv/bin/pip && /app/blackbird-venv/bin/pip install --no-cache-dir -r /app/blackbird/requirements.txt || echo "WARN: Blackbird pip не выполнен"
# Зависимости Snoop — отдельный venv (свои pin-ы, чтобы не конфликтовать с ботом/Blackbird).
RUN test -d /app/snoop && python -m venv /app/snoop-venv || echo "WARN: Snoop venv не создан"
RUN test -x /app/snoop-venv/bin/pip && /app/snoop-venv/bin/pip install --no-cache-dir -r /app/snoop/requirements.txt || echo "WARN: Snoop pip не выполнен"
RUN test -d /app/blackbird && python -c 'import urllib.request, os; os.makedirs("/app/blackbird/data", exist_ok=True); urllib.request.urlretrieve("https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json", "/app/blackbird/data/wmn-data.json")' || echo "WARN: список WhatsMyName не скачан (работает без него)"
# Патч Blackbird: фильтр заведомо недоступных доменов (Instagram/YouTube/files.fm/t.me),
# чтобы он не пытался искать по заблокированным сетям и не терял время впустую.
# blackbird_patch.py скопирован выше (строка COPY blackbird_patch.py .).
RUN test -d /app/blackbird && python /app/blackbird_patch.py || echo "WARN: Blackbird-патч не применён"

ENV BLACKBIRD_DIR=/app/blackbird
ENV BLACKBIRD_PYTHON=/app/blackbird-venv/bin/python
ENV BLACKBIRD_FILTER=cat=social
ENV BLACKBIRD_TIMEOUT=120
ENV SNOOP_DIR=/app/snoop
ENV SNOOP_PYTHON=/app/snoop-venv/bin/python
ENV SNOOP_TIMEOUT=180

# Код бота
COPY bot/ ./bot/
COPY catalog.json .

# База и сессии — в volume
RUN mkdir -p /app/data
VOLUME /app/data

CMD ["python", "-m", "bot.main"]
