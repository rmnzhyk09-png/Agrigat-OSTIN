"""Запись в БД: пользователи, история, задачи, отчёты."""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .database import AsyncSessionLocal
from .models import Job, Monitor, Report, SearchHistory, User, Watch, FormatResult

logger = logging.getLogger(__name__)

MAX_WATCHES = 5


async def add_watch(user_id: int, query: str) -> tuple[bool, str]:
    """Добавить слежение. Возвращает (успех, сообщение)."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Watch).filter_by(user_id=user_id,
                                                            is_active=True))
        active = res.scalars().all()
        if len(active) >= MAX_WATCHES:
            return False, f"Максимум {MAX_WATCHES} наблюдений. Удалите лишние: /unwatch"
        if any(w.query.lower() == query.lower() for w in active):
            return False, "Этот аккаунт уже отслеживается."
        session.add(Watch(user_id=user_id, query=query,
                          last_checked=_now()))
        await session.commit()
    return True, "Слежение добавлено."


async def remove_watch(user_id: int, query: str) -> bool:
    """Удалить слежение по запросу (или все, если query='all')."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Watch).filter_by(user_id=user_id,
                                                            is_active=True))
        rows = res.scalars().all()
        removed = False
        for w in rows:
            if query.lower() == "all" or w.query.lower() == query.lower():
                w.is_active = False
                removed = True
        await session.commit()
    return removed


async def list_watches(user_id: int) -> list[str]:
    """Активные слежения пользователя."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Watch).filter_by(user_id=user_id,
                                                            is_active=True))
        return [w.query for w in res.scalars().all()]


async def get_active_watches() -> list[Watch]:
    """Все активные слежения (для планировщика)."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Watch).filter_by(is_active=True))
        return list(res.scalars().all())


async def touch_watch(watch_id: int) -> None:
    """Обновить время последней проверки."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Watch).filter_by(id=watch_id))
        w = res.scalar_one_or_none()
        if w:
            w.last_checked = _now()
            await session.commit()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def upsert_user(tg_user) -> None:
    """Сохранить/обновить пользователя при /start."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).filter_by(user_id=tg_user.id))
        user = res.scalar_one_or_none()
        if not user:
            session.add(User(user_id=tg_user.id,
                             username=tg_user.username,
                             first_name=tg_user.first_name,
                             last_name=tg_user.last_name,
                             is_active=True))
        else:
            user.username = tg_user.username
            user.first_name = tg_user.first_name
            user.is_active = True
        await session.commit()


async def record_search(user_id: int, query: str, results_count: int) -> None:
    """Записать поиск в историю."""
    async with AsyncSessionLocal() as session:
        session.add(SearchHistory(user_id=user_id, query=query[:500],
                                  results_count=results_count))
        await session.commit()


async def record_job(user_id: int, job_type: str, stats: dict) -> None:
    """Записать выполненную задачу мониторинга."""
    async with AsyncSessionLocal() as session:
        session.add(Job(chat_id=user_id, job_type=job_type, status="done",
                        result={"total": stats.get("total", 0)},
                        completed_at=_now()))
        await session.commit()


async def record_report(user_id: int, summary: str, stats: dict) -> None:
    """Сохранить последний отчёт для /report."""
    async with AsyncSessionLocal() as session:
        session.add(Report(monitor_id=0, user_id=user_id, format="html",
                           data={"summary": summary, "stats": stats},
                           file_path=""))
        await session.commit()


async def set_subscription(user_id: int, tg_user, tags: list[str], active: bool) -> None:
    """Создать/обновить подписку с тегами дайджеста."""
    await upsert_user(tg_user)
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Monitor).filter_by(user_id=user_id, name="digest"))
        mon = res.scalar_one_or_none()
        if mon:
            mon.tags = tags
            mon.updated_at = _now()
        elif active:
            session.add(Monitor(user_id=user_id, name="digest", tags=tags,
                                platforms=[]))
        await session.commit()
        # флаг активности пользователя
        res = await session.execute(select(User).filter_by(user_id=user_id))
        user = res.scalar_one_or_none()
        if user:
            user.is_active = active
            await session.commit()


async def get_active_subscriptions() -> list[tuple[int, list[str]]]:
    """Активные подписки: [(user_id, tags)]."""
    async with AsyncSessionLocal() as session:
        users = (await session.execute(
            select(User).filter_by(is_active=True))).scalars().all()
        result = []
        for user in users:
            res = await session.execute(
                select(Monitor).filter_by(user_id=user.user_id, name="digest"))
            mon = res.scalar_one_or_none()
            if mon and mon.tags:
                result.append((user.user_id, mon.tags))
        return result


# ---------- Персистентный кэш результатов (меню выбора формата) ----------

async def save_format_result(user_id: int, chat_id: int, title: str,
                             items: list, stats: dict, ttl_seconds: int = 1800) -> None:
    """Сохранить результат поиска в БД (переживает перезапуск бота)."""
    cutoff = datetime.utcnow() - timedelta(seconds=ttl_seconds)
    async with AsyncSessionLocal() as session:
        # чистим старые записи этого пользователя
        res = await session.execute(
            select(FormatResult).where(
                FormatResult.user_id == user_id,
                FormatResult.created_at < cutoff,
            ))
        for row in res.scalars().all():
            await session.delete(row)
        session.add(FormatResult(user_id=user_id, chat_id=chat_id,
                                 title=title[:500],
                                 items=items, stats=stats))
        await session.commit()


async def load_format_result(user_id: int, ttl_seconds: int = 1800):
    """Загрузить последний результат поиска пользователя (или None)."""
    cutoff = datetime.utcnow() - timedelta(seconds=ttl_seconds)
    async with AsyncSessionLocal() as session:
        # удаляем устаревшие
        res = await session.execute(
            select(FormatResult).where(FormatResult.created_at < cutoff))
        for row in res.scalars().all():
            await session.delete(row)
        await session.commit()
        res = await session.execute(
            select(FormatResult).filter_by(user_id=user_id)
            .order_by(FormatResult.id.desc()).limit(1))
        row = res.scalar_one_or_none()
        if not row:
            return None
        return row.title, row.items, row.stats


async def clear_format_result(user_id: int) -> None:
    """Удалить кэш результата (кнопка «не сохранять»)."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(FormatResult).filter_by(user_id=user_id))
        for row in res.scalars().all():
            await session.delete(row)
        await session.commit()
