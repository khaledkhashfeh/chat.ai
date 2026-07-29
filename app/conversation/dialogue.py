"""app/conversation/dialogue.py

[5] مدير الحوار — القلب: كل القرارات هون، وفق جدول "دليل قرار الاستدعاء"
بـ docs/spec.md §4 حرفيًا (وخارطة docs/plan.md المخطط 2).

قاعدة إلزامية (CLAUDE.md): كل مسار هنا يُحدّث ذاكرة العمل (WorkingMemory) قبل
إرجاع الرد. **لا نصوص واجهة هنا إطلاقًا** — كل نص يأتي حصرًا من
app.conversation.responses (respond وأخواتها).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
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
    DateRange,
    ErrorObject,
    Modification,
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

# ---------------------------------------------------------------------------
# تعديل المعلومات المجموعة (لا الخطة) — إضافة/إزالة وجهة أو اهتمام، وإعادة
# بدء الجمع من الصفر بتأكيد صريح قبل أي مسح (إجراء غير قابل للتراجع، spec.md §2.2).
# الإضافة تعمل أصلًا عبر الاستخراج العادي (ذِكر مدينة = تُضاف تراكميًا)؛ الجديد
# هنا هو الإزالة الصريحة والمسح الكامل بتأكيد.
# ---------------------------------------------------------------------------

_REMOVE_VERBS = ("شيل", "احذف", "الغي", "الغى", "بلاش", "remove", "cancel", "delete")
_RESET_TRIGGER_WORDS = (
    "خطه جديده", "خطة جديدة", "ابلش من جديد", "ابدا من جديد", "ابدأ من جديد",
    "امسح كل شي", "ابدا من الصفر", "خطه من الصفر", "start over", "new plan", "start fresh",
)
_CONFIRM_YES_WORDS = ("ايوا", "ايه", "اكيد", "تمام", "نعم", "yes", "sure", "confirm", "yeah", "yep", "ok")
_CONFIRM_NO_WORDS = ("لا", "لأ", "ما بدي", "بلاش", "خليها", "no", "cancel", "نو")


_PLAN_REFERENCE_WORDS = ("الخطه", "خطتي", "من الخطه", "plan")


def _find_removal_target(text_norm: str, state: ConversationState) -> Optional[tuple[str, str]]:
    """يفحص إن كانت الرسالة تطلب إزالة مدينة أو اهتمام **موجود فعليًا بالحالة**
    (لا أي مدينة مذكورة) — بفحص قرب فعل إزالة (شيل/الغي..) من اسمها (نافذة
    ~12 محرفًا، نفس أسلوب كشف النفي بـ context_extractor.py). يرجع أول مطابقة
    (الحقل، القيمة) أو None. هذا ما يمنع «شيل دمشق وضيف حلب» من إزالة حلب سهوًا:
    الفعل يجب أن يسبق اسم المدينة الصحيح تحديدًا ضمن نافذة قصيرة.

    استثناء مقصود: رسالة تذكر «الخطة» صراحة («شيل قلعة دمشق من الخطة») تعني
    التعديل على خطة **قائمة فعلًا** (modify_plan، اسم المكان لا المدينة نفسها
    قد يتداخل نصيًا مع اسم مدينة بالحالة) — نتنحّى ونترك التصنيف الطبيعي يتولاها."""
    if any(w in text_norm for w in _PLAN_REFERENCE_WORDS):
        return None
    for city in state.destination:
        for kw in entities_mod.CITIES.get(city, [city]):
            idx = text_norm.find(kw)
            if idx == -1:
                continue
            window = text_norm[max(0, idx - 12):idx]
            if any(v in window for v in _REMOVE_VERBS):
                return ("destination", city)
    for tag in state.interests:
        for keyword, mapped_tag in entities_mod.INTEREST_TAGS.items():
            if mapped_tag != tag:
                continue
            idx = text_norm.find(keyword)
            if idx == -1:
                continue
            window = text_norm[max(0, idx - 12):idx]
            if any(v in window for v in _REMOVE_VERBS):
                return ("interests", tag)
    return None


def _reset_conversation_state(state: ConversationState) -> None:
    """يعيد كل حقول الحالة لقيمها الافتراضية عدا اللغة (تبقى بلغة آخر رسالة).
    يعدّل state بالمكان (mutation) لا يستبدل الكائن — session.state يبقى نفس المرجع."""
    fresh = ConversationState(language=state.language)
    for field in ConversationState.model_fields:
        setattr(state, field, getattr(fresh, field))


def _reset_working_memory(memory: WorkingMemory) -> None:
    """نفس مبدأ _reset_conversation_state — تعديل بالمكان لا استبدال الكائن."""
    fresh = WorkingMemory()
    for field in WorkingMemory.model_fields:
        setattr(memory, field, getattr(fresh, field))


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
        if field in ("interests", "destination"):
            # تراكمي لا استبدال — «دمشق» ثم «وحلب كمان» = وجهتان معًا (قاعدة الوراثة)
            accumulated = getattr(state, field)
            setattr(state, field, list(dict.fromkeys([*accumulated, *value])))
        else:
            setattr(state, field, value)

    # تاريخ بدء الرحلة — اختياري تمامًا (لا يُسأل عنه أبدًا، يُستخرج فقط إن ذُكر
    # صراحة، نسبيًا أو صريحًا). نحدّث dates كاملًا (start+end) بمجرد معرفة البداية،
    # ونعيد حساب النهاية تلقائيًا إن تغيّرت المدة لاحقًا (state.duration_days موروث).
    start_date = entities_mod.find_start_date(text)
    if start_date or (found.get("duration_days") and state.dates):
        anchor = start_date or date.fromisoformat(state.dates.start)
        end = anchor + timedelta(days=(state.duration_days or 1) - 1)
        state.dates = DateRange(start=anchor.isoformat(), end=end.isoformat())

    # تأكيد إجراء مدمِّر معلَّق من الدور السابق (حاليًا: إعادة البدء) — يُفحص أولًا
    # ويُنهي الدور فورًا؛ لا ننفّذ مسحًا بلا تأكيد صريح (spec.md §2.2 الصراحة).
    if memory.pending_confirmation == "reset_plan":
        memory.pending_confirmation = None
        if any(w in text for w in _CONFIRM_YES_WORDS) and not any(w in text for w in _CONFIRM_NO_WORDS):
            _reset_conversation_state(state)
            state.language = language  # التصفير أعاد اللغة الحالية أصلًا، لكن تصريحًا لا ضرر
            _reset_working_memory(memory)
            memory.last_bot_action = "reset_confirmed"
            question = responses.gather_question_for("trip_purpose", language)
            return responses.respond("reset_confirmed", language, state=state, question=question)
        memory.last_bot_action = "reset_declined"
        return responses.respond("reset_declined", language, state=state)

    # إعادة بدء الجمع من الصفر — لا تُنفَّذ مباشرة (إجراء غير قابل للتراجع)،
    # بل تُطرح كسؤال تأكيد أولًا.
    if any(w in text for w in _RESET_TRIGGER_WORDS):
        memory.pending_confirmation = "reset_plan"
        memory.last_bot_action = "asked_reset_confirmation"
        return responses.respond("confirm_reset", language, state=state)

    # إزالة صريحة لوجهة أو اهتمام مذكورين سابقًا («شيل دمشق»، «الغي التاريخي») —
    # يُفحص قبل أي شي آخر لأن الاستخراج العادي أعلاه قد يكون "أضاف" المدينة
    # ذاتها للتو (ذِكرها بالنص لا يميّز وحده بين إضافة وإزالة؛ الفعل المرافق يميّز).
    removal = _find_removal_target(text, state)
    if removal:
        field, value = removal
        setattr(state, field, [v for v in getattr(state, field) if v != value])
        memory.last_bot_action = f"removed_{field}"
        label = value if field == "destination" else responses.tag_display_label(value, language)
        return responses.respond("removed_value", language, state=state, value=label)

    # هل قدّم المستخدم بهذا الدور حقلًا قابلًا للجمع؟ (لتصفير عدّاد التهرّب)
    made_progress = any(f in found for f in _GATHERABLE_FIELDS)

    # استئناف الجمع: الجواب المقتضب ("لحلب"، "مع عيلتي") قد يُصنَّف unclear أو
    # search، فنمتصّه ضمن المسار المعلَّق بدل تشتيته. نية غير سياحية واضحة (تفاصيل/
    # تعديل/رفض/ترحيب) تلغي التعليق (المستخدم بدّل الموضوع فعلًا).
    if memory.pending_intent == "recommend":
        if intent_label in ("unclear", "search"):
            intent_label = "search"
        else:
            # نية مختلفة (حتى build_plan) → بدّل المسار فعلًا؛ نصفّر last_asked_field
            # كي لا يُخطئ سؤال مستقبلي (بمسار جديد) فيظنّ نفسه إعادة سؤال لم يُفهَم.
            memory.pending_intent = None
            memory.last_asked_field = None
    elif memory.pending_intent == "build_plan":
        if intent_label in ("unclear", "search", "build_plan"):
            intent_label = "build_plan"
        else:
            memory.pending_intent = None
            memory.last_asked_field = None

    # سياج أمان (لا علاقة له بجلسة معلَّقة): المصنّف له ثغرات حتمية بصيغ لم
    # يتدرّب عليها (مثلًا "أريد الذهاب لدمشق" الفصحى مقابل "بدي روح ع دمشق"
    # العامية بالتدريب) فيرجع unclear رغم أن استخراج الكيانات نجح فعليًا. بدل
    # إهدار وجهة/اهتمام استُخرج هذا الدور بردّ عام لا يستخدمه، نعامل الرسالة
    # كبداية توصية — الفعل الافتراضي الأكثر أمانًا بمجال تطبيق سياحي بحت.
    # نتحقق من `found` (هذا الدور تحديدًا) لا `state` المتراكمة كي لا نخطف رسالة
    # غامضة لاحقة لا علاقة لها بالسفر لمجرد أن وجهة قديمة ما زالت بالحالة.
    # trip_purpose مضاف هنا أيضًا: جواب مقتضب على سؤاله ("بدي استجمام") غالبًا
    # يُصنَّف unclear بمفرده — يجب أن يُفهَم كبداية توصية لا رسالة غامضة عامة.
    elif intent_label == "unclear" and (
        found.get("destination") or found.get("interests") or found.get("trip_purpose")
    ):
        intent_label = "search"

    # [5] القرار (دليل قرار الاستدعاء — spec.md §4)
    if intent_label == "search":
        response = await _handle_search(state, memory, made_progress)
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

# ترتيب أولوية الجمع (الهدف → المدينة → التصنيفات → الميزانية → المجموعة [→ المدة للخطة])
# trip_purpose أولوية قصوى (docs/contract.md §1.1) — حقل جمع إلزامي دائمًا، مستقل
# عن group_type/interests (يصف دافع الرحلة لا تركيبتها ولا نوع أماكنها).
_GATHER_FOR_RECOMMEND = ("trip_purpose", "destination", "interests", "budget_level", "group_type")
_GATHER_FOR_PLAN = ("trip_purpose", "destination", "interests", "budget_level", "group_type", "duration_days")
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
        memory.last_asked_field = None
        return None
    if made_progress:
        memory.gather_asks = 0  # المستخدم تقدّم فعليًا → ليس تهرّبًا
    if memory.gather_asks >= _MAX_GATHER_ASKS:
        # نتابع بما توفّر (يُصرَّح بذلك بالرد) — نصفّر العدّاد فورًا (لا ننتظر
        # استدعاء أداة لاحقًا لتصفيره؛ هذه الطبقة لم تعد تستدعي أدوات هنا أصلًا)
        # كي لا يبقى عالقًا عند حدّه الأقصى لأي جمع مستقبلي.
        memory.gather_asks = 0
        memory.pending_intent = None
        memory.last_asked_field = None
        return None
    memory.gather_asks += 1
    memory.pending_intent = pending_tag
    memory.last_bot_action = "asked_missing_info"
    field = _first_missing_gather(state, fields)
    question = responses.gather_question_for(field, state.language)
    # نفس الحقل يُعاد سؤاله ولم يقدّم المستخدم أي معلومة جديدة هذا الدور → جوابه
    # لم يُفهَم فعلًا (لا مجرد إجابة حقل آخر بترتيب مختلف) — نصرّح بذلك صراحة
    # بدل تكرار نفس السؤال حرفيًا بصمت (طلب المالك: "يعيد السؤال ان لم يفهم").
    repeated_unresolved = field == memory.last_asked_field and not made_progress
    memory.last_asked_field = field
    template = "ask_again" if repeated_unresolved else "ask"
    return responses.respond(template, state.language, state=state, question=question)


# ---------------------------------------------------------------------------
# search — اجمع (مدينة + تصنيفات + ميزانية + مجموعة) ثم **توقّف**.
#
# قرار المالك الصريح: عرض الأماكن ليس شغل هذه الطبقة — بمجرد اكتمال الجمع
# تؤكّد الطبقة الجاهزية فقط، بلا استدعاء recommender.search ولا بطاقات. الحالة
# المتراكمة (ConversationState) هي بالضبط ما "سيُرسَل" لطبقة التوصية لاحقًا —
# تُعرض بقسم منفصل بواجهة الاختبار (غير مرتبط فعليًا بعد)، لا داخل رد الشات.
# ---------------------------------------------------------------------------

_TARGET_WORD = {"recommend": ("طلبك", "your request"), "build_plan": ("رحلتك", "your trip")}


async def _handle_search(
    state: ConversationState, memory: WorkingMemory, made_progress: bool = False
) -> ChatResponse:
    lang = state.language

    ask = _gather_step(state, memory, _GATHER_FOR_RECOMMEND, made_progress, "recommend")
    if ask is not None:
        return ask

    proceeded_partial = _first_missing_gather(state, _GATHER_FOR_RECOMMEND) is not None
    target = _TARGET_WORD["recommend"][0 if lang == "ar" else 1]
    memory.last_bot_action = "gathered_info_partial" if proceeded_partial else "gathered_info"
    key = "gathered_partial" if proceeded_partial else "gathered_ready"
    return responses.respond(key, lang, state=state, target=target)


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


async def _handle_build_plan(
    state: ConversationState, memory: WorkingMemory, made_progress: bool = False
) -> ChatResponse:
    lang = state.language

    ask = _gather_step(state, memory, _GATHER_FOR_PLAN, made_progress, "build_plan")
    if ask is not None:
        return ask

    proceeded_partial = _first_missing_gather(state, _GATHER_FOR_PLAN) is not None
    target = _TARGET_WORD["build_plan"][0 if lang == "ar" else 1]
    memory.last_bot_action = "gathered_info_partial" if proceeded_partial else "gathered_info"
    key = "gathered_partial" if proceeded_partial else "gathered_ready"
    return responses.respond(key, lang, state=state, target=target)


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
