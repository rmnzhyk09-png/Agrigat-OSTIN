"""Модели базы данных."""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, Boolean
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class User(Base):
    """Пользователь бота."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True, nullable=False)
    username = Column(String(255))
    first_name = Column(String(255))
    last_name = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)


class Monitor(Base):
    """Монитор — цель для сбора данных."""
    __tablename__ = "monitors"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    tags = Column(JSON, default=list)
    platforms = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Target(Base):
    """Цель мониторинга (Никнейм в платформе)."""
    __tablename__ = "targets"

    id = Column(Integer, primary_key=True)
    monitor_id = Column(Integer, nullable=False)
    platform = Column(String(50), nullable=False)
    username = Column(String(255), nullable=False)
    raw = Column(Text)  # Исходная ссылка или никнейм


class Report(Base):
    """Отчёт по монитору."""
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    monitor_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    data = Column(JSON)  # Данные отчёта
    format = Column(String(20))  # json, csv, md, pdf
    file_path = Column(String(500))  # Путь к файлу
    created_at = Column(DateTime, default=datetime.utcnow)


class Post(Base):
    """Пост из соцсети."""
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True)
    monitor_id = Column(Integer, nullable=False)
    platform = Column(String(50), nullable=False)
    post_id = Column(String(255), nullable=False)
    text = Column(Text)
    url = Column(String(500))
    author = Column(String(255))
    published_at = Column(DateTime)
    tags = Column(JSON, default=list)
    sentiment = Column(String(20))  # positive, neutral, negative
    created_at = Column(DateTime, default=datetime.utcnow)


class Job(Base):
    """Задача в очереди."""
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, nullable=False)
    job_type = Column(String(50), nullable=False)  # run, digest
    target_id = Column(Integer)
    status = Column(String(20), default="pending")  # pending, processing, done, error
    result = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)


class SearchHistory(Base):
    """История поиска."""
    __tablename__ = "search_history"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    query = Column(String(500), nullable=False)
    results_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Watch(Base):
    """Слежение за аккаунтом в реальном времени."""
    __tablename__ = "watches"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    query = Column(String(255), nullable=False)      # ник или ссылка
    last_checked = Column(DateTime)                  # когда проверяли последний раз
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------- Импорт файлов БД (/import) ----------

class DbImport(Base):
    """Факт загрузки файла БД (локальное зеркало)."""
    __tablename__ = "db_imports"

    id = Column(Integer, primary_key=True)
    file_name = Column(String(500))
    format = Column(String(50))          # sqlite, csv, json, xlsx
    rows_total = Column(Integer, default=0)
    sections_found = Column(Integer, default=0)
    sections_new = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class DbSection(Base):
    """Раздел (категория/субъект), созданный автоматически."""
    __tablename__ = "db_sections"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class DbRecord(Base):
    """Проанализированная запись из загруженного файла."""
    __tablename__ = "db_records"

    id = Column(Integer, primary_key=True)
    import_id = Column(Integer, nullable=False)
    section = Column(String(255), nullable=False)    # раздел/категория
    source = Column(String(255), default="")
    author = Column(String(255), default="")
    text = Column(Text)
    url = Column(String(500), default="")
    date = Column(String(100), default="")
    checksum = Column(String(64), unique=True, nullable=False)
    raw = Column(Text)                               # исходная строка JSON
    created_at = Column(DateTime, default=datetime.utcnow)
