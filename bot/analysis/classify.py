"""Классификация постов по тегам."""
import re
from typing import Optional


class KeywordClassifier:
    """Классификация по ключевым словам (работает без API)."""

    def __init__(self):
        # Примерные словоформы для тегов (русские окончания)
        self._keywords = {
            "криптовалюта": ["биткоин", "бтк", "btc", "эфир", "eth", "крипта", "коин", "токен", "wallet", "blockchain"],
            "политика": ["политика", "паша", "путин", "зеленский", "байден", "конгресс", "дuma", "госдума"],
            "ищу работу": ["вакансия", "работа", "hr", "соискатель", "резюме", "самообучение", "it-специалист"],
            "технологии": ["tech", "it", "программирование", "код", "software", "dev", "разработчик", "python", "js"],
            "новости": ["новость", "событие", "новости", "пресса", "медиа", "журналистика"],
        }

    def classify(self, text: str, tags: list[str]) -> dict[str, bool]:
        """Определить, к каким тегам относится текст."""
        text_lower = text.lower()
        result = {}
        
        for tag in tags:
            tag_lower = tag.lower()
            keywords = self._keywords.get(tag_lower, [])
            
            # Прямое вхождение тега
            if tag_lower in text_lower:
                result[tag] = True
                continue
            
            # Вхождение словоформ
            for kw in keywords:
                if kw in text_lower:
                    result[tag] = True
                    break
            else:
                result[tag] = False
        
        return result


def classify_items(items: list[dict], tags: list[str]) -> list[dict]:
    """Классифицировать список постов."""
    classifier = KeywordClassifier()
    
    for item in items:
        classification = classifier.classify(item.get("text", ""), tags)
        item["tags"] = [tag for tag, match in classification.items() if match]
    
    return items
