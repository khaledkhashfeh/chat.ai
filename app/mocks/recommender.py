"""app/mocks/recommender.py

نسخة وهمية لمحرك التوصية — 5 أدوات ملتزمة حرفيًا بـ docs/contract.md §2:
search / details / compare / log / profile. تعمل فوق بيانات ثابتة في
app/mocks/data.py فقط — بدون أي استدعاء شبكي أو نموذج لغوي.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional, Union

from app.mocks.data import PLACES, get_place
from app.shared.models import (
    CompareAxis,
    CompareResponse,
    CompareVerdict,
    ConversationState,
    ErrorDetail,
    ErrorObject,
    ErrorSuggestion,
    GroupSuitability,
    LastActivity,
    LogEventType,
    LogResponse,
    LogSource,
    OpeningHours,
    OpeningHoursDay,
    PlaceCard,
    PlaceDetails,
    ProfileResponse,
    SearchFallback,
    SearchResponse,
    SearchScope,
)

# ---------------------------------------------------------------------------
# أدوات مساعدة داخلية
# ---------------------------------------------------------------------------

_PY_WEEKDAY_TO_KEY = {5: "sat", 6: "sun", 0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri"}

_PRICE_FOR_BUDGET = {
    "low": {"free", "cheap"},
    "medium": {"cheap", "medium"},
    "high": {"medium", "expensive"},
}

_TAG_TRAIT_AR = {
    "tag:historical": "قيمته التاريخية",
    "tag:religious": "أهميته الدينية",
    "tag:nature": "مناظره الطبيعية",
    "tag:sea": "قربه من البحر",
    "tag:market": "أجواء السوق النابضة",
    "tag:food": "خياراته الغذائية",
    "tag:family_fun": "ملاءمته للأطفال",
    "tag:adventure": "طابعه المغامر",
    "tag:quiet": "هدوءه",
    "tag:museum": "محتواه المتحفي",
}
_TAG_TRAIT_EN = {
    "tag:historical": "its historical value",
    "tag:religious": "its religious significance",
    "tag:nature": "its natural scenery",
    "tag:sea": "its seaside location",
    "tag:market": "its lively market atmosphere",
    "tag:food": "its food options",
    "tag:family_fun": "how kid-friendly it is",
    "tag:adventure": "its adventurous character",
    "tag:quiet": "its calm setting",
    "tag:museum": "its museum content",
}

_REASON_TEMPLATES_AR = {
    "family": "مناسب لعائلتك بفضل {trait}",
    "couple": "أجواء هادئة تناسب رحلتكما، و{trait}",
    "friends": "خيار ممتع لمجموعة أصحاب بفضل {trait}",
    "solo": "مريح لرحلة فردية هادئة، و{trait}",
    "large_group": "يستوعب مجموعتكم الكبيرة بارتياح، و{trait}",
    None: "خيار مقيَّم جيدًا بفضل {trait}",
}
_REASON_TEMPLATES_EN = {
    "family": "great for your family thanks to {trait}",
    "couple": "a calm spot that suits your trip, plus {trait}",
    "friends": "a fun pick for a friends trip thanks to {trait}",
    "solo": "relaxed for solo travel, plus {trait}",
    "large_group": "comfortably fits a large group, plus {trait}",
    None: "well-rated overall thanks to {trait}",
}


def _recommendation_reason(place: dict, state: ConversationState) -> str:
    """جملة مخصصة لسياق هذا المستخدم (وسمه المشترك مع اهتماماته + نوع مجموعته)."""
    lang = state.language
    tags = place["tags"]
    trait_tag = next((t for t in state.interests if t in tags), tags[0] if tags else None)
    trait_map = _TAG_TRAIT_AR if lang == "ar" else _TAG_TRAIT_EN
    trait = trait_map.get(trait_tag, "تقييمه الجيد" if lang == "ar" else "its good rating")
    templates = _REASON_TEMPLATES_AR if lang == "ar" else _REASON_TEMPLATES_EN
    template = templates.get(state.group_type, templates[None])
    return template.format(trait=trait)


def _place_card(place: dict, state: ConversationState) -> PlaceCard:
    return PlaceCard(
        place_id=place["place_id"],
        name_ar=place["name_ar"],
        name_en=place["name_en"],
        city=place["city"],
        category=place["category"],
        tags=list(place["tags"]),
        rating=place["rating"],
        reviews_count=place["reviews_count"],
        photo_url=place["photo_url"],
        recommendation_reason=_recommendation_reason(place, state),
        visit_duration_min=place["visit_duration_min"],
        lat=place["lat"],
        lng=place["lng"],
        price_level=place["price_level"],
    )


def _score(place: dict, state: ConversationState, query: str) -> float:
    score = float(place["rating"])
    matching_tags = set(state.interests) & set(place["tags"])
    score += 2.0 * len(matching_tags)
    if state.group_type:
        score += place["group_suitability"].get(state.group_type, 50) / 20
    if state.budget_level:
        allowed = _PRICE_FOR_BUDGET.get(state.budget_level, set())
        if place["price_level"] in allowed:
            score += 1.0
    q = (query or "").strip().lower()
    if q:
        haystack = " ".join([place["name_ar"], place["name_en"], place["city"], place["category"]]).lower()
        for token in q.split():
            if len(token) >= 2 and token in haystack:
                score += 1.5
                break
    return score


def _diversify(places: list[dict], max_run: int = 2) -> list[dict]:
    """يمنع أكثر من max_run أماكن متتالية من نفس الفئة (التزام contract.md بالتنويع)."""
    result: list[dict] = []
    pool = list(places)
    while pool:
        for i, p in enumerate(pool):
            recent = result[-max_run:]
            if len(recent) < max_run or any(r["category"] != p["category"] for r in recent):
                result.append(pool.pop(i))
                break
        else:
            result.append(pool.pop(0))
    return result


def _ranking_note(state: ConversationState) -> str:
    if state.language == "en":
        if state.group_type:
            return f"Ranked by best fit for a {state.group_type} trip and overall rating."
        return "Ranked by overall rating and relevance to your interests."
    if state.group_type:
        return "رتّبنا حسب الملاءمة لنوع رحلتك والتقييم العام"
    return "رتّبنا حسب التقييم العام والملاءمة لاهتماماتك"


def _build_fallback(scope: Optional[SearchScope], state: ConversationState) -> SearchFallback:
    city = (scope.city if scope else None) or (state.destination[0] if state.destination else None)
    if state.language == "ar":
        reason = f"ما لقيت أماكن مطابقة في {city}" if city else "ما لقيت أماكن مطابقة بمعاييرك الحالية"
    else:
        reason = f"No matching places found in {city}" if city else "No matching places found for your current criteria"
    return SearchFallback(
        reason=reason,
        suggestion=ErrorSuggestion(action="expand_search", params={"city": None}),
    )


# ---------------------------------------------------------------------------
# 2.1 search
# ---------------------------------------------------------------------------


async def search(
    query: str,
    state: ConversationState,
    top_k: int = 8,
    scope: Optional[SearchScope] = None,
) -> SearchResponse:
    candidates = list(PLACES.values())

    if scope and scope.city:
        candidates = [p for p in candidates if p["city"] == scope.city]
    elif state.destination:
        candidates = [p for p in candidates if p["city"] in state.destination]

    if scope and scope.category:
        candidates = [p for p in candidates if p["category"] == scope.category]

    # التزام صارم: استبعاد excluded_place_ids دون استثناء
    candidates = [p for p in candidates if p["place_id"] not in state.excluded_place_ids]

    if not candidates:
        return SearchResponse(results=[], ranking_note=_ranking_note(state), fallback=_build_fallback(scope, state))

    scored = sorted(candidates, key=lambda p: _score(p, state, query), reverse=True)
    diversified = _diversify(scored)
    top = diversified[: max(top_k, 0)]

    return SearchResponse(
        results=[_place_card(p, state) for p in top],
        ranking_note=_ranking_note(state),
        fallback=None,
    )


# ---------------------------------------------------------------------------
# 2.2 details
# ---------------------------------------------------------------------------


async def details(
    place_id: str, language: str, now: Optional[datetime] = None
) -> Union[PlaceDetails, ErrorObject]:
    place = get_place(place_id)
    if place is None:
        msg = "ما لقيت هالمكان بقاعدة بياناتنا" if language == "ar" else "I couldn't find that place in our database"
        return ErrorObject(error=ErrorDetail(type="no_results", user_message=msg))

    now = now or datetime.now()
    day_key = _PY_WEEKDAY_TO_KEY[now.weekday()]
    today_hours = place["opening_hours"].get(day_key)
    open_now = False
    if today_hours is not None:
        current = now.strftime("%H:%M")
        open_now = today_hours["open"] <= current <= today_hours["close"]

    oh = place["opening_hours"]
    opening_hours = OpeningHours(
        **{
            day: (OpeningHoursDay(**oh[day]) if oh[day] else None)
            for day in ("sat", "sun", "mon", "tue", "wed", "thu", "fri")
        }
    )

    dummy_state = ConversationState(language=language)
    card = _place_card(place, dummy_state)

    return PlaceDetails(
        place=card,
        description=place["description_ar"] if language == "ar" else place["description_en"],
        opening_hours=opening_hours,
        open_now=open_now,
        phone=place["phone"],
        website=place["website"],
        photos=list(place["photos"]),
        group_suitability=GroupSuitability(**place["group_suitability"]),
        best_season=place["best_season"],
        best_time_of_day=place["best_time_of_day"],
        practical_notes=list(place["practical_notes_ar"] if language == "ar" else place["practical_notes_en"]),
    )


# ---------------------------------------------------------------------------
# 2.3 compare
# ---------------------------------------------------------------------------

_REFERENCE_POINT = (33.5138, 36.2765)  # مركز دمشق — مرجع افتراضي لحساب "البعد عنك" بالنسخة الوهمية


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def compare(
    place_ids: list[str], state: ConversationState, language: str
) -> Union[CompareResponse, ErrorObject]:
    if not (2 <= len(place_ids) <= 4):
        msg = (
            "المقارنة تحتاج مكانين إلى أربعة أماكن على الأكثر"
            if language == "ar"
            else "Comparison needs between 2 and 4 places"
        )
        return ErrorObject(error=ErrorDetail(type="missing_input", user_message=msg))

    places = []
    for pid in place_ids:
        p = get_place(pid)
        if p is None:
            msg = f"ما لقيت المكان {pid}" if language == "ar" else f"Couldn't find place {pid}"
            return ErrorObject(error=ErrorDetail(type="no_results", user_message=msg))
        places.append(p)

    cards = [_place_card(p, state) for p in places]

    rating_values: dict[str, Union[float, str]] = {p["place_id"]: p["rating"] for p in places}
    group_fit_values: dict[str, Union[float, str]] = {
        p["place_id"]: (p["group_suitability"].get(state.group_type, 50) if state.group_type else 50)
        for p in places
    }
    duration_values: dict[str, Union[float, str]] = {p["place_id"]: p["visit_duration_min"] for p in places}
    distance_values: dict[str, Union[float, str]] = {
        p["place_id"]: round(_haversine_km(*_REFERENCE_POINT, p["lat"], p["lng"]), 1) for p in places
    }
    price_values: dict[str, Union[float, str]] = {p["place_id"]: p["price_level"] for p in places}

    axes = [
        CompareAxis(axis="rating", label_ar="التقييم", values=rating_values),
        CompareAxis(axis="group_fit", label_ar="الملاءمة لمجموعتك", values=group_fit_values),
        CompareAxis(axis="visit_duration_min", label_ar="مدة الزيارة", values=duration_values),
        CompareAxis(axis="distance_km", label_ar="البعد عنك", values=distance_values),
        CompareAxis(axis="price_level", label_ar="السعر", values=price_values),
    ]

    def weighted(pid: str) -> float:
        return (
            float(group_fit_values[pid]) * 0.5
            + float(rating_values[pid]) * 10 * 0.3
            + max(0.0, 100.0 - float(distance_values[pid])) * 0.2
        )

    winner_id = max((p["place_id"] for p in places), key=weighted)
    winner_place = next(p for p in places if p["place_id"] == winner_id)
    if language == "ar":
        reason = f"لسياق رحلتك، {winner_place['name_ar']} الأنسب: ملاءمة أعلى وتقييم قوي"
    else:
        reason = f"For your trip, {winner_place['name_en']} fits best: higher suitability and a strong rating"

    return CompareResponse(places=cards, axes=axes, verdict=CompareVerdict(winner_place_id=winner_id, reason=reason))


# ---------------------------------------------------------------------------
# 2.4 log — غير متزامن منطقيًا (لا تنتظره المحادثة)، لكن المعالجة هنا فورية ورخيصة
# ---------------------------------------------------------------------------

_LOG_EVENTS: list[dict] = []  # سجل بالذاكرة لأغراض الاختبار والتدقيق فقط


async def log(
    user_id: str, place_id: str, event: LogEventType, source: LogSource, ts: Optional[str] = None
) -> LogResponse:
    _LOG_EVENTS.append(
        {
            "user_id": user_id,
            "place_id": place_id,
            "event": event,
            "source": source,
            "ts": ts or datetime.now(timezone.utc).isoformat(),
        }
    )
    return LogResponse(ok=True)


# ---------------------------------------------------------------------------
# 2.5 profile
# ---------------------------------------------------------------------------

_MOCK_PROFILES: dict[str, ProfileResponse] = {
    "u_demo": ProfileResponse(
        top_tags=["tag:historical", "tag:quiet"],
        visited_cities=["دمشق"],
        usual_group_type="family",
        usual_pace="relaxed",
        last_activity=LastActivity(type="plan_draft", city="اللاذقية", days_ago=7),
    )
}


async def profile(user_id: str) -> ProfileResponse:
    # مستخدم جديد → كل الحقول null/فارغة (افتراضي ProfileResponse())
    return _MOCK_PROFILES.get(user_id, ProfileResponse())
