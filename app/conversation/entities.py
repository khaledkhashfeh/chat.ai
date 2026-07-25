"""app/conversation/entities.py

[4] مستخرج الكيانات — قواميس وأنماط regex فقط (بدون أي نموذج لغوي)، وفق
docs/spec.md §3-[4]. يستقبل نصًا **مطبَّعًا مسبقًا** ويرجع dict بالحقول التي
اكتُشفت فعلًا فقط (لا يكتب حقلًا لم يُذكر — الدمج بحالة المحادثة في dialogue.py
يحافظ على قاعدة الوراثة: ما لم يُذكر لا يُمحى).
"""
from __future__ import annotations

import re
from typing import Optional

from app.shared.models import TAG_VOCAB

CITIES: dict[str, list[str]] = {
    "دمشق": ["دمشق", "الشام", "damascus"],
    "حلب": ["حلب", "aleppo"],
    "اللاذقية": ["اللاذقيه", "الاذقيه", "لاذقيه", "latakia", "lattakia"],
    "طرطوس": ["طرطوس", "tartus", "tartous"],
    "حمص": ["حمص", "homs"],
    "حماة": ["حماه", "hama"],
    "السويداء": ["السويداء", "السويدا", "سويداء", "سويدا", "sweida", "suwayda"],
    "دير الزور": ["دير الزور", "deir ezzor", "deir al-zor"],
    "درعا": ["درعا", "daraa"],
    "بصرى": ["بصري", "bosra"],  # keyword مطبَّع (ى→ي) كي يطابق النص بعد normalize
    "تدمر": ["تدمر", "palmyra"],
    "معلولا": ["معلولا", "maaloula", "maalula"],
}

GROUPS: dict[str, list[str]] = {
    "family": ["عيله", "عيلت", "عائله", "عائلت", "اطفال", "ولاد", "طفل", "family", "kids"],
    "friends": [
        "اصحاب", "شباب", "رفقات", "صحابي", "friends",
        "رحله تخرج", "رحله التخرج", "تخرج", "graduation trip", "graduation",
    ],
    "couple": [
        "زوجتي", "خطيبتي", "مرتي", "جوزي", "خطيبي", "زوجي", "wife", "husband", "couple", "honeymoon",
        "شهر عسل", "شهر العسل", "ذكرى زواج", "ذكرى الزواج", "anniversary",
    ],
    "solo": ["لحالي", "وحدي", "solo", "alone"],
    "large_group": ["رحله جماعيه", "مجموعه كبيره", "large group", "group trip"],
}

BUDGET: dict[str, list[str]] = {
    "low": ["اقتصادي", "رخيص", "على قد الحال", "عقد الحال", "ميزانيه محدوده", "قليل المصاريف", "cheap", "budget"],
    "medium": ["متوسط", "وسط", "معقول", "متوسطه", "medium", "moderate", "mid"],
    "high": ["فخم", "فاخر", "غالي", "luxury", "fancy", "expensive"],
}

INTEREST_TAGS: dict[str, str] = {
    "تاريخيه": "tag:historical", "تاريخي": "tag:historical", "اثار": "tag:historical",
    "اثري": "tag:historical", "اثريه": "tag:historical", "تراثي": "tag:historical",
    "historical": "tag:historical", "archaeological": "tag:historical",
    "دينيه": "tag:religious", "ديني": "tag:religious", "religious": "tag:religious",
    "طبيعيه": "tag:nature", "طبيعي": "tag:nature", "طبيعه": "tag:nature", "nature": "tag:nature",
    "بحر": "tag:sea", "شاطئ": "tag:sea", "sea": "tag:sea", "beach": "tag:sea",
    "سوق": "tag:market", "تسوق": "tag:market", "market": "tag:market", "shopping": "tag:market",
    "مطاعم": "tag:food", "اكل": "tag:food", "طعام": "tag:food", "food": "tag:food", "restaurants": "tag:food",
    "اطفال": "tag:family_fun", "family fun": "tag:family_fun",
    "مغامرات": "tag:adventure", "مغامره": "tag:adventure", "adventure": "tag:adventure",
    "هادئه": "tag:quiet", "هادئ": "tag:quiet", "هدوء": "tag:quiet", "quiet": "tag:quiet",
    "متاحف": "tag:museum", "متحف": "tag:museum", "museum": "tag:museum",
    # هروب من الطقس: يُطوى على الوسمين الموجودين، لا وسم جديد (tag:nature=بارد/جبلي،
    # tag:sea=دافئ/شاطئي) — القرار موثّق بـ docs/spec.md §3-[4].
    "هروب من الحر": "tag:nature", "هربان من الحر": "tag:nature", "بعيد عن الحر": "tag:nature",
    "جو بارد": "tag:nature", "طقس بارد": "tag:nature", "مكان بارد": "tag:nature",
    "escape the heat": "tag:nature", "cool weather": "tag:nature", "cold weather": "tag:nature",
    "دافئ": "tag:sea", "دفا": "tag:sea", "شمس": "tag:sea",
    "warm weather": "tag:sea", "sunny": "tag:sea",
}

_DURATION_NUM_PATTERN = re.compile(r"(\d+)\s*(?:يوم|ايام|day|days)")
_DURATION_WORDS: dict[str, int] = {
    "اسبوعين": 14,
    "تلات ايام": 3, "ثلاث ايام": 3, "ثلاثه ايام": 3, "تلاته ايام": 3,
    "اربعه ايام": 4, "اربع ايام": 4,
    "خمسه ايام": 5, "خمس ايام": 5,
    "يوم واحد": 1, "يوميين": 2, "يومين": 2,
    "اسبوع": 7, "week": 7,
}
_DURATION_WORDS_BY_LENGTH = sorted(_DURATION_WORDS.keys(), key=len, reverse=True)


def _find_city(text_norm: str) -> Optional[str]:
    for city, keywords in CITIES.items():
        if any(kw in text_norm for kw in keywords):
            return city
    return None


def _find_group_type(text_norm: str) -> Optional[str]:
    for group, keywords in GROUPS.items():
        if any(kw in text_norm for kw in keywords):
            return group
    return None


def _find_budget(text_norm: str) -> Optional[str]:
    for level, keywords in BUDGET.items():
        if any(kw in text_norm for kw in keywords):
            return level
    return None


def _find_duration_days(text_norm: str) -> Optional[int]:
    match = _DURATION_NUM_PATTERN.search(text_norm)
    if match:
        return int(match.group(1))
    for phrase in _DURATION_WORDS_BY_LENGTH:
        if phrase in text_norm:
            return _DURATION_WORDS[phrase]
    return None


def _find_interests(text_norm: str) -> list[str]:
    tags: list[str] = []
    for keyword, tag in INTEREST_TAGS.items():
        if keyword in text_norm and tag not in tags:
            tags.append(tag)
    return [t for t in tags if t in TAG_VOCAB]


def extract_entities(text_norm: str) -> dict:
    """يرجع dict بالحقول المكتشفة فقط من نص مطبَّع مسبقًا: destination (قائمة
    مدينة واحدة)، duration_days، group_type، budget_level، interests."""
    entities: dict = {}

    city = _find_city(text_norm)
    if city:
        entities["destination"] = [city]

    duration = _find_duration_days(text_norm)
    if duration is not None:
        entities["duration_days"] = duration

    group = _find_group_type(text_norm)
    if group:
        entities["group_type"] = group

    budget = _find_budget(text_norm)
    if budget:
        entities["budget_level"] = budget

    interests = _find_interests(text_norm)
    if interests:
        entities["interests"] = interests

    return entities
