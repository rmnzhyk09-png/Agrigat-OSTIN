"""График-сводка мониторинга (matplotlib, Agg) — PNG в памяти."""
import io
import logging

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)

PLATFORM_NAMES = {
    "github": "GitHub", "reddit": "Reddit", "bluesky": "Bluesky",
    "mastodon": "Mastodon", "hackernews": "HackerNews", "vk": "VK",
    "x": "X/Twitter", "youtube": "YouTube", "rss": "RSS", "web": "Веб",
    "google": "Google", "serpapi": "SerpAPI", "brave": "Brave",
    "db": "База импорта", "profile": "Профиль",
}

SENT_COLORS = {"positive": "#2ca02c", "neutral": "#bbbbbb", "negative": "#d62728"}
SENT_NAMES = {"positive": "Позитив", "neutral": "Нейтрально", "negative": "Негатив"}


def generate_chart_png(items: list[dict], stats: dict) -> bytes | None:
    """Сводный график: платформы + тональность + теги. PNG-байты или None."""
    try:
        fig, axes = plt.subplots(1, 3, figsize=(12, 3.8), dpi=110)
        fig.patch.set_facecolor("white")

        # 1. По платформам
        ax = axes[0]
        by_platform = stats.get("by_platform", {})
        platforms = list(by_platform)[:8]
        values = [by_platform[p] for p in platforms]
        labels = [PLATFORM_NAMES.get(p, p)[:14] for p in platforms]
        ax.barh(labels[::-1], values[::-1], color="#1f77b4")
        ax.set_title("По платформам", fontsize=10)
        ax.tick_params(labelsize=8)
        for i, v in enumerate(values[::-1]):
            ax.text(v, i, f" {v}", va="center", fontsize=8)

        # 2. Тональность
        ax = axes[1]
        sent = stats.get("sentiment", {})
        parts = [(SENT_NAMES.get(k, k), sent.get(k, 0)) for k in ("positive", "neutral", "negative")]
        non_zero = [(n, v) for n, v in parts if v > 0]
        if non_zero:
            ax.pie([v for _, v in non_zero],
                   labels=[n for n, _ in non_zero],
                   colors=[SENT_COLORS.get(k, "#999") for k in ("positive", "neutral", "negative")
                           if sent.get(k, 0) > 0],
                   autopct=lambda p: f"{p:.0f}%", textprops={"fontsize": 8},
                   startangle=90)
        ax.set_title("Тональность", fontsize=10)

        # 3. По тегам
        ax = axes[2]
        by_tag = stats.get("by_tag", {})
        tags = list(by_tag)[:6]
        if tags:
            ax.bar([t[:16] for t in tags], [by_tag[t] for t in tags], color="#9467bd")
            ax.set_title("По тегам", fontsize=10)
            ax.tick_params(labelsize=7, rotation=15)
        else:
            ax.text(0.5, 0.5, "нет тегов", ha="center", va="center", fontsize=9)
            ax.set_xticks([])
            ax.set_yticks([])

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception as ex:
        logger.warning("график не построен: %s", ex)
        return None
