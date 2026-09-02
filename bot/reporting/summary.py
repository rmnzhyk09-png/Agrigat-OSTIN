"""Генерация краткой сводки."""

PLATFORM_NAMES = {
    "github": "GitHub", "reddit": "Reddit", "bluesky": "Bluesky",
    "mastodon": "Mastodon", "hackernews": "HackerNews", "vk": "VK",
    "x": "X", "youtube": "YouTube", "rss": "RSS", "web": "Веб",
    "google": "Google", "serpapi": "SerpAPI", "brave": "Brave",
    "db": "База импорта",
    "profile": "Профиль человека",
    "blackbird": "Blackbird",
    "datatech": "DataTech",
}


def generate_summary(items: list[dict], tags: list[str]) -> str:
    """Сгенерировать краткую сводку для Telegram."""
    if not items:
        return "Ничего не найдено."

    total = len(items)
    by_platform = {}
    by_tag = {}
    sentiment_stats = {"positive": 0, "neutral": 0, "negative": 0}

    for item in items:
        platform = item.get("platform", "unknown")
        by_platform[platform] = by_platform.get(platform, 0) + 1
        for tag in item.get("tags", []):
            by_tag[tag] = by_tag.get(tag, 0) + 1
        sentiment = item.get("sentiment", "neutral")
        sentiment_stats[sentiment] = sentiment_stats.get(sentiment, 0) + 1

    lines = [
        "<b>Сводка</b>",
        "",
        f"Найдено: {total}",
        "",
        "<b>Платформы:</b>",
    ]
    for platform, count in sorted(by_platform.items(), key=lambda x: -x[1])[:5]:
        name = PLATFORM_NAMES.get(platform, platform)
        lines.append(f"- {name}: {count}")

    if by_tag:
        lines.append("")
        lines.append("<b>Теги:</b>")
        for tag, count in sorted(by_tag.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"- {tag}: {count}")

    lines += [
        "",
        "<b>Тональность:</b>",
        f"- позитив: {sentiment_stats['positive']}",
        f"- нейтрально: {sentiment_stats['neutral']}",
        f"- негатив: {sentiment_stats['negative']}",
    ]

    return "\n".join(lines)
