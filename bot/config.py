"""Конфигурация бота из .env файлов."""
import os
from pathlib import Path


class Settings:
    """Настройки бота."""

    def __init__(self):
        # Загружаем переменные окружения из .env (или .env.txt как запасной вариант)
        env_path = Path(__file__).parent.parent / ".env"
        if not env_path.exists():
            alt = Path(__file__).parent.parent / ".env.txt"
            if alt.exists():
                env_path = alt
        if env_path.exists():
            from dotenv import load_dotenv
            load_dotenv(env_path)

        # Telegram
        self.bot_token = os.getenv("BOT_TOKEN", "")

        # Владелец бота (Telegram user id) — для админ-команд, например /dbreset.
        # 0 = владелец не задан, админ-команды отключены.
        self.owner_id = _env_int("BOT_OWNER_ID", 0)

        # Прокси для бота (если провайдер блокирует api.telegram.org):
        # http://127.0.0.1:8080 или socks5://127.0.0.1:1080
        self.proxy_url = os.getenv("PROXY_URL", "")
        
        # База данных
        self.db_url = os.getenv("DB_URL", "sqlite+aiosqlite:///./data/bot.db")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        
        # Каталог
        self.catalog_enabled = os.getenv("CATALOG_ENABLED", "true").lower() == "true"
        
        # Поисковые движки
        self.serper_api_key = os.getenv("SERPER_API_KEY", "")
        self.serpapi_api_key = os.getenv("SERPAPI_API_KEY", "")
        self.brave_api_key = os.getenv("BRAVE_API_KEY", "")
        
        # Соцсети
        self.reddit_client_id = os.getenv("REDDIT_CLIENT_ID", "")
        self.reddit_client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
        self.github_token = os.getenv("GITHUB_TOKEN", "")
        self.vk_service_token = os.getenv("VK_SERVICE_TOKEN", "")
        self.twitter_bearer_token = os.getenv("TWITTER_BEARER_TOKEN", "")
        self.youtube_api_key = os.getenv("YOUTUBE_API_KEY", "")
        
        # LLM
        self.llm_api_key = os.getenv("LLM_API_KEY", "")
        self.llm_base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.llm_model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        
        # Скрапинг
        self.max_scrape_pages = _env_int("MAX_SCRAPE_PAGES", 15)
        self.request_timeout = _env_float("REQUEST_TIMEOUT", 20.0)
        self.rate_limit_per_second = _env_float("RATE_LIMIT_PER_SECOND", 5.0)

        # RSS-ленты для /find (через запятую в .env: RSS_FEEDS=url1,url2)
        self.rss_feeds = [u.strip() for u in os.getenv("RSS_FEEDS", "").split(",") if u.strip()]

        # Каталог для файлов, загруженных через /import
        self.upload_dir = os.getenv("UPLOAD_DIR", "data/uploads")

        # Supabase — хранение импортированных БД (см. supabase/schema.sql)
        # URL вида https://xxxx.supabase.co
        self.supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        # Service role key из Supabase → Settings → API (секрет!)
        self.supabase_service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        # Реф проекта и токен для авто-создания таблиц (опционально)
        self.supabase_project_ref = os.getenv("SUPABASE_PROJECT_REF", "").strip()
        self.supabase_pat = os.getenv("SUPABASE_ACCESS_TOKEN", "").strip()

        # Supabase Storage: бакет для сохранения загруженных файлов (/import_url).
        # Публичный бакет даёт прямую ссылку на файл для просмотра в админке.
        self.supabase_bucket = os.getenv("SUPABASE_BUCKET", "imports").strip()
        self.supabase_bucket_public = os.getenv("SUPABASE_BUCKET_PUBLIC",
                                                "true").lower() == "true"

        # Blackbird — OSINT поиск по никнейму/email (готовый инструмент p1ngul1n0/blackbird)
        # Путь к папке blackbird (там лежит blackbird.py). Пусто = не подключён.
        self.blackbird_dir = os.getenv("BLACKBIRD_DIR", "").strip()
        # Python Blackbird (желательно отдельный venv, чтобы не конфликтовали зависимости).
        # Пусто = системный/проектный python.
        self.blackbird_python = os.getenv("BLACKBIRD_PYTHON", "").strip()
        # Максимальное время (сек) на один поиск Blackbird
        self.blackbird_timeout = _env_int("BLACKBIRD_TIMEOUT", 120)

        # DataTech — параллельный поиск по внешним базам (best-effort API).
        # Пусто = не подключён. Задайте DATATECH_API_KEY (и при необходимости
        # DATATECH_BASE_URL / DATATECH_MODE) чтобы включить.
        self.datatech_api_key = os.getenv("DATATECH_API_KEY", "").strip()
        self.datatech_base_url = os.getenv("DATATECH_BASE_URL", "").strip()
        self.datatech_mode = os.getenv("DATATECH_MODE", "auto").strip()

        # Snoop — OSINT username presence-check по 400+ сайтам
        # (https://github.com/snooppr/snoop). Запускается как отдельный процесс.
        # SNOOP_DIR = папка со snoop-ом; SNOOP_PYTHON = (опц.) python для него.
        self.snoop_dir = os.getenv("SNOOP_DIR", "").strip()
        self.snoop_python = os.getenv("SNOOP_PYTHON", "").strip()
        self.snoop_timeout = _env_int("SNOOP_TIMEOUT", 180)
        # Домены, по которым запрещено искать (заблокированы средой развёртывания).
        self.blocked_hosts = [h.strip().lower() for h in
                              os.getenv("BLOCKED_HOSTS",
                                        "instagram.com,youtube.com,youtu.be,files.fm,"
                                        "t.me,telegram.org,threads.net,tiktok.com")
                              .split(",") if h.strip()]

    def validate(self) -> list[str]:
        """Проверяет обязательные настройки."""
        errors = []
        if not self.bot_token:
            errors.append("BOT_TOKEN не задан (обязательно)")
        return errors


def _env_int(name: str, default: int) -> int:
    """Безопасный int из env: битая переменная не роняет бот при импорте."""
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        print(f"  ! {name} не число («{raw}») — использую по умолчанию {default}")
        return default


def _env_float(name: str, default: float) -> float:
    """Безопасный float из env (аналог _env_int)."""
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        print(f"  ! {name} не число («{raw}») — использую по умолчанию {default}")
        return default


settings = Settings()
