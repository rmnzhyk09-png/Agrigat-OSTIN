"""Анализ тональности текста."""
from typing import Optional


class SentimentAnalyzer:
    """Простой анализ тональности на основе лексикона (RU/EN)."""

    def __init__(self):
        self._positive_words = {
            "хорошо", "отлично", "замечательно", "супер", "красота", "удача", "победа",
            "счастье", "любовь", "приятно", "классно", "awesome", "great", "good",
            "wonderful", "amazing", "love", "happy", "nice", "perfect", "best",
        }
        self._negative_words = {
            "плохо", "ужасно", "отвратительно", "страшно", "грустно", "горе", "беда",
            "несчастье", "ненавижу", "досадно", "terrible", "bad", "awful", "sad",
            "hate", "worst", "pain", "痛苦", "horrible", "disgusting",
        }

    def analyze(self, text: str) -> str:
        """
        Определить тональность.
        
        Returns:
            "positive", "negative" или "neutral"
        """
        if not text:
            return "neutral"
        
        text_lower = text.lower()
        
        positive_count = sum(1 for w in self._positive_words if w in text_lower)
        negative_count = sum(1 for w in self._negative_words if w in text_lower)
        
        # Если нейтральный текст
        if positive_count == 0 and negative_count == 0:
            return "neutral"
        
        # Если положительных больше
        if positive_count > negative_count * 1.5:
            return "positive"
        
        # Если отрицательных больше
        if negative_count > positive_count * 1.5:
            return "negative"
        
        # Иначе нейтрально
        return "neutral"


def analyze_sentiment(items: list[dict]) -> list[dict]:
    """Анализ тональности для списка постов."""
    analyzer = SentimentAnalyzer()
    
    for item in items:
        item["sentiment"] = analyzer.analyze(item.get("text", ""))
    
    return items
