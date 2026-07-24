"""app/mocks/planner.py

نسخة وهمية لمحرك التخطيط — 3 أدوات ملتزمة حرفيًا بـ docs/contract.md §3:
build / modify / feasibility. الخطط المبنية تُحفظ بذاكرة العملية (dict) كي
تستطيع modify إيجادها لاحقًا بنفس الجلسة/العملية — لا قاعدة بيانات إضافية.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from typing import Optional, Union

from app.mocks.data import PLACES, get_place, places_by_city
from app.shared.models import (
    ConversationState,
    CostEstimate,
    ErrorDetail,
    ErrorObject,
    FeasibilityResponse,
    FeasibilitySuggestion,
    Modification,
    ModifyResponse,
    PlaceCard,
    PlanChange,
    PlanDay,
    PlanObject,
    PlanStop,
    StartLocation,
    Tradeoff,
    TradeoffOption,
)

_PLANS: dict[str, PlanObject] = {}
_plan_id_counter = itertools.count(1)

_PRICE_COST = {"free": 0.0, "cheap": 5.0, "medium": 15.0, "expensive": 30.0}
_TIME_OF_DAY_AR = {"morning": "الصباح", "afternoon": "بعد الظهر", "evening": "المساء"}
_PACE_LABEL_AR = {"relaxed": "مريح", "moderate": "معتدل", "intense": "مكثف"}
_GROUP_LABEL_AR = {
    "solo": "فردية", "couple": "زوجين", "family": "عائلية",
    "friends": "أصحاب", "large_group": "مجموعة كبيرة",
}

MAX_STOPS_PER_DAY = 3


def _activity_cost(place: dict) -> float:
    return _PRICE_COST.get(place["price_level"], 10.0)


def _why_here(place: dict, state: ConversationState) -> str:
    if state.language == "en":
        return f"Scheduled for the {place['best_time_of_day']} — its best time of day"
    time_ar = _TIME_OF_DAY_AR.get(place["best_time_of_day"], "هذا الوقت")
    return f"وضعناه بـ{time_ar} لأنه أفضل وقت لزيارته"


def _place_card_ar(place: dict, reason: str) -> PlaceCard:
    """بطاقة مكان بديل — عربية افتراضيًا لأن عقد modify لا يحمل حقل language."""
    return PlaceCard(
        place_id=place["place_id"], name_ar=place["name_ar"], name_en=place["name_en"],
        city=place["city"], category=place["category"], tags=list(place["tags"]),
        rating=place["rating"], reviews_count=place["reviews_count"], photo_url=place["photo_url"],
        recommendation_reason=reason, visit_duration_min=place["visit_duration_min"],
        lat=place["lat"], lng=place["lng"], price_level=place["price_level"],
    )


# ---------------------------------------------------------------------------
# 3.1 build
# ---------------------------------------------------------------------------


async def build(
    state: ConversationState,
    mandatory_place_ids: Optional[list[str]] = None,
    candidate_place_ids: Optional[list[str]] = None,
    start_location: Optional[StartLocation] = None,
) -> Union[PlanObject, ErrorObject]:
    lang = state.language
    mandatory_place_ids = mandatory_place_ids or []
    candidate_place_ids = candidate_place_ids or []

    missing = [f for f in ("destination", "duration_days", "group_type") if not getattr(state, f)]
    if missing:
        msg = (
            "معلومات الوجهة أو المدة أو نوع المجموعة غير مكتملة لبناء خطة"
            if lang == "ar"
            else "Destination, duration, or group type is missing to build a plan"
        )
        return ErrorObject(error=ErrorDetail(type="missing_input", user_message=msg))

    duration_days: int = state.duration_days  # type: ignore[assignment]
    city = state.destination[0]

    mandatory_places = [get_place(pid) for pid in mandatory_place_ids]
    mandatory_places = [p for p in mandatory_places if p is not None]

    candidate_places = [get_place(pid) for pid in candidate_place_ids]
    candidate_places = [p for p in candidate_places if p is not None]
    if not candidate_places:
        # المحرك يطلبها من التوصية داخليًا عند غيابها — هنا نحاكي بأماكن نفس المدينة
        mandatory_ids = {p["place_id"] for p in mandatory_places}
        candidate_places = [
            p for p in places_by_city(city)
            if p["place_id"] not in mandatory_ids and p["place_id"] not in state.excluded_place_ids
        ]

    chosen: list[dict] = list(mandatory_places)
    seen_ids = {p["place_id"] for p in chosen}
    target_total = duration_days * MAX_STOPS_PER_DAY
    for p in sorted(candidate_places, key=lambda x: x["rating"], reverse=True):
        if len(chosen) >= target_total:
            break
        if p["place_id"] in seen_ids:
            continue
        chosen.append(p)
        seen_ids.add(p["place_id"])

    days_stops: list[list[dict]] = [[] for _ in range(duration_days)]
    for i, p in enumerate(chosen):
        days_stops[i % duration_days].append(p)

    tradeoffs: list[Tradeoff] = []
    avg_per_day = len(chosen) / duration_days if duration_days else 0.0
    if avg_per_day > MAX_STOPS_PER_DAY - 0.5 and len(chosen) > duration_days * 2:
        tradeoffs.append(
            Tradeoff(
                issue_ar=f"{len(chosen)} أماكن على {duration_days} أيام إيقاع مكثف بعض الشيء",
                options=[
                    TradeoffOption(action="extend_days", label_ar="تمديد الخطة ليوم إضافي"),
                    TradeoffOption(
                        action="drop_place",
                        label_ar="حذف أحد الأماكن الأقل أولوية",
                        params={"place_id": chosen[-1]["place_id"]},
                    ),
                ],
            )
        )

    plan_days: list[PlanDay] = []
    total_activities = 0.0
    total_food = 0.0
    total_transport = 0.0

    for day_index in range(duration_days):
        stops_for_day = days_stops[day_index]
        stops: list[PlanStop] = []
        current_time = datetime.strptime("09:00", "%H:%M")
        day_cost = 0.0
        added_lunch = False

        for j, p in enumerate(stops_for_day):
            travel = 0 if j == 0 else 15
            current_time += timedelta(minutes=travel)
            arrival = current_time.strftime("%H:%M")
            current_time += timedelta(minutes=p["visit_duration_min"])
            departure = current_time.strftime("%H:%M")
            cost = _activity_cost(p)
            day_cost += cost
            total_activities += cost
            if travel:
                total_transport += 2.0
            stops.append(
                PlanStop(
                    place_id=p["place_id"], name_ar=p["name_ar"], name_en=p["name_en"],
                    stop_type="visit", arrival=arrival, departure=departure,
                    visit_duration_min=p["visit_duration_min"], travel_from_prev_min=travel,
                    travel_mode="drive" if travel else None, cost_estimate=cost,
                    why_here=_why_here(p, state),
                )
            )
            if not added_lunch and current_time.hour >= 12:
                lunch_start = current_time
                lunch_end = lunch_start + timedelta(minutes=60)
                stops.append(
                    PlanStop(
                        place_id=None, name_ar="غداء", name_en="Lunch", stop_type="meal",
                        arrival=lunch_start.strftime("%H:%M"), departure=lunch_end.strftime("%H:%M"),
                        visit_duration_min=60, travel_from_prev_min=10, travel_mode="walk",
                        cost_estimate=15.0,
                        why_here=(
                            "استراحة منتصف اليوم قرب محطتك التالية"
                            if lang == "ar" else "Midday break near your next stop"
                        ),
                    )
                )
                current_time = lunch_end + timedelta(minutes=10)
                day_cost += 15.0
                total_food += 15.0
                added_lunch = True

        if not stops:
            stops.append(
                PlanStop(
                    place_id=None,
                    name_ar="يوم حر للاستكشاف الذاتي", name_en="Free day to explore on your own",
                    stop_type="rest", arrival="09:00", departure="18:00", visit_duration_min=540,
                    travel_from_prev_min=0, travel_mode=None, cost_estimate=0.0,
                    why_here=(
                        "ما توفرت أماكن كافية لهذا اليوم بعد"
                        if lang == "ar" else "Not enough places available for this day yet"
                    ),
                )
            )

        plan_days.append(
            PlanDay(
                day_number=day_index + 1,
                date=(state.dates.start if state.dates and day_index == 0 else None),
                title_ar=f"يوم {day_index + 1}",
                weather_note=None,
                day_cost_estimate=day_cost,
                stops=stops,
            )
        )

    plan_id = f"pl_{next(_plan_id_counter):04d}"
    summary_ar = (
        f"{duration_days} أيام في {city} بإيقاع {_PACE_LABEL_AR.get(state.pace, 'معتدل')} "
        f"لرحلة {_GROUP_LABEL_AR.get(state.group_type, 'عامة')}"
    )
    summary_en = f"{duration_days} days in {city} at a {state.pace or 'moderate'} pace for a {state.group_type} trip"

    plan = PlanObject(
        plan_id=plan_id, summary_ar=summary_ar, summary_en=summary_en,
        total_cost_estimate=CostEstimate(activities=total_activities, food=total_food, transport=total_transport),
        days=plan_days, tradeoffs=tradeoffs,
    )
    _PLANS[plan_id] = plan
    return plan


# ---------------------------------------------------------------------------
# 3.2 modify — العقد الأدق: التعديل جراحي، يمس اليوم المتأثر فقط
# ---------------------------------------------------------------------------


def _find_stop(plan: PlanObject, place_id: Optional[str]):
    if not place_id:
        return None, None
    for day in plan.days:
        for idx, stop in enumerate(day.stops):
            if stop.place_id == place_id:
                return day, idx
    return None, None


def _retime_day(day: PlanDay) -> None:
    current = datetime.strptime("09:00", "%H:%M")
    day_cost = 0.0
    for i, stop in enumerate(day.stops):
        travel = 0 if i == 0 else (stop.travel_from_prev_min or 15)
        stop.travel_from_prev_min = travel
        current += timedelta(minutes=travel)
        stop.arrival = current.strftime("%H:%M")
        current += timedelta(minutes=stop.visit_duration_min)
        stop.departure = current.strftime("%H:%M")
        day_cost += stop.cost_estimate
    day.day_cost_estimate = day_cost


def _remove_stop(plan: PlanObject, target_place_id: Optional[str]) -> list[PlanChange]:
    day, idx = _find_stop(plan, target_place_id)
    if day is None:
        return [PlanChange(change_ar=f"ما لقيت المكان {target_place_id} بالخطة")]
    removed = day.stops.pop(idx)
    _retime_day(day)
    return [PlanChange(change_ar=f"حذفنا {removed.name_ar} من اليوم {day.day_number}")]


def _add_stop(plan: PlanObject, modification: Modification) -> list[PlanChange]:
    params = modification.params
    place_id = params.get("place_id") or modification.target_place_id
    day_number = params.get("day_number", 1)
    place = get_place(place_id) if place_id else None
    if place is None:
        return [PlanChange(change_ar="ما لقيت المكان المطلوب إضافته")]
    day = next((d for d in plan.days if d.day_number == day_number), plan.days[0])
    day.stops.append(
        PlanStop(
            place_id=place["place_id"], name_ar=place["name_ar"], name_en=place["name_en"],
            stop_type="visit", arrival="09:00", departure="09:00",
            visit_duration_min=place["visit_duration_min"], travel_from_prev_min=15,
            travel_mode="drive", cost_estimate=_activity_cost(place), why_here="أُضيف حسب طلبك",
        )
    )
    _retime_day(day)
    return [PlanChange(change_ar=f"أضفنا {place['name_ar']} لليوم {day.day_number}")]


def _replace_with_place(plan: PlanObject, modification: Modification) -> list[PlanChange]:
    day, idx = _find_stop(plan, modification.target_place_id)
    new_place = get_place(modification.params.get("place_id", ""))
    if day is None or new_place is None:
        return [PlanChange(change_ar="تعذّر تنفيذ الاستبدال — تأكد من المكانين")]
    old = day.stops[idx]
    day.stops[idx] = PlanStop(
        place_id=new_place["place_id"], name_ar=new_place["name_ar"], name_en=new_place["name_en"],
        stop_type="visit", arrival=old.arrival, departure=old.departure,
        visit_duration_min=new_place["visit_duration_min"], travel_from_prev_min=old.travel_from_prev_min,
        travel_mode=old.travel_mode, cost_estimate=_activity_cost(new_place), why_here="بديل باختيارك",
    )
    _retime_day(day)
    return [PlanChange(change_ar=f"استبدلنا {old.name_ar} بـ{new_place['name_ar']} باليوم {day.day_number}")]


def _replace_with_kind(plan: PlanObject, modification: Modification) -> tuple[list[PlanChange], list[PlaceCard]]:
    params = modification.params
    kind_tags = set(params.get("kind_tags", []))
    day, idx = _find_stop(plan, modification.target_place_id)
    if day is None:
        return [PlanChange(change_ar="ما لقيت المكان المطلوب استبداله")], []

    existing_ids = {s.place_id for d in plan.days for s in d.stops if s.place_id}
    candidates = [
        p for p in PLACES.values()
        if kind_tags & set(p["tags"]) and p["place_id"] not in existing_ids
    ]
    candidates.sort(key=lambda p: p["rating"], reverse=True)
    if not candidates:
        return [PlanChange(change_ar="ما لقيت بديلًا مناسبًا من نفس النوعية")], []

    chosen = candidates[0]
    alt_places = candidates[1:3]
    old = day.stops[idx]
    day.stops[idx] = PlanStop(
        place_id=chosen["place_id"], name_ar=chosen["name_ar"], name_en=chosen["name_en"],
        stop_type="visit", arrival=old.arrival, departure=old.departure,
        visit_duration_min=chosen["visit_duration_min"], travel_from_prev_min=old.travel_from_prev_min,
        travel_mode=old.travel_mode, cost_estimate=_activity_cost(chosen),
        why_here="بديل بنفس النوعية المطلوبة",
    )
    _retime_day(day)
    changes = [PlanChange(change_ar=f"استبدلنا {old.name_ar} بـ{chosen['name_ar']} باليوم {day.day_number}")]
    alternatives = [_place_card_ar(p, "بديل جيد التقييم بنفس النوعية المطلوبة") for p in alt_places]
    return changes, alternatives


def _move_to_day(plan: PlanObject, modification: Modification) -> list[PlanChange]:
    day, idx = _find_stop(plan, modification.target_place_id)
    target_day_number = modification.params.get("day_number")
    if day is None or target_day_number is None:
        return [PlanChange(change_ar="تعذّر نقل المحطة — تأكد من المكان واليوم الهدف")]
    target_day = next((d for d in plan.days if d.day_number == target_day_number), None)
    if target_day is None:
        return [PlanChange(change_ar="اليوم الهدف غير موجود بالخطة")]
    stop = day.stops.pop(idx)
    target_day.stops.append(stop)
    _retime_day(day)
    _retime_day(target_day)
    return [PlanChange(change_ar=f"نقلنا {stop.name_ar} من اليوم {day.day_number} إلى اليوم {target_day.day_number}")]


def _shift_time(plan: PlanObject, modification: Modification) -> list[PlanChange]:
    params = modification.params
    minutes = int(params.get("minutes", -30))
    stop_type = params.get("stop_type")
    day = stop = None

    if modification.target_place_id:
        day, idx = _find_stop(plan, modification.target_place_id)
        if day is not None:
            stop = day.stops[idx]
    elif stop_type:
        for d in plan.days:
            for s in d.stops:
                if s.stop_type == stop_type:
                    day, stop = d, s
                    break
            if stop:
                break

    if not stop or not day:
        return [PlanChange(change_ar="ما لقيت المحطة المطلوب تعديل وقتها")]

    stop.arrival = (datetime.strptime(stop.arrival, "%H:%M") + timedelta(minutes=minutes)).strftime("%H:%M")
    stop.departure = (datetime.strptime(stop.departure, "%H:%M") + timedelta(minutes=minutes)).strftime("%H:%M")
    direction = "قدّمنا" if minutes < 0 else "أخّرنا"
    return [PlanChange(change_ar=f"{direction} {stop.name_ar} {abs(minutes)} دقيقة باليوم {day.day_number}")]


def _change_day_pace(plan: PlanObject, modification: Modification) -> list[PlanChange]:
    day_number = modification.params.get("day_number")
    pace = modification.params.get("pace", "relaxed")
    day = next((d for d in plan.days if d.day_number == day_number), None)
    if day is None:
        return [PlanChange(change_ar="اليوم المطلوب تعديل إيقاعه غير موجود")]
    day.title_ar = f"{day.title_ar.split(' (')[0]} (إيقاع {_PACE_LABEL_AR.get(pace, pace)})"
    return [PlanChange(change_ar=f"عدّلنا إيقاع اليوم {day.day_number} ليصير {_PACE_LABEL_AR.get(pace, pace)}")]


def _extend_days(plan: PlanObject) -> list[PlanChange]:
    new_day_number = plan.days[-1].day_number + 1 if plan.days else 1
    new_day = PlanDay(
        day_number=new_day_number, title_ar=f"يوم {new_day_number} (مُضاف)", day_cost_estimate=0.0,
        stops=[
            PlanStop(
                place_id=None, name_ar="يوم حر للاستكشاف الذاتي", name_en="Free day to explore on your own",
                stop_type="rest", arrival="09:00", departure="18:00", visit_duration_min=540,
                travel_from_prev_min=0, cost_estimate=0.0, why_here="يوم إضافي لتخفيف الإيقاع",
            )
        ],
    )
    plan.days.append(new_day)
    return [PlanChange(change_ar=f"أضفنا يومًا {new_day_number} لتخفيف إيقاع الخطة")]


def _shrink_days(plan: PlanObject) -> list[PlanChange]:
    if len(plan.days) <= 1:
        return [PlanChange(change_ar="ما ينفع نصغّر الخطة أكثر — لازم يوم واحد على الأقل")]
    removed_day = plan.days.pop()
    dropped_names = "، ".join(s.name_ar for s in removed_day.stops if s.place_id)
    msg = f"حذفنا اليوم {removed_day.day_number} بالكامل"
    if dropped_names:
        msg += f" (وبالتالي سقطت زيارة: {dropped_names})"
    return [PlanChange(change_ar=msg)]


_MODIFY_HANDLERS = {
    "remove": lambda plan, mod: (_remove_stop(plan, mod.target_place_id), []),
    "add": lambda plan, mod: (_add_stop(plan, mod), []),
    "replace_with_place": lambda plan, mod: (_replace_with_place(plan, mod), []),
    "replace_with_kind": lambda plan, mod: _replace_with_kind(plan, mod),
    "move_to_day": lambda plan, mod: (_move_to_day(plan, mod), []),
    "shift_time": lambda plan, mod: (_shift_time(plan, mod), []),
    "change_day_pace": lambda plan, mod: (_change_day_pace(plan, mod), []),
    "extend_days": lambda plan, mod: (_extend_days(plan), []),
    "shrink_days": lambda plan, mod: (_shrink_days(plan), []),
}


async def modify(plan_id: str, modification: Modification) -> Union[ModifyResponse, ErrorObject]:
    plan = _PLANS.get(plan_id)
    if plan is None:
        return ErrorObject(
            error=ErrorDetail(type="unsolvable", user_message=f"ما في خطة بهالمعرف {plan_id}")
        )

    handler = _MODIFY_HANDLERS.get(modification.type)
    if handler is None:
        return ErrorObject(error=ErrorDetail(type="unsolvable", user_message="نوع تعديل غير مدعوم"))

    changes, alternatives = handler(plan, modification)
    _PLANS[plan_id] = plan
    return ModifyResponse(plan=plan, changes=changes, alternatives=alternatives)


# ---------------------------------------------------------------------------
# 3.3 feasibility
# ---------------------------------------------------------------------------


async def feasibility(
    place_ids: list[str], duration_days: int, group_type: Optional[str] = None
) -> FeasibilityResponse:
    places = [p for p in (get_place(pid) for pid in place_ids) if p is not None]
    n = len(places)

    if duration_days <= 0:
        return FeasibilityResponse(
            verdict="unrealistic", reason_ar="عدد الأيام غير صالح",
            suggestion=FeasibilitySuggestion(action="extend_days", label_ar="حدد عدد أيام صحيح أولًا"),
        )

    avg = n / duration_days
    tight_threshold = 2.5
    unrealistic_threshold = 3.5
    if group_type in ("family", "large_group"):
        tight_threshold -= 0.5
        unrealistic_threshold -= 0.5

    if avg <= tight_threshold:
        return FeasibilityResponse(
            verdict="comfortable", reason_ar=f"{n} أماكن على {duration_days} أيام — إيقاع مريح", suggestion=None
        )
    if avg <= unrealistic_threshold:
        return FeasibilityResponse(
            verdict="tight",
            reason_ar=f"{n} أماكن متباعدة بـ{duration_days} أيام — ممكن لكن مرهق",
            suggestion=FeasibilitySuggestion(action="extend_days", label_ar="تمديد الخطة ليوم إضافي أريح بكثير"),
        )
    return FeasibilityResponse(
        verdict="unrealistic",
        reason_ar=f"{n} أماكن بـ{duration_days} أيام غير واقعي — يحتاج تقليصًا أو تمديدًا كبيرًا",
        suggestion=FeasibilitySuggestion(action="extend_days", label_ar="تمديد الخطة لأيام إضافية أو تقليل عدد الأماكن"),
    )
