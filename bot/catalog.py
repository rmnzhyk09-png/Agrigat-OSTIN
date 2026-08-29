"""Каталог OSINT-ботов из catalog.json."""
import json
from pathlib import Path
from typing import Optional

# Путь к catalog.json — рядом с этим файлом или в корне
CATALOG_FILE = Path(__file__).parent.parent / "catalog.json"


class Catalog:
    """Работа с каталогом OSINT-ботов."""

    def __init__(self, path: str | None = None):
        catalog_path = Path(path) if path else CATALOG_FILE
        if not catalog_path.exists():
            catalog_path = Path(__file__).parent.parent.parent / "catalog.json"
        
        with open(catalog_path, "r", encoding="utf-8") as f:
            self._data = json.load(f)
        
        self._groups = {g["id"]: g for g in self._data.get("groups", [])}
        self._price_labels = {
            "free": "Бесплатно",
            "freemium": "Есть бесплатная версия",
            "paid": "Платно",
            "unknown": "—"
        }

    def groups(self) -> list[dict]:
        """Список групп."""
        return list(self._data.get("groups", []))

    def bots(self, group_id: str | None = None) -> list[dict]:
        """Боты в группе или все."""
        if group_id is None:
            return list(self._data.get("bots", []))
        return [b for b in self._data.get("bots", []) if self._group_of(b) == group_id]

    def get(self, bot_id: str) -> Optional[dict]:
        """Найти бота по ID."""
        for b in self._data.get("bots", []):
            if b.get("id") == bot_id:
                return b
        return None

    def _group_of(self, bot: dict) -> str:
        """Определить группу бота."""
        main = bot.get("main", "")
        for g in self._data.get("groups", []):
            if main in g.get("members", []):
                return g["id"]
        # Поиск по категориям
        best, best_n = "", 0
        for g in self._data.get("groups", []):
            n = len(set(bot.get("categories", [])) & set(g.get("members", [])))
            if n > best_n:
                best, best_n = g["id"], n
        return best or self._data.get("groups", [{}])[0].get("id", "other")

    def group_name(self, group_id: str) -> str:
        """Имя группы."""
        return self._groups.get(group_id, {}).get("name", group_id)

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Поиск ботов."""
        q = query.strip().lower()
        if not q:
            return []
        results = []
        for b in self._data.get("bots", []):
            hay = " ".join([
                b.get("name", "").lower(),
                b.get("url", "").lower(),
                " ".join(b.get("aliases", [])).lower(),
                b.get("searches", "").lower(),
                " ".join(b.get("categories", [])).lower(),
                self.group_name(self._group_of(b)).lower(),
            ])
            if q in hay:
                results.append(b)
                if len(results) >= limit:
                    break
        return results

    def format_bot(self, bot: dict) -> str:
        """HTML-текст карточки бота."""
        lines = [
            f"<b>{self._esc(bot.get('name', '—'))}</b>",
            f"Бот: {bot.get('url', '#')}",
            f"Поиск: {self._esc(bot.get('searches', '—'))[:200]}",
            f"Цена: {self._price_labels.get(bot.get('price', 'unknown'), '—')}",
            f"Группа: {self._esc(self.group_name(self._group_of(bot)))}",
        ]
        if bot.get("has_api"):
            docs = bot.get("api_docs")
            lines.append(f"API: {'да' if not docs else self._esc(docs)}")
        if bot.get("status") == "closed":
            lines.append("Статус: закрыт")
        return "\n".join(lines)

    def format_group(self, group_id: str) -> str:
        """Текст группы с ботами."""
        g = self._groups.get(group_id)
        if not g:
            return "Группа не найдена"
        bots = self.bots(group_id)
        items = [
            f"{i}. <a href=\"{b['url']}\">{self._esc(b['name'])}</a>"
            f" — {self._price_labels.get(b.get('price', 'unknown'), '—')}"
            for i, b in enumerate(bots, 1)
        ]
        return f"<b>{self._esc(g['name'])}</b> ({len(bots)})\n\n" + "\n".join(items)

    def format_groups_list(self) -> str:
        """Список групп."""
        lines = ["<b>Группы каталога:</b>"]
        for g in self._data.get("groups", []):
            count = len(self.bots(g["id"]))
            lines.append(f"- <code>{g['id']}</code> — {self._esc(g['name'])} ({count})")
        return "\n".join(lines)

    def _esc(self, text: str) -> str:
        """Экранирование HTML."""
        if not text:
            return "—"
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
