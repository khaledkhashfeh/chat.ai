"""app/conversation/entities.py

[4] مستخرج الكيانات — قواميس وأنماط regex فقط (بدون أي نموذج لغوي)، وفق
docs/spec.md §3-[4]. يستقبل نصًا **مطبَّعًا مسبقًا** ويرجع dict بالحقول التي
اكتُشفت فعلًا فقط (لا يكتب حقلًا لم يُذكر — الدمج بحالة المحادثة في dialogue.py
يحافظ على قاعدة الوراثة: ما لم يُذكر لا يُمحى).
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
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
        "اصدقاء", "اصدقائي", "صديق", "صديقي", "صديقتي", "صديقاتي", "صاحبي", "صاحبتي", "friend",
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

# دافع الرحلة — حقل مستقل عن group_type/interests (docs/contract.md §1.1)، يوصف
# سبب الرحلة لا تركيبة المجموعة ولا نوع الأماكن. تداخل طبيعي مع كلمات أخرى مقبول
# (مثلًا «مغامره» تُلحق tag:adventure و trip_purpose=adventure معًا بنفس الرسالة).
TRIP_PURPOSE: dict[str, list[str]] = {
    "leisure": ["استجمام", "استرخاء", "استرخا", "راحه بس", "ترفيه", "relaxation", "leisure", "chill trip"],
    "adventure": ["مغامره", "مغامرات", "اثاره", "adventure trip", "thrill"],
    "cultural": ["ثقافي", "ثقافيه", "تراث", "تراثي", "cultural trip", "heritage trip"],
    "family_fun": ["متعه عائليه", "نشاط عائلي", "وقت ممتع للعيله", "family fun trip"],
    "romantic": ["رومانسي", "رومنسي", "شهر عسل", "شهر العسل", "romantic trip", "honeymoon"],
}

TRANSPORT_MODE: dict[str, list[str]] = {
    "car": ["بسيارتي", "بالسياره", "عندي سياره", "سياره خاصه", "سياره مستأجره", "my car", "by car", "private car", "rental car"],
    "public_transport": ["مواصلات عامه", "الباص", "بالباص", "قطار", "public transport", "bus", "train"],
    "walking": ["ماشي", "عالماشي", "مشي بس", "walking", "on foot"],
    "mixed": ["مواصلات متنوعه", "مختلطه", "mixed transport"],
}

PREFERRED_TIME: dict[str, list[str]] = {
    "morning": ["صباحي", "الصبح", "بكير", "morning routine", "morning"],
    "afternoon": ["بعد الظهر", "عصرا", "afternoon"],
    "evening": ["مسائي", "بالليل", "مساء", "evening", "night owl"],
}

# «بعد ...» يعني تاريخ بدء نسبي (find_start_date أدناه) لا مدة رحلة — نستثنيه
# هنا كي لا يبتلع «بعد 5 ايام» مدةَ الرحلة سهوًا (ويكتب فوق duration_days الصحيحة).
_DURATION_NUM_PATTERN = re.compile(r"(?<!بعد )(\d+)\s*(?:يوم|ايام|day|days)")
_DURATION_WORDS: dict[str, int] = {
    "اسبوعين": 14,
    "تلات ايام": 3, "ثلاث ايام": 3, "ثلاثه ايام": 3, "تلاته ايام": 3,
    "اربعه ايام": 4, "اربع ايام": 4,
    "خمسه ايام": 5, "خمس ايام": 5,
    "يوم واحد": 1, "يوميين": 2, "يومين": 2,
    "اسبوع": 7, "week": 7,
}
_DURATION_WORDS_BY_LENGTH = sorted(_DURATION_WORDS.keys(), key=len, reverse=True)

# تاريخ بدء الرحلة — نسبي أو صريح (بلا مكتبات تواريخ خارجية، حساب يدوي بالتقويم القياسي).
_TOMORROW_WORDS = ["بكرا", "بكره", "غدا", "غدًا", "tomorrow"]
_NEXT_WEEK_WORDS = ["الاسبوع القادم", "الاسبوع الجاي", "next week"]
_NEXT_MONTH_WORDS = ["الشهر القادم", "الشهر الجاي", "next month"]
_AFTER_ONE_WEEK_WORDS = ["بعد اسبوع", "after a week", "after one week"]  # مفرد ضمني = اسبوع واحد
_AFTER_TWO_WEEKS_WORDS = ["بعد اسبوعين", "after two weeks"]

# أرقام مكتوبة (لـ«بعد خمس ايام» لا فقط «بعد 5 ايام») — نفس نطاق duration أعلاه
_NUMBER_WORDS: dict[str, int] = {
    "واحد": 1, "اثنين": 2, "تلاته": 3, "ثلاثه": 3, "اربعه": 4, "خمسه": 5, "خمس": 5,
    "سته": 6, "سبعه": 7, "تمانيه": 8, "ثمانيه": 8, "تسعه": 9, "عشره": 10,
}
_NUMBER_WORD_ALT = "|".join(sorted(_NUMBER_WORDS.keys(), key=len, reverse=True))
_AFTER_N_DAYS_PATTERN = re.compile(r"بعد\s*(\d+|" + _NUMBER_WORD_ALT + r")\s*(?:يوم|ايام|day|days)")
_AFTER_N_WEEKS_PATTERN = re.compile(r"بعد\s*(\d+|" + _NUMBER_WORD_ALT + r")\s*(?:اسبوع|اسابيع|week|weeks)")


def _to_int(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBER_WORDS[token]

# أسماء الأشهر: الميلادية المتداولة + الشامية/السورية الشائعة + الإنكليزية —
# المفاتيح بصيغتها بعد normalize() (أ/إ/آ→ا).
_MONTHS: dict[str, int] = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "ابريل": 4, "مايو": 5, "يونيو": 6,
    "يوليو": 7, "اغسطس": 8, "سبتمبر": 9, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    "كانون الثاني": 1, "شباط": 2, "اذار": 3, "نيسان": 4, "ايار": 5, "حزيران": 6,
    "تموز": 7, "اب": 8, "ايلول": 9, "تشرين الاول": 10, "تشرين الثاني": 11, "كانون الاول": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_KEYS_BY_LENGTH = sorted(_MONTHS.keys(), key=len, reverse=True)
_EXPLICIT_DATE_PATTERN = re.compile(
    r"(\d{1,2})\s*(?:من\s*)?(" + "|".join(re.escape(k) for k in _MONTH_KEYS_BY_LENGTH) + r")"
)


def _add_months(d: date, months: int) -> date:
    """يضيف أشهرًا لتاريخ مع معالجة فيض الأيام (31 كانون الثاني + شهر → 28/29 شباط)."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def find_start_date(text_norm: str, today: Optional[date] = None) -> Optional[date]:
    """تاريخ بدء الرحلة من نص مطبَّع مسبقًا — يدعم النسبي (غدا/الاسبوع القادم/
    الشهر القادم/بعد N يوم أو اسبوع) والصريح (20 اكتوبر). يرجع None إن لم يُذكر
    أي تاريخ (الحقل اختياري تمامًا — لا يُسأل عنه إن لم يذكره المستخدم)."""
    today = today or date.today()

    match = _EXPLICIT_DATE_PATTERN.search(text_norm)
    if match:
        day, month = int(match.group(1)), _MONTHS[match.group(2)]
        try:
            year = today.year
            candidate = date(year, month, day)
            # لو التاريخ الصريح مضى هالسنة، الأرجح إنه يقصد نفس التاريخ بالسنة الجاية
            if candidate < today:
                candidate = date(year + 1, month, day)
            return candidate
        except ValueError:
            return None  # يوم غير صالح لهالشهر (مثلًا 31 شباط) — نتجاهله بدل الانهيار

    days_match = _AFTER_N_DAYS_PATTERN.search(text_norm)
    if days_match:
        return today + timedelta(days=_to_int(days_match.group(1)))

    if any(w in text_norm for w in _AFTER_TWO_WEEKS_WORDS):
        return today + timedelta(weeks=2)

    weeks_match = _AFTER_N_WEEKS_PATTERN.search(text_norm)
    if weeks_match:
        return today + timedelta(weeks=_to_int(weeks_match.group(1)))

    # مفرد ضمني («بعد اسبوع» بلا رقم = اسبوع واحد) — يُفحص أخيرًا بين حالات
    # الأسبوع كي لا يسبق «بعد اسبوعين»/«بعد N اسابيع» الأكثر تحديدًا
    if any(w in text_norm for w in _AFTER_ONE_WEEK_WORDS):
        return today + timedelta(weeks=1)

    if any(w in text_norm for w in _NEXT_MONTH_WORDS):
        return _add_months(today, 1)

    if any(w in text_norm for w in _NEXT_WEEK_WORDS):
        return today + timedelta(weeks=1)

    if any(w in text_norm for w in _TOMORROW_WORDS):
        return today + timedelta(days=1)

    return None


def _find_cities(text_norm: str) -> list[str]:
    """يرجع **كل** المدن المذكورة بالنص (لا أول واحدة فقط) — يدعم «دمشق وحلب»
    كوجهتين معًا. الترتيب حسب ورود اسم المدينة الأول بالنص."""
    found: list[tuple[int, str]] = []
    for city, keywords in CITIES.items():
        idx = min((text_norm.find(kw) for kw in keywords if kw in text_norm), default=-1)
        if idx != -1:
            found.append((idx, city))
    found.sort(key=lambda pair: pair[0])
    return [city for _, city in found]


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


def _find_trip_purpose(text_norm: str) -> Optional[str]:
    for purpose, keywords in TRIP_PURPOSE.items():
        if any(kw in text_norm for kw in keywords):
            return purpose
    return None


def _find_transport_mode(text_norm: str) -> Optional[str]:
    for mode, keywords in TRANSPORT_MODE.items():
        if any(kw in text_norm for kw in keywords):
            return mode
    return None


def _find_preferred_time(text_norm: str) -> Optional[str]:
    for time_of_day, keywords in PREFERRED_TIME.items():
        if any(kw in text_norm for kw in keywords):
            return time_of_day
    return None


def _find_duration_days(text_norm: str) -> Optional[int]:
    match = _DURATION_NUM_PATTERN.search(text_norm)
    if match:
        return int(match.group(1))
    for phrase in _DURATION_WORDS_BY_LENGTH:
        # نفس استثناء «بعد» أعلاه: «بعد اسبوعين» تاريخ بدء نسبي لا مدة رحلة 14 يوم
        if phrase in text_norm and f"بعد {phrase}" not in text_norm:
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
    مدينة واحدة أو أكثر — «دمشق وحلب»)، duration_days، group_type، budget_level،
    interests (قائمة واحدة أو أكثر أصلًا)، trip_purpose، transport_mode،
    preferred_time. كل كاشف مستقل ويُشغَّل دائمًا — رسالة واحدة قد تحمل عدة
    حقول معًا («اريد الذهاب لدمشق مع اصدقائي» = destination + group_type)."""
    entities: dict = {}

    cities = _find_cities(text_norm)
    if cities:
        entities["destination"] = cities

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

    purpose = _find_trip_purpose(text_norm)
    if purpose:
        entities["trip_purpose"] = purpose

    transport = _find_transport_mode(text_norm)
    if transport:
        entities["transport_mode"] = transport

    preferred_time = _find_preferred_time(text_norm)
    if preferred_time:
        entities["preferred_time"] = preferred_time

    return entities
