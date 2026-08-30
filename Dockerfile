FROM python:3.12-slim

WORKDIR /app

# Распаковка RAR внутри архивов: rarfile использует libarchive (bsdtar)
RUN apt-get update && apt-get install -y --no-install-recommends libarchive-tools \
    && rm -rf /var/lib/apt/lists/*

# Зависимости отдельным слоем — кэшируется
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код бота
COPY bot/ ./bot/
COPY catalog.json .

# База и сессии — в volume
RUN mkdir -p /app/data
VOLUME /app/data

CMD ["python", "-m", "bot.main"]
