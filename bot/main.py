"""Точка входа бота Agrigat Ostin.

Порядок запуска:
1. Проверка настроек (.env) — без BOT_TOKEN бот не стартует
2. Подключение к Telegram (через прокси, если задан PROXY_URL)
3. Снятие webhook, если кто-то его поставил (webhook и polling несовместимы)
4. Создание таблиц БД
5. Запуск планировщика (дайджест 9:00, проверка слежений каждые 20 мин)
6. Health-сервер для хостинга (Render) — только если задан PORT
7. Polling — основной цикл приёма сообщений
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError

from .config import settings
from .db.database import init_db
from .handlers import get_router
from .scheduler import DigestScheduler
from .utils.logging import setup_logging

logger = logging.getLogger(__name__)


def _build_session() -> AiohttpSession | None:
    """Сессия с прокси, если задан PROXY_URL (http/https/socks5)."""
    proxy = (settings.proxy_url or "").strip()
    if not proxy:
        return None
    if proxy.startswith("socks"):
        from aiohttp_socks import ProxyConnector
        return AiohttpSession(connector=ProxyConnector.from_url(proxy))
    return AiohttpSession(proxy=proxy)


async def on_startup(bot: Bot):
    """При запуске."""
    await init_db()
    logger.info("Бот запущен. Пользователи: все команды доступны после /start")


async def main():
    """Запуск бота."""
    # 1. Обязательные настройки
    errors = settings.validate()
    if errors:
        for error in errors:
            print(f"  ✗ {error}")
        print("\nСоздайте .env из .env.example и заполните ключи.")
        return

    setup_logging(settings.log_level)

    # 2. Роутеры команд (все обработчики бота)
    dp = Dispatcher()
    dp.include_router(get_router())

    bot = Bot(
        settings.bot_token,
        session=_build_session(),
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )

    # Проверяем связь с Telegram до старта планировщика — сразу понятная ошибка
    try:
        me = await bot.get_me()
    except TelegramNetworkError:
        await bot.session.close()
        print("=" * 60)
        print("✗ Не удалось подключиться к api.telegram.org.")
        print("  Провайдер блокирует Telegram API.")
        print("  Решение: включи VPN или укажи PROXY_URL в .env, например:")
        print('    PROXY_URL=http://127.0.0.1:8080')
        print('    PROXY_URL=socks5://127.0.0.1:1080')
        print("  (порт возьми из настроек своего VPN/прокси-приложения)")
        print("=" * 60)
        return
    logger.info("Подключено к Telegram как @%s", me.username)

    # 3. Webhook и polling несовместимы: если токен использовался как webhook
    # (например, проектом social-monitor) — снимаем webhook перед polling
    try:
        info = await bot.get_webhook_info()
        if info.url:
            await bot.delete_webhook(drop_pending_updates=True)
            logger.info("Удалён активный webhook (%s) — он конфликтовал с polling", info.url)
    except Exception as ex:
        logger.warning("Не удалось проверить/удалить webhook: %s", ex)

    # 4. Таблицы БД (пользователи, история, отчёты, слежения, подписки)
    try:
        await init_db()
    except Exception as ex:
        logger.warning("init_db: %s", ex)

    # 5. Планировщик: дайджест в 9:00, слежения каждые 20 минут
    scheduler = DigestScheduler(bot)
    scheduler.start()

    # 6. Health-сервер для хостингов (Render): поднимается только если задан PORT
    from .health import start_health_server
    health = await start_health_server()

    # 7. Основной цикл
    logger.info("Старт polling...")
    try:
        await dp.start_polling(bot)
    finally:
        if health:
            health.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
    except Exception:
        logger.exception("Фатальная ошибка")
