"""Импорт файлов БД: парсинг → классификация → сохранение (Supabase)."""
from .store import import_database_file

__all__ = ["import_database_file"]