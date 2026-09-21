"""Методы поиска каналов (раздел 3 ТЗ).

Каждый метод принимает gateway + параметры и отдаёт список Candidate.
Сбор метрик и фильтрация происходят уже в конвейере (`tghunter.pipeline`).
"""

from .folders import folder_candidates
from .keyword import keyword_candidates
from .manual import manual_candidates
from .mentions import candidates_from_posts, mention_candidates
from .similar import similar_candidates

__all__ = [
    "keyword_candidates",
    "similar_candidates",
    "mention_candidates",
    "candidates_from_posts",
    "folder_candidates",
    "manual_candidates",
]
