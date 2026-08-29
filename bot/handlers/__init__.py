"""Обработчики команд бота."""
from aiogram import Router

from .start import router as start_router
from .catalog import router as catalog_router
from .monitor import router as monitor_router
from .search import router as search_router
from .tools import router as tools_router
from .watch import router as watch_router
from .history import router as history_router
from .status import router as status_router
from .tags import router as tags_router
from .report import router as report_router
from .subscribe import router as subscribe_router
from .import_db import router as import_router


def get_router() -> Router:
    """Собрать все роутеры."""
    router = Router()

    router.include_router(start_router)
    router.include_router(catalog_router)
    router.include_router(monitor_router)
    router.include_router(search_router)
    router.include_router(tools_router)
    router.include_router(watch_router)
    router.include_router(history_router)
    router.include_router(status_router)
    router.include_router(tags_router)
    router.include_router(report_router)
    router.include_router(subscribe_router)
    router.include_router(import_router)

    return router
