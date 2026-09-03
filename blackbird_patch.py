"""Build-time helper: применяет к свежесклонированному Blackbird фильтр
заведомо недоступных доменов (Instagram/YouTube/files.fm/t.me и т.д.), чтобы
провайдер не пытался искать по заблокированным сетям.

Вызывается из Dockerfile после git clone Blackbird. Идемпотентно:
повторный запуск ничего не ломает. Не используется в рантайме бота.
"""
import pathlib
import sys

if len(sys.argv) > 1:
    TARGET = str(pathlib.Path(sys.argv[1]))
else:
    TARGET = "/app/blackbird/src/modules/utils/filter.py"
BLOCKED_HOSTS = (
    "instagram.com", "youtube.com", "youtu.be", "files.fm",
    "t.me", "telegram.org", "threads.net", "tiktok.com",
)

FILTER_BLOCKED = (
    "def filterBlocked(site):\n"
    "    uri = (site.get(\"uri_check\") or site.get(\"uri\") or \"\").lower()\n"
    "    name = (site.get(\"name\") or \"\").lower()\n"
    "    return not any(h in uri or h in name for h in BLOCKED_HOSTS)\n"
    "\n"
)


def main() -> int:
    path = pathlib.Path(TARGET)
    if not path.is_file():
        print("WARN: нет %s — патч пропущен" % TARGET)
        return 0
    src = path.read_text(encoding="utf-8")

    marker = "def applyFilters(sitesToSearch, config):"
    if marker not in src:
        print("WARN: якорь applyFilters не найден — патч пропущен")
        return 0
    # Идемпотентность: уже патчено — не трогаем.
    if "filterBlocked" in src:
        print("OK: Blackbird уже пропатчен")
        return 0

    anchor_line = src.find(marker)
    # Вставляем блоки перед applyFilters, а внутри — first-line filter.
    head = src[:anchor_line]
    body = src[anchor_line:]
    new_apply = (
        "BLOCKED_HOSTS = " + repr(BLOCKED_HOSTS) + "\n"
        "\n"
        + FILTER_BLOCKED
        + marker
        + "\n"
        + "    sitesToSearch = list(filter(filterBlocked, sitesToSearch))\n"
    )
    path.write_text(head + new_apply + body, encoding="utf-8")
    print("OK: Blackbird пропатчен (%d доментов)" % len(BLOCKED_HOSTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())