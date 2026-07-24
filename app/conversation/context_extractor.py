"""app/conversation/context_extractor.py

مستخرج عقد conversation_context_v1 (طبقة المحادثة → Laravel).

يعيد استخدام خط المعالجة نفسه (تطبيع → تصنيف نية → استخراج كيانات) لكن بدل
استدعاء المحركين الوهميين وإرجاع بطاقات، يبني **فهمًا منظمًا** لرسالة المستخدم:
النية (من نواياك الست)، قيود البحث، التفضيلات الموزونة، سياق الرحلة، والاستبعادات،
مع جودة الفهم (confidence / requires_clarification / clarification_question).

قواعد ملزمة (CLAUDE.md + رسالة المالك):
- **بلا LLM/API خارجي** — كل شيء قواميس + المصنّف المدرَّب ذاتيًا.
- **بلا حالة** — دالة صرفة من الرسالة (Laravel يملك المستخدم وسلوكه وحالته).
- **بلا اختراع** — لا اسم مكان ولا تقييم ولا درجة توصية؛ فقط ما يُستخرج من الكلام.
- كل نص معروض (سؤال التوضيح) يأتي من responses.py حصرًا.
"""
from __future__ import annotations

import re
from typing import Optional

from app.config import settings
from app.conversation import entities as entities_mod
from app.conversation import responses
from app.conversation.intent import classify_raw
from app.conversation.normalizer import detect_language, normalize
from app.shared.models import (
    ContextExclusions,
    ContextFilters,
    ContextLocation,
    ConversationContextV1,
    TripContext,
)

# ---------------------------------------------------------------------------
# جداول التحويل — كلها بأشكال **مطبَّعة مسبقًا** (تطابق مخرجات normalize)
# ---------------------------------------------------------------------------

# تحويل النوايا التسع الداخلية → نواياك الست الخارجية
_INTENT_MAP: dict[str, str] = {
    "search": "recommend_places",  # قد تُصقل إلى search_places عند وجود فعل بحث صريح
    "details": "place_details",
    "compare": "general_question",
    "build_plan": "create_plan",
    "modify_plan": "modify_plan",
    "add_to_plan": "modify_plan",  # الإضافة للخطة = تعديل خطة
    "reject": "recommend_places",  # الرفض يعيد التوصية بمعايير جديدة
    "greeting_thanks": "general_question",
    "out_of_scope": "general_question",
}

# ألفاظ بحث صريحة تفرّق search_places عن recommend_places (ضمن نية search الداخلية)
_EXPLICIT_SEARCH_KW = ("دور", "دورلي", "ابحث", "بحث", "فتشلي", "لاقيلي", "search", "find", "look for", "looking for")

# مدينة عربية موحّدة (مخرج extract_entities) → (المحافظة بالإنكليزية، المدينة إن كانت بلدة داخل محافظة)
_GOVERNORATE_MAP: dict[str, tuple[str, Optional[str]]] = {
    "دمشق": ("Damascus", None),
    "حلب": ("Aleppo", None),
    "اللاذقية": ("Latakia", None),
    "طرطوس": ("Tartus", None),
    "حمص": ("Homs", None),
    "حماة": ("Hama", None),
    "السويداء": ("As-Suwayda", None),
    "دير الزور": ("Deir ez-Zor", None),
    "درعا": ("Daraa", None),
    "بصرى": ("Daraa", "Bosra"),
    "تدمر": ("Homs", "Palmyra"),
    "معلولا": ("Rif Dimashq", "Maaloula"),
}

# وسم "نوعي" → فئة (category) واحدة إلزامية. الوسوم اللينة (quiet/family_fun)
# ليست فئات — تذهب للتفضيلات فقط.
_TAG_TO_CATEGORY: dict[str, str] = {
    "tag:historical": "Historical",
    "tag:religious": "Religious",
    "tag:nature": "Nature",
    "tag:sea": "Sea",
    "tag:market": "Market",
    "tag:food": "Food",
    "tag:museum": "Museum",
    "tag:adventure": "Adventure",
}

# وسم → (مفتاح تفضيل، وزن). قاموس تفضيلاتك: history/nature/family/romantic/
# culture/food/shopping/photography/adventure/crowdedness.
_TAG_TO_PREF: dict[str, tuple[str, float]] = {
    "tag:historical": ("history", 1.0),
    "tag:museum": ("culture", 0.9),
    "tag:religious": ("culture", 0.8),
    "tag:nature": ("nature", 1.0),
    "tag:sea": ("nature", 0.8),
    "tag:market": ("shopping", 0.9),
    "tag:food": ("food", 1.0),
    "tag:family_fun": ("family", 0.9),
    "tag:adventure": ("adventure", 1.0),
    "tag:quiet": ("crowdedness", -0.7),
}
_GROUP_TO_PREF: dict[str, tuple[str, float]] = {
    "family": ("family", 0.8),
    "couple": ("romantic", 0.8),
}

# مستوى الميزانية (budget_tier) يُكشف مباشرةً من النص (مستقل عن budget_level
# منخفض/عالٍ في عقد المحركين) لأن عقدك يستعمل free/cheap/medium/expensive.
_BUDGET_TIER_KW: dict[str, tuple[str, ...]] = {
    "free": ("مجاني", "مجانيه", "ببلاش", "بلا مصاري", "مجانا", "free"),
    "cheap": ("رخيص", "اقتصادي", "على قد الحال", "عقد الحال", "قليل المصاريف", "cheap", "budget"),
    "medium": ("متوسط", "وسط", "معقول", "mid", "moderate"),
    "expensive": ("فخم", "فاخر", "غالي", "luxury", "fancy", "expensive"),
}

# إشارات تفضيل لا يغطّيها قاموس الوسوم
_PHOTOGRAPHY_KW = ("تصوير", "صور", "photography", "photo", "photos")
_ROMANTIC_KW = ("رومانسي", "رومنسي", "شهر العسل", "romantic", "honeymoon")
_CROWD_AVOID_KW = ("مو مزدحم", "بلا زحمه", "بعيد عن الزحمه", "not crowded", "less crowded", "avoid crowd")

_PREFERRED_TIME_KW: dict[str, tuple[str, ...]] = {
    "morning": ("صباح", "الصبح", "بكير", "morning"),
    "afternoon": ("بعد الظهر", "الظهر", "عصر", "afternoon", "noon"),
    "evening": ("مسا", "مساء", "عالمغرب", "غروب", "evening", "sunset"),
    "night": ("ليل", "بالليل", "سهره", "night", "nightlife"),
}
_SEASON_KW: dict[str, tuple[str, ...]] = {
    "spring": ("ربيع", "spring"),
    "summer": ("صيف", "الصيف", "summer"),
    "autumn": ("خريف", "autumn", "fall"),
    "winter": ("شتا", "شتاء", "الشتي", "winter"),
}
_ACCESSIBILITY_KW = ("كرسي متحرك", "كرسي مدولب", "wheelchair", "معاق", "اعاقه", "accessible", "accessibility")

# نفي فئة: "بدون/بلا/مو + كلمة اهتمام" → فئة مستبعَدة
_NEGATION_KW = ("بدون", "بلا", "مو", "ما بدي", "مب", "no ", "not ", "without", "avoid")

# ألفاظ طلب تُزال من مقدمة query_text (البحث الدلالي لا يحتاجها)
_REQUEST_PREFIXES = (
    "بدي", "بدنا", "اريد", "اقترحلي", "اقترح لي", "دورلي", "دور لي", "عطيني", "اعطيني",
    "وريني", "ورجيني", "لاقيلي", "بحب", "ممكن", "شو احلى", "شو في", "وين",
    "i want", "i'd like", "id like", "suggest", "show me", "find me", "recommend", "looking for", "can you",
)

_DURATION_HOURS_PATTERN = re.compile(r"(\d+)\s*(?:ساعه|ساعات|hour|hours|hr)")


def _has(text: str, keywords) -> bool:
    return any(kw in text for kw in keywords)


def _map_intent(raw_intent: str, text_norm: str) -> str:
    intent = _INTENT_MAP.get(raw_intent, "general_question")
    if raw_intent == "search" and _has(text_norm, _EXPLICIT_SEARCH_KW):
        return "search_places"
    return intent


def _build_filters(text_norm: str, destination: Optional[str], interests: list[str]) -> ContextFilters:
    governorate = city = None
    if destination and destination in _GOVERNORATE_MAP:
        governorate, city = _GOVERNORATE_MAP[destination]

    category = None
    for tag in interests:
        if tag in _TAG_TO_CATEGORY:
            category = _TAG_TO_CATEGORY[tag]
            break

    budget_tier = None
    for tier, kws in _BUDGET_TIER_KW.items():
        if _has(text_norm, kws):
            budget_tier = tier
            break

    return ContextFilters(governorate=governorate, city=city, category=category, budget_tier=budget_tier)


def _build_preferences(text_norm: str, interests: list[str], group_type: Optional[str]) -> dict[str, float]:
    prefs: dict[str, float] = {}

    def put(key: str, weight: float) -> None:
        # عند تعارض المصدرين لنفس المفتاح، نُبقي الأكبر مقدارًا (الإشارة الأقوى)
        if key not in prefs or abs(weight) > abs(prefs[key]):
            prefs[key] = weight

    for tag in interests:
        if tag in _TAG_TO_PREF:
            key, weight = _TAG_TO_PREF[tag]
            put(key, weight)
    if group_type in _GROUP_TO_PREF:
        key, weight = _GROUP_TO_PREF[group_type]
        put(key, weight)
    if _has(text_norm, _PHOTOGRAPHY_KW):
        put("photography", 0.9)
    if _has(text_norm, _ROMANTIC_KW):
        put("romantic", 0.8)
    if _has(text_norm, _CROWD_AVOID_KW):
        put("crowdedness", -0.8)

    return {key: round(weight, 2) for key, weight in prefs.items()}


def _build_trip_context(
    text_norm: str, group_type: Optional[str], interests: list[str]
) -> TripContext:
    duration_minutes = None
    if "ساعتين" in text_norm:
        duration_minutes = 120
    else:
        match = _DURATION_HOURS_PATTERN.search(text_norm)
        if match:
            duration_minutes = int(match.group(1)) * 60

    preferred_time = next((t for t, kws in _PREFERRED_TIME_KW.items() if _has(text_norm, kws)), None)
    season = next((s for s, kws in _SEASON_KW.items() if _has(text_norm, kws)), None)

    # صعوبة بدنية: مغامرة → moderate؛ عائلة/هدوء → easy؛ غير ذلك → غير محدد
    physical_difficulty: Optional[str] = None
    if "tag:adventure" in interests:
        physical_difficulty = "moderate"
    elif group_type == "family" or "tag:quiet" in interests:
        physical_difficulty = "easy"

    return TripContext(
        group_type=group_type,  # type: ignore[arg-type]
        duration_minutes=duration_minutes,
        preferred_time=preferred_time,  # type: ignore[arg-type]
        season=season,  # type: ignore[arg-type]
        visit_date=None,  # لا نحسب تواريخ من كلام نسبي ("غدًا") — يبقى ضمن query_text
        physical_difficulty=physical_difficulty,  # type: ignore[arg-type]
    )


def _negated_tags(text_norm: str) -> set[str]:
    """الوسوم التي وردت كلماتها مسبوقةً بأداة نفي ("بدون متاحف" → tag:museum).
    تُستبعد لاحقًا من الاهتمامات الإيجابية وتُدرَج ضمن exclusions."""
    if not _has(text_norm, _NEGATION_KW):
        return set()
    negated: set[str] = set()
    for keyword, tag in entities_mod.INTEREST_TAGS.items():
        if _negated(text_norm, keyword):
            negated.add(tag)
    return negated


def _build_exclusions(text_norm: str, negated_tags: set[str]) -> ContextExclusions:
    requirements: list[str] = []
    if _has(text_norm, _ACCESSIBILITY_KW):
        requirements.append("wheelchair_accessible")

    categories: list[str] = []
    for tag in negated_tags:
        cat = _TAG_TO_CATEGORY.get(tag)
        if cat and cat not in categories:
            categories.append(cat)

    return ContextExclusions(categories=sorted(categories), place_ids=[], requirements=requirements)


def _negated(text_norm: str, keyword: str) -> bool:
    """هل وردت أداة نفي قبل الكلمة مباشرةً (ضمن ~12 محرفًا)؟"""
    idx = text_norm.find(keyword)
    if idx == -1:
        return False
    window = text_norm[max(0, idx - 12):idx]
    return _has(window, _NEGATION_KW)


# حروف جر تُصبح يتيمة بعد حذف اسم المدينة التالي لها ("في دمشق"/"to latakia")
_ORPHAN_PREPS = frozenset({"في", "ب", "بـ", "ع", "على", "لـ", "to", "in", "at", "near", "around"})


def _build_query_text(text_norm: str, filters: ContextFilters, destination: Optional[str]) -> str:
    """وصف مقتضب للبحث الدلالي: نزيل ألفاظ الطلب من المقدمة، وكلمات المدينة/
    الميزانية التي التُقطت أصلًا كقيود منظمة (تفاديًا للتكرار). الحذف على مستوى
    الكلمة الكاملة لا الجزء (كي لا نمزّق "ومجانيا" إلى "و ا"). BGE-M3 يتحمل ما
    يتبقى. إن أفرغنا النص نرجع للنص المطبَّع كاملًا (أفضل من فراغ)."""
    q = f" {text_norm} "
    for prefix in sorted(_REQUEST_PREFIXES, key=len, reverse=True):
        q = q.replace(f" {prefix} ", " ")

    drop_surfaces: list[str] = []
    if destination:
        drop_surfaces += entities_mod.CITIES.get(destination, [])
    if filters.budget_tier:
        drop_surfaces += list(_BUDGET_TIER_KW[filters.budget_tier])

    kept: list[str] = []
    for token in q.split():
        if any(surface in token for surface in drop_surfaces):
            # الكلمة قيد إزالتها (مدينة/ميزانية) — أزل حرف الجر اليتيم قبلها إن وُجد
            if kept and kept[-1] in _ORPHAN_PREPS:
                kept.pop()
            continue
        kept.append(token)

    return " ".join(kept).strip() or text_norm


def _missing_information(
    intent: str,
    filters: ContextFilters,
    interests: list[str],
    preferences: dict[str, float],
    has_duration: bool,
    group_type: Optional[str],
) -> list[str]:
    """الحقول الإلزامية الناقصة حسب النية (مفردات ثابتة يقرؤها Laravel)."""
    missing: list[str] = []
    if intent in ("recommend_places", "search_places"):
        if not filters.governorate and not filters.city:
            missing.append("governorate_or_city")
        if not interests and not preferences:
            missing.append("trip_interests")
    elif intent == "create_plan":
        if not filters.governorate and not filters.city:
            missing.append("governorate_or_city")
        if not has_duration:
            missing.append("duration")
        if not group_type:
            missing.append("group_type")
    # place_details / modify_plan / general_question: تُحرَّك بالثقة فقط v1
    return missing


def extract_context(message: str, language: Optional[str] = None) -> ConversationContextV1:
    """يبني عقد conversation_context_v1 من رسالة واحدة (بلا حالة)."""
    lang = language or detect_language(message)
    text = normalize(message)

    raw_intent, confidence = classify_raw(text)
    # ثقة متدنية (< العتبة) → النية غير موثوقة؛ نرجع للفعل الافتراضي للمنصة
    # (recommend_places) كي يعمل فحص النقص ويسأل عن الموقع/الاهتمامات بدل تمرير
    # نية عشوائية. يطابق المثال الثاني في العقد ("مكان مناسب غدًا").
    if confidence < settings.intent_confidence_threshold:
        intent = "recommend_places"
    else:
        intent = _map_intent(raw_intent, text)

    ents = entities_mod.extract_entities(text)
    destination: Optional[str] = ents["destination"][0] if ents.get("destination") else None
    group_type: Optional[str] = ents.get("group_type")
    has_duration = "duration_days" in ents

    # نستبعد الوسوم المنفيّة ("بدون متاحف") من الاهتمامات الإيجابية كي لا تصير فئةً
    # أو تفضيلًا يناقض الاستبعاد
    negated = _negated_tags(text)
    interests: list[str] = [t for t in ents.get("interests", []) if t not in negated]

    filters = _build_filters(text, destination, interests)
    preferences = _build_preferences(text, interests, group_type)
    trip_context = _build_trip_context(text, group_type, interests)
    exclusions = _build_exclusions(text, negated)
    query_text = _build_query_text(text, filters, destination)

    # requires_clarification تحكمه **كفاية المعلومات** (missing_information) لا ثقة
    # المصنّف: إن استخرجنا محافظة + اهتمامات فبإمكان Laravel التصرّف مهما كانت ثقة
    # النية. حقل confidence يُبلَّغ مستقلًّا كي يقرر Laravel عتبته الخاصة.
    missing = _missing_information(intent, filters, interests, preferences, has_duration, group_type)
    requires_clarification = bool(missing)
    clarification_question = (
        responses.context_clarification(missing, lang) if requires_clarification else None
    )

    return ConversationContextV1(
        intent=intent,  # type: ignore[arg-type]
        language=lang,  # type: ignore[arg-type]
        query_text=query_text,
        filters=filters,
        preferences=preferences,
        trip_context=trip_context,
        location=ContextLocation(),  # يأتي من الجهاز/Laravel، ليس من النص
        exclusions=exclusions,
        confidence=round(confidence, 2),
        requires_clarification=requires_clarification,
        missing_information=missing,
        clarification_question=clarification_question,
    )
