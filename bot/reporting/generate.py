"""Генерация файлов отчётов."""
import json
import csv
import io
from typing import Optional


def generate_json(items: list[dict]) -> str:
    """Сгенерировать JSON."""
    return json.dumps(items, indent=2, ensure_ascii=False)


def generate_csv(items: list[dict]) -> str:
    """Сгенерировать CSV."""
    if not items:
        return ""
    
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["platform", "post_id", "author", "text", "url", "tags", "sentiment"],
        extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(items)
    
    return output.getvalue()


def generate_markdown(items: list[dict]) -> str:
    """Сгенерировать Markdown."""
    if not items:
        return "# Отчёт\n\nНичего не найдено."
    
    lines = ["# Отчёт по мониторингу\n", f"**Всего постов:** {len(items)}\n"]
    
    for item in items:
        platform = item.get("platform", "unknown")
        author = item.get("author", "unknown")
        text = item.get("text", "")[:200]
        url = item.get("url", "#")
        tags = ", ".join(item.get("tags", [])) or "—"
        sentiment = item.get("sentiment", "neutral")
        
        emoji = {"positive": "😊", "neutral": "😐", "negative": "😞"}.get(sentiment, "😐")
        
        lines.append(f"---\n")
        lines.append(f"**[{platform}]** [{author}]({url}) {emoji}\n")
        lines.append(f"**Теги:** {tags}\n")
        lines.append(f"{text[:200]}...")
    
    return "\n".join(lines)
