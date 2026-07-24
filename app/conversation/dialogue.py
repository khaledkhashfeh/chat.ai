"""app/conversation/dialogue.py

[5] مدير الحوار — القلب: كل القرارات هون، وفق جدول "دليل قرار الاستدعاء"
بـ docs/spec.md §4 حرفيًا (وخارطة docs/plan.md المخطط 2).

قاعدة إلزامية (CLAUDE.md): كل مسار هنا يُحدّث ذاكرة العمل (WorkingMemory) قبل
إرجاع الرد. **لا نصوص واجهة هنا إطلاقًا** — كل نص يأتي حصرًا من
app.conversation.responses (respond وأخواتها).
"""
from __future__ import annotations

import re
from typing import Optional

from app.conversation import entities as entities_mod
from app.conversation import responses
from app.conversation.intent import REFERENCE_TAG, classify
from app.conversation.normalizer import detect_language, normalize
from app.conversation.resolver import (
    extract_ordinal_place_ids,
    resolve_place_by_name,
    resolve_references,
)
from app.conversation.session import SessionData
from app.mocks import planner, recommender
from app.mocks.data import get_place
from app.shared.models import (
    ChatResponse,
    ConversationState,
    ErrorObject,
    Modification,
    RecommendedPlaceRef,
    WorkingMemory,
)

# قاموس أفعال التعديل (spec.md §4 صف modify_plan) — يُفحص بهذا الترتيب تحديدًا
# كي لا تسبق كلمة عامة كلمة أكثر تحديدًا (مثلًا extend_days قبل add العامة).
_MODIFY_ACTION_KEYWORDS: dict[str, list[str]] = {
    "remove": ["شيل", "احذف", "الغي", "remove", "delete"],
    "extend_days": ["مدد الخطة", "زود يوم", "extend"],
    "shrink_days": ["قصر الخطة", "قلل يوم", "shrink"],
    "replace": ["بدل", "غير مكان", "استبدل", "replace"],
    "move": ["انقل", "حط بيوم", "move"],
    "shift_time": ["قدم", "اخر", "ابكر", "earlier", "later", "shift"],
    "change_pace": ["خفف", "ريح الجدول", "pace"],
    "add": ["ضيف", "زود", "add"],
}
_MODIFY_ACTION_ORDER = [
    "remove", "extend_days", "shrink_days", "replace", "move", "shift_time", "change_pace", "add",
]

_DAY_NUMBER_PATTERN = re.compile(r"(?:يوم|day)\D{0,4}(\d)")


def _detect_modify_action(text_norm: str) -> Optional[str]:
    for action in _MODIFY_ACTION_ORDER:
        if any(kw in text_norm for kw in _MODIFY_ACTION_KEYWORDS[action]):
            return action
    return None


def _extract_day_number(text_norm: str) -> Optional[int]:
    match = _DAY_NUMBER_PATTERN.search(text_norm)
    return int(match.group(1)) if match else None


async def handle_turn(session: SessionData, raw_text: str, user_id: str) -> ChatResponse:
    """نقطة الدخول: يعالج رسالة واحدة، يُحدّث session.state و session.memory
    بالمكان (mutation)، ويرجع الرد المنظم. main.py مسؤول عن الحفظ بعد النداء."""
    state = session.state
    memory = session.memory

    # [1] التطبيع + كشف اللغة — الرد دائمًا بلغة آخر رسالة (spec.md §2.1)
    language = detect_language(raw_text)
    state.language = language
    text = normalize(raw_text)

    # [2] حل الإشارات — قبل تصنيف النية (قرار معماري مقصود)
    ref = resolve_references(text, memory)
    text_for_intent = f"{text} {REFERENCE_TAG}" if ref else text

    # [3] تصنيف النية
    intent_label, _confidence = classify(text_for_intent)

    # [4] استخراج الكيانات → تحديث الحالة (تراكمي — قاعدة الوراثة، لا يُنسى)
    found = entities_mod.extract_entities(text)
    for field, value in found.items():
        if field == "interests":
            state.interests = list(dict.fromkeys([*state.interests, *value]))
        else:
            setattr(state, field, value)

    # هل قدّم المستخدم بهذا الدور حقلًا قابلًا للجمع؟ (لتصفير عدّاد التهرّب)
    made_progress = any(f in found for f in _GATHERABLE_FIELDS)

    # استئناف الجمع: الجواب المقتضب ("لحلب"، "مع عيلتي") قد يُصنَّف unclear أو
    # search، فنمتصّه ضمن المسار المعلَّق بدل تشتيته. نية غير سياحية واضحة (تفاصيل/
    # تعديل/رفض/ترحيب) تلغي التعليق (المستخدم بدّل الموضوع فعلًا).
    if memory.pending_intent == "recommend":
        if intent_label in ("unclear", "search"):
            intent_label = "search"
        else:
            memory.pending_intent = None  # نية مختلفة (حتى build_plan) → بدّل المسار
    elif memory.pending_intent == "build_plan":
        if intent_label in ("unclear", "search", "build_plan"):
            intent_label = "build_plan"
        else:
            memory.pending_intent = None

    # [5] القرار (دليل قرار الاستدعاء — spec.md §4)
    if intent_label == "search":
        response = await _handle_search(text, state, memory, user_id, made_progress)
    elif intent_label == "details":
        response = await _handle_details(text, ref, state, memory)
    elif intent_label == "compare":
        response = await _handle_compare(text, ref, state, memory)
    elif intent_label == "build_plan":
        response = await _handle_build_plan(state, memory, made_progress)
    elif intent_label == "modify_plan":
        response = await _handle_modify_plan(text, ref, state, memory)
    elif intent_label == "add_to_plan":
        response = await _handle_add_to_plan(ref, state, memory, user_id)
    elif intent_label == "reject":
        response = await _handle_reject(state, memory, user_id)
    elif intent_label == "greeting_thanks":
        response = await _handle_greeting(state, memory, user_id)
    elif intent_label == "out_of_scope":
        memory.last_bot_action = "out_of_scope"
        response = responses.respond("out_of_scope", language, state=state)
    else:  # unclear
        response = _handle_unclear(state, memory)

    return response


# ---------------------------------------------------------------------------
# نمط الجمع الموحّد: «اجمع الحقول أولًا ثم استدعِ» (قرار المالك) — سؤال واحد لكل
# دور بترتيب الأولوية، مع استئناف الأجوبة المقتضبة (pending_intent) وسقف تهرّب.
# ---------------------------------------------------------------------------

# ترتيب أولوية الجمع (المدينة → التصنيفات → الميزانية → المجموعة [→ المدة للخطة])
_GATHER_FOR_RECOMMEND = ("destination", "interests", "budget_level", "group_type")
_GATHER_FOR_PLAN = ("destination", "interests", "budget_level", "group_type", "duration_days")
_GATHERABLE_FIELDS = _GATHER_FOR_PLAN  # المجموعة الأشمل (لكشف التقدّم)
_MAX_GATHER_ASKS = 2  # بعد تهرّبين متتاليين نتابع بما توفّر


def _first_missing_gather(state: ConversationState, fields: tuple[str, ...]) -> Optional[str]:
    for f in fields:
        value = getattr(state, f)
        if isinstance(value, list):
            if not value:
                return f
        elif not value:
            return f
    return None


def _gather_step(
    state: ConversationState,
    memory: WorkingMemory,
    fields: tuple[str, ...],
    made_progress: bool,
    pending_tag: str,
) -> Optional[ChatResponse]:
    """يسأل عن أول حقل ناقص (سؤال واحد) ويرجّع الرد، أو None حين يكتمل الجمع أو
    يُستنفَد سقف التهرّب (عندها يتابع المستدعي بما توفّر). يُدير gather_asks
    و pending_intent مركزيًا."""
    if _first_missing_gather(state, fields) is None:
        memory.gather_asks = 0
        memory.pending_intent = None
        return None
    if made_progress:
        memory.gather_asks = 0  # المستخدم تقدّم فعليًا → ليس تهرّبًا
    if memory.gather_asks >= _MAX_GATHER_ASKS:
        memory.pending_intent = None  # نتابع بما توفّر (يُصرَّح بذلك في الرد)
        return None
    memory.gather_asks += 1
    memory.pending_intent = pending_tag
    memory.last_bot_action = "asked_missing_info"
    field = _first_missing_gather(state, fields)
    question = responses.gather_question_for(field, state.language)
    return responses.respond("ask", state.language, state=state, question=question)


# ---------------------------------------------------------------------------
# search — اجمع (مدينة + تصنيفات + ميزانية + مجموعة) ثم استدعِ التوصية
# ---------------------------------------------------------------------------


async def _handle_search(
    text: str, state: ConversationState, memory: WorkingMemory, user_id: str, made_progress: bool = False
) -> ChatResponse:
    lang = state.language

    ask = _gather_step(state, memory, _GATHER_FOR_RECOMMEND, made_progress, "recommend")
    if ask is not None:
        return ask
    proceeded_partial = _first_missing_gather(state, _GATHER_FOR_RECOMMEND) is not None

    result = await recommender.search(query=text, state=state, top_k=8)

    if not result.results:
        memory.last_bot_action = "search_no_results"
        reason = result.fallback.reason if result.fallback else ""
        return responses.respond("no_results", lang, state=state, reason=reason)

    memory.last_recommendations = [
        RecommendedPlaceRef(pos=i + 1, place_id=c.place_id, name_ar=c.name_ar, name_en=c.name_en)
        for i, c in enumerate(result.results)
    ]
    memory.last_bot_action = "showed_recommendations"
    key = "show_places_partial" if proceeded_partial else "show_places"
    return responses.respond(key, lang, state=state, cards=result.results, n=len(result.results))


# ---------------------------------------------------------------------------
# details — لا يستدعي التخطيط ولا يفتح أسئلته أبدًا (قاعدة حاسمة بـ spec.md §4)
# ---------------------------------------------------------------------------


async def _handle_details(text: str, ref: dict, state: ConversationState, memory: WorkingMemory) -> ChatResponse:
    lang = state.language
    # الأولوية: إشارة محلولة من السياق → آخر مكان مذكور → بحث بالاسم بالكتالوج
    # الكامل (يجعل «معلومات عن قلعة حلب» يعمل من أول رسالة بلا سياق سابق).
    pid = ref.get("place_id") or memory.last_mentioned_place or resolve_place_by_name(text)
    if not pid:
        memory.last_bot_action = "asked_which_place"
        return responses.respond("which_place", lang, state=state)

    result = await recommender.details(place_id=pid, language=lang)
    if isinstance(result, ErrorObject):
        return responses.respond("tool_error", lang, state=state, message=result.error.user_message)

    memory.last_mentioned_place = pid
    memory.last_bot_action = "showed_details"

    plan_note = ""
    if memory.current_plan:
        for day in memory.current_plan.days:
            for stop in day.stops:
                if stop.place_id == pid:
                    plan_note = (
                        f"وهو بيومك {day.day_number} الساعة {stop.arrival}."
                        if lang == "ar"
                        else f"It's on day {day.day_number} of your plan at {stop.arrival}."
                    )
                    break

    return responses.details_reply(result, lang, state, plan_link_note=plan_note)


# ---------------------------------------------------------------------------
# compare — لا تخطيط أبدًا
# ---------------------------------------------------------------------------


async def _handle_compare(text: str, ref: dict, state: ConversationState, memory: WorkingMemory) -> ChatResponse:
    lang = state.language
    target_ids = extract_ordinal_place_ids(text, memory.last_recommendations)

    if len(target_ids) < 2 and ref.get("place_id") and ref["place_id"] not in target_ids:
        target_ids.append(ref["place_id"])
    if len(target_ids) < 2 and memory.last_mentioned_place and memory.last_mentioned_place not in target_ids:
        target_ids.append(memory.last_mentioned_place)

    if len(target_ids) < 2:
        memory.last_bot_action = "asked_which_places_to_compare"
        return responses.respond("which_places_to_compare", lang, state=state)

    result = await recommender.compare(place_ids=target_ids[:4], state=state, language=lang)
    if isinstance(result, ErrorObject):
        return responses.respond("tool_error", lang, state=state, message=result.error.user_message)

    memory.last_bot_action = "showed_comparison"
    winner = next((p for p in result.places if p.place_id == result.verdict.winner_place_id), None)
    winner_name = responses.place_display_name(winner.name_ar, winner.name_en, lang) if winner else ""
    return responses.respond("compare_result", lang, state=state, comparison=result, winner=winner_name)


# ---------------------------------------------------------------------------
# build_plan
# ---------------------------------------------------------------------------


# قيم افتراضية للحد الأدنى الصلب الذي تحتاجه أداة البناء (لا اختراع أماكن — بارامترات رحلة فقط)
_DEFAULT_DESTINATION = "دمشق"
_DEFAULT_DURATION_DAYS = 2
_DEFAULT_GROUP_TYPE = "solo"


async def _handle_build_plan(
    state: ConversationState, memory: WorkingMemory, made_progress: bool = False
) -> ChatResponse:
    lang = state.language

    ask = _gather_step(state, memory, _GATHER_FOR_PLAN, made_progress, "build_plan")
    if ask is not None:
        return ask
    used_defaults = _first_missing_gather(state, _GATHER_FOR_PLAN) is not None

    # ضمان الحد الأدنى الصلب لأداة البناء عند التهرّب (dest+duration+group)
    if not state.destination:
        state.destination = [_DEFAULT_DESTINATION]
    if not state.duration_days:
        state.duration_days = _DEFAULT_DURATION_DAYS
    if not state.group_type:
        state.group_type = _DEFAULT_GROUP_TYPE

    if state.saved_place_ids:
        # المستخدم اختار أماكنه بنفسه → feasibility أولًا (spec.md §4)
        feas = await planner.feasibility(
            place_ids=state.saved_place_ids, duration_days=state.duration_days, group_type=state.group_type
        )
        if feas.verdict == "unrealistic":
            memory.last_bot_action = "feasibility_warning"
            return responses.infeasible_reply(feas, lang, state)
        plan_result = await planner.build(state=state, mandatory_place_ids=state.saved_place_ids)
    else:
        plan_result = await planner.build(state=state)

    if isinstance(plan_result, ErrorObject):
        return responses.respond("tool_error", lang, state=state, message=plan_result.error.user_message)

    memory.current_plan = plan_result
    state.current_plan_id = plan_result.plan_id
    memory.gather_asks = 0
    memory.pending_intent = None  # انتهى جمع الخطة
    summary = plan_result.summary_ar if lang == "ar" else plan_result.summary_en
    if used_defaults:
        memory.last_bot_action = "showed_plan_with_defaults"
        return responses.respond("plan_ready_defaults", lang, state=state, plan=plan_result, summary=summary)
    memory.last_bot_action = "showed_plan"
    return responses.respond("plan_ready", lang, state=state, plan=plan_result, summary=summary)


# ---------------------------------------------------------------------------
# modify_plan — جراحي: يمس اليوم/المحطة المتأثرة فقط
# ---------------------------------------------------------------------------


async def _handle_modify_plan(text: str, ref: dict, state: ConversationState, memory: WorkingMemory) -> ChatResponse:
    lang = state.language
    if not memory.current_plan:
        memory.last_bot_action = "no_plan"
        return responses.respond("no_plan_yet", lang, state=state)

    action = _detect_modify_action(text)
    if action is None:
        memory.last_bot_action = "asked_which_change"
        return responses.respond("which_change", lang, state=state)

    target_pid = ref.get("place_id")
    needs_target = action in ("remove", "replace", "move")
    if needs_target and not target_pid:
        memory.last_bot_action = "asked_which_place_in_plan"
        return responses.respond("which_place_in_plan", lang, state=state)

    modification = _build_modification(action, target_pid, text, memory)
    result = await planner.modify(plan_id=memory.current_plan.plan_id, modification=modification)
    if isinstance(result, ErrorObject):
        return responses.respond("tool_error", lang, state=state, message=result.error.user_message)

    memory.current_plan = result.plan
    memory.last_bot_action = "updated_plan"
    return responses.plan_updated_reply(result.changes, result.plan, lang, state)


def _build_modification(action: str, target_pid: Optional[str], text: str, memory: WorkingMemory) -> Modification:
    if action == "remove":
        return Modification(type="remove", target_place_id=target_pid)

    if action == "replace":
        kind_tags = entities_mod.extract_entities(text).get("interests")
        if not kind_tags and target_pid:
            place = get_place(target_pid)
            kind_tags = list(place["tags"]) if place else []
        return Modification(type="replace_with_kind", target_place_id=target_pid, params={"kind_tags": kind_tags or []})

    if action == "move":
        day_number = _extract_day_number(text) or 1
        return Modification(type="move_to_day", target_place_id=target_pid, params={"day_number": day_number})

    if action == "shift_time":
        minutes = -30 if any(w in text for w in ("قدم", "ابكر", "earlier")) else 30
        params: dict = {"minutes": minutes}
        if not target_pid:
            params["stop_type"] = "meal"
        return Modification(type="shift_time", target_place_id=target_pid, params=params)

    if action == "change_pace":
        day_number = _extract_day_number(text) or 1
        pace = "relaxed" if any(w in text for w in ("خفف", "ريح")) else "moderate"
        return Modification(
            type="change_day_pace", target_place_id=None, params={"day_number": day_number, "pace": pace}
        )

    if action == "extend_days":
        return Modification(type="extend_days", target_place_id=None, params={})

    if action == "shrink_days":
        return Modification(type="shrink_days", target_place_id=None, params={})

    # add
    place_id = target_pid or memory.last_mentioned_place
    day_number = _extract_day_number(text) or 1
    return Modification(type="add", target_place_id=place_id, params={"place_id": place_id, "day_number": day_number})


# ---------------------------------------------------------------------------
# add_to_plan
# ---------------------------------------------------------------------------


async def _handle_add_to_plan(ref: dict, state: ConversationState, memory: WorkingMemory, user_id: str) -> ChatResponse:
    lang = state.language
    pid = ref.get("place_id") or memory.last_mentioned_place
    if not pid:
        memory.last_bot_action = "asked_which_place"
        return responses.respond("which_place", lang, state=state)

    if pid not in state.saved_place_ids:
        state.saved_place_ids.append(pid)

    await recommender.log(user_id=user_id, place_id=pid, event="added_to_plan", source="chat")  # غير متزامن منطقيًا

    memory.last_mentioned_place = pid
    memory.last_bot_action = "added_to_plan"

    place = get_place(pid)
    name = responses.place_display_name(place["name_ar"], place["name_en"], lang) if place else pid
    return responses.respond("added_to_plan", lang, state=state, name=name)


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


async def _handle_reject(state: ConversationState, memory: WorkingMemory, user_id: str) -> ChatResponse:
    lang = state.language
    for r in memory.last_recommendations:
        await recommender.log(user_id=user_id, place_id=r.place_id, event="pass", source="chat")
        if r.place_id not in state.excluded_place_ids:
            state.excluded_place_ids.append(r.place_id)

    memory.last_bot_action = "asked_rejection_reason"
    return responses.respond("diagnose_rejection", lang, state=state)


# ---------------------------------------------------------------------------
# greeting_thanks
# ---------------------------------------------------------------------------


async def _handle_greeting(state: ConversationState, memory: WorkingMemory, user_id: str) -> ChatResponse:
    lang = state.language
    profile = await recommender.profile(user_id=user_id)
    memory.last_bot_action = "greeted"
    return responses.build_greeting(profile, lang, state)


# ---------------------------------------------------------------------------
# unclear — توضيح مستنتج من السياق، ممنوع "ما فهمت" الجافة
# ---------------------------------------------------------------------------


def _handle_unclear(state: ConversationState, memory: WorkingMemory) -> ChatResponse:
    lang = state.language
    memory.last_bot_action = "asked_clarification"
    if memory.current_plan:
        return responses.respond("unclear_plan_context", lang, state=state)
    if memory.last_recommendations:
        return responses.respond("unclear_recs_context", lang, state=state)
    return responses.respond("unclear_generic", lang, state=state)
