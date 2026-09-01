"""Планировщик: ежедневный дайджест по подпискам."""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class DigestScheduler:
    """Дайджест каждый день в 9:00 по всем активным подпискам."""

    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()
        self.running = False

    def start(self):
        """Запустить планировщик."""
        if self.running:
            return
        self.scheduler.add_job(
            self.send_daily_digest,
            CronTrigger(hour=9, minute=0),
            id="daily_digest",
        )
        self.scheduler.add_job(
            self.check_watches,
            CronTrigger(minute="*/20"),
            id="watch_check",
        )
        self.scheduler.start()
        self.running = True

    def stop(self):
        """Остановить планировщик."""
        if self.running:
            self.scheduler.shutdown()
            self.running = False

    async def send_daily_digest(self):
        """Сводка упоминаний за сутки по тегам каждого подписчика."""
        from .db import repo
        from .tasks import run_monitoring

        try:
            subs = await repo.get_active_subscriptions()
        except Exception as ex:
            logger.warning("дайджест: не удалось прочитать подписки: %s", ex)
            return

        if not subs:
            logger.info("дайджест: нет активных подписок")
            return

        logger.info("дайджест: %d подписчик(ов)", len(subs))
        for user_id, tags in subs:
            try:
                parts = ["<b>Дайджест за сутки</b>"]
                found_any = False
                for tag in tags[:3]:
                    result = await run_monitoring(tag, [tag], mode="query")
                    items = result.get("items", [])
                    if not items:
                        parts.append(f"\n{tag}: упоминаний нет")
                        continue
                    found_any = True
                    stats = result["stats"]
                    sent = stats["sentiment"]
                    parts.append(
                        f"\n{tag}: {len(items)} упоминаний\n"
                        f"позитив {sent.get('positive', 0)} / "
                        f"нейтрально {sent.get('neutral', 0)} / "
                        f"негатив {sent.get('negative', 0)}\n"
                        f"топ: {items[0].get('url', '')}"
                    )
                text = "\n".join(parts)[:4000]
                await self.bot.send_message(user_id, text, parse_mode="HTML",
                                            disable_web_page_preview=True)
                if not found_any:
                    logger.info("дайджест для %s: пусто", user_id)
            except Exception as ex:
                # пользователь мог заблокировать бота — идём дальше
                logger.warning("дайджест для %s не отправлен: %s", user_id, ex)

    async def check_watches(self):
        """Проверка слежений каждые 20 минут: новые посты → сообщение."""
        from .db import repo
        from .tasks import collect_account

        try:
            watches = await repo.get_active_watches()
        except Exception as ex:
            logger.warning("watch: не удалось прочитать слежения: %s", ex)
            return
        if not watches:
            return

        # один запрос — одна проверка, независимо от числа наблюдателей
        by_query: dict[str, list] = {}
        for w in watches:
            by_query.setdefault(w.query.lower(), []).append(w)

        for query, group in by_query.items():
            try:
                items = await collect_account(group[0].query, limit=10)
            except Exception as ex:
                logger.debug("watch %s: %s", query, ex)
                continue

            from datetime import datetime, timezone
            checked = group[0].last_checked
            checked_iso = checked.replace(tzinfo=timezone.utc).isoformat() if checked else ""
            fresh = [it for it in items
                     if it.get("posted_at") and it["posted_at"] > checked_iso]
            if not fresh:
                continue

            lines = [f"<b>Новое по {group[0].query}:</b>", ""]
            for it in fresh[:5]:
                head = (it["text"] or "").split("\n")[0][:80]
                lines.append(f'- [{it["platform"]}] <a href="{it["url"]}">{_esc(head)}</a>')
            if len(fresh) > 5:
                lines.append(f"… и ещё {len(fresh) - 5}")

            for w in group:
                try:
                    await self.bot.send_message(w.user_id, "\n".join(lines),
                                                parse_mode="HTML",
                                                disable_web_page_preview=True)
                except Exception as ex:
                    logger.warning("watch %s: не отправлено: %s", w.user_id, ex)
                try:
                    await repo.touch_watch(w.id)
                except Exception as ex:
                    logger.warning("touch_watch %s: %s", w.id, ex)


__all__ = ["DigestScheduler"]
