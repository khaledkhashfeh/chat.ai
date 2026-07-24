"""app/conversation/responses.py

[6] مولّد الرد — القالب الوحيد المسموح بكتابة نصوص معروضة للمستخدم فيه
(CLAUDE.md: "النصوص المعروضة للمستخدم في responses.py فقط"). dialogue.py لا
يكتب أي نص مباشرة — يستدعي respond(key, language, **kwargs) دائمًا.

مبدأ توزيع الثقل (docs/spec.md §2.5): النص قصير (سطر-سطران)، والبطاقات/الخطة
تحملان الثقل الفعلي. كل رد ينتهي بخطوة تالية ضمنية عبر صيغة السؤال/الاقتراح.
"""
from __future__ import annotations

import random
from typing import Any, Optional

from app.shared.models import (
    ChatResponse,
    CompareResponse,
    ConversationState,
    FeasibilityResponse,
    PlaceCard,
    PlaceDetails,
    PlanChange,
    PlanObject,
    ProfileResponse,
)

# سؤال واحد لكل حقل ناقص عند بناء خطة (أولوية: destination ثم duration_days ثم group_type)
QUESTIONS: dict[str, dict[str, str]] = {
    "ar": {
        "destination": "لوين ناوي تروح؟",
        "duration_days": "كم يوم معك للرحلة؟",
        "group_type": "رايح لحالك، مع العيلة، ولا مع أصحاب؟",
    },
    "en": {
        "destination": "Where would you like to go?",
        "duration_days": "How many days do you have?",
        "group_type": "Traveling solo, with family, or friends?",
    },
}

# أسئلة إثراء search — تُطرح بعد العرض لا قبل الاستدعاء (أولوية: group_type ثم
# budget_level ثم dates)، وسؤال واحد فقط لكل جلسة بحث (memory.search_enrichment_asked).
ENRICHMENT_QUESTIONS: dict[str, dict[str, str]] = {
    "ar": {
        "group_type": "رايح لحالك، مع العيلة، ولا مع أصحاب؟",
        "budget_level": "بتفضل خيارات اقتصادية، ولا مرتاح أكتر بالميزانية؟",
        "dates": "عندك تواريخ محددة بخاطرك للرحلة؟",
    },
    "en": {
        "group_type": "Traveling solo, with family, or with friends?",
        "budget_level": "Prefer budget-friendly options, or something fancier?",
        "dates": "Do you have specific dates in mind?",
    },
}

T: dict[str, dict[str, list[str]]] = {
    "show_places": {
        "ar": ["لقيتلك {n} أماكن حلوة 👇 شو رأيك فيهن؟", "هي أفضل {n} خيارات — بتحب تعرف أكتر عن حدا منهن؟"],
        "en": ["Found {n} great options 👇 What do you think?", "Here are the top {n} — want details on any of them?"],
    },
    "show_places_ask": {
        "ar": ["لقيتلك {n} أماكن حلوة 👇 {question}", "هي أفضل {n} خيارات 👆 {question}"],
        "en": ["Found {n} great options 👇 {question}", "Here are the top {n} 👆 {question}"],
    },
    "no_results": {
        "ar": ["{reason}. بتحب نوسّع البحث؟", "ما لقيت شي مطابق تمامًا — {reason}."],
        "en": ["{reason}. Want me to widen the search?", "Nothing matched exactly — {reason}."],
    },
    "place_details": {
        "ar": ["هاد كل يلي عندي عن {name} 👆 شو رأيك، بتحب تضيفو للخطة؟", "تفاصيل {name} فوق 👆 في شي تاني بدك تعرفو؟"],
        "en": ["Here's everything I have on {name} 👆 Want to add it to your plan?", "Details for {name} above 👆 Anything else you'd like to know?"],
    },
    "which_place": {
        "ar": ["عن أي مكان بالضبط؟", "قصدك مين من يلي حكينا عنهن؟"],
        "en": ["Which place exactly?", "Which one did you mean?"],
    },
    "compare_result": {
        "ar": ["قارنتلك بينهن 👆 وبرأيي {winner} الأنسب، بس القرار إلك", "هاي المقارنة 👆 شخصيًا بميل لـ{winner}، وإنت شو رأيك؟"],
        "en": ["Here's the comparison 👆 I'd lean towards {winner}, but it's your call", "Compared them above 👆 {winner} looks like the strongest pick — what do you think?"],
    },
    "which_places_to_compare": {
        "ar": ["بين أي مكانين بالضبط بدك تقارن؟", "قصدك تقارن بين مين ومين؟"],
        "en": ["Which two places would you like to compare?", "Which ones exactly should I compare?"],
    },
    "ask": {
        "ar": ["{question}", "بس بدي اعرف كمان شي: {question}"],
        "en": ["{question}", "Just one more thing: {question}"],
    },
    "plan_ready": {
        "ar": ["جهزتلك الخطة: {summary} 👆 شو رأيك فيها؟", "هاي خطتك: {summary} — في شي بدك تعدلو؟"],
        "en": ["Here's your plan: {summary} 👆 What do you think?", "Your plan is ready: {summary} — anything you'd like to adjust?"],
    },
    "plan_ready_defaults": {
        "ar": ["رح جيبلك اقتراحات عامة هلأ، وفينا نضبطها أكتر بعدين: {summary} 👆", "بما إنه ما توضحت التفاصيل، جهزتلك خطة عامة: {summary} 👆 وفينا نعدلها متل ما بدك"],
        "en": ["I'll go with general suggestions for now, and we can fine-tune later: {summary} 👆", "Since the details weren't clear, here's a general plan: {summary} 👆 we can adjust it anytime"],
    },
    "infeasible": {
        "ar": ["{reason}. {suggestion}", "بصراحة، {reason}. {suggestion}"],
        "en": ["{reason}. {suggestion}", "Honestly, {reason}. {suggestion}"],
    },
    "no_plan_yet": {
        "ar": ["ما في خطة لسا — بدك نبني وحدة؟", "لسا ما عنا خطة جاهزة، رح نعملها سوا؟"],
        "en": ["There's no plan yet — want to build one?", "We don't have a plan yet, shall we make one?"],
    },
    "which_place_in_plan": {
        "ar": ["أي واحد من أماكن الخطة تقصد بالضبط؟", "قصدك مين من محطات خطتك الحالية؟"],
        "en": ["Which stop in your plan do you mean exactly?", "Which one of your plan's places did you mean?"],
    },
    "which_change": {
        "ar": ["شو بالضبط بدك تغير بالخطة؟", "وضّحلي أكتر شو التعديل يلي بدك ياه؟"],
        "en": ["What exactly would you like to change in the plan?", "Could you clarify what change you'd like?"],
    },
    "plan_updated": {
        "ar": ["تم ✅ {change_note} — في شي تاني بدك تعدلو؟", "خلص ✅ {change_note}. شو رأيك فيها هلق؟"],
        "en": ["Done ✅ {change_note} — anything else to adjust?", "All set ✅ {change_note}. How does it look now?"],
    },
    "added_to_plan": {
        "ar": ["تمت إضافة {name} ✅ في شي تاني بدك تضيفو؟", "ضفتلك {name} عالخطة ✅ كمّل معايا؟"],
        "en": ["Added {name} ✅ Anything else to add?", "{name} is now saved ✅ Want to keep going?"],
    },
    "diagnose_rejection": {
        "ar": ["تمام، خليني افهم أكتر: شو يلي ما ناسبك — النوعية، البعد، ولا شي تاني؟", "حلو عرفني بس: شو المشكلة بالضبط بيلي عرضتن — النوع، المسافة، ولا غيرها؟"],
        "en": ["Got it — what didn't work: the type of places, the distance, or something else?", "No problem — what was off about them: category, distance, or something else?"],
    },
    "greeting_new": {
        "ar": ["أهلًا فيك! 👋 بحب ساعدك تلاقي أحلى الأماكن بسوريا — وين ناوي تروح؟", "مرحبا! جاهز اقترحلك رحلة حلوة — شو بخاطرك؟"],
        "en": ["Welcome! 👋 I can help you find great places in Syria — where are you thinking of going?", "Hi there! Ready to plan something nice — what's on your mind?"],
    },
    "greeting_returning": {
        "ar": ["أهلا فيك من جديد! {last_activity_note} — نكمل منين ما وقفنا؟", "مرحبا رجعت! {last_activity_note}. شو رأيك نكمل؟"],
        "en": ["Welcome back! {last_activity_note} — shall we continue where we left off?", "Hey, good to see you again! {last_activity_note}."],
    },
    "out_of_scope": {
        "ar": ["هاد خارج تخصصي، أنا متخصص بالسياحة داخل سوريا بس. بس لو ناوي رحلة بسوريا بساعدك فيها! 😊", "ما بقدر ساعدك بهالطلب — تخصصي السياحة بسوريا فقط. في وجهة سورية ببالك؟"],
        "en": ["That's outside what I do — I specialize in tourism within Syria. But if you're planning a Syria trip, I'm here to help! 😊", "I can't help with that — I only cover travel within Syria. Have a Syrian destination in mind?"],
    },
    "unclear_plan_context": {
        "ar": ["ما وضحتلي تمامًا 🙂 بتقصد تعدل عالخطة الحالية؟", "مش متأكد شو قصدك — عم تحكي عن تعديل بالخطة؟"],
        "en": ["I didn't quite catch that 🙂 Did you mean you'd like to adjust the current plan?", "Not sure I follow — are you referring to a change in the plan?"],
    },
    "unclear_recs_context": {
        "ar": ["ما وضحتلي تمامًا 🙂 بتحب تعرف أكتر عن حدا من الأماكن يلي عرضتلك ياهن؟", "مش متأكد شو قصدك — تقصد سؤال عن واحد من الاقتراحات؟"],
        "en": ["I didn't quite catch that 🙂 Would you like more details on one of the places I showed you?", "Not sure I follow — are you asking about one of the suggestions?"],
    },
    "unclear_generic": {
        "ar": ["ما فهمت قصدك تمامًا 🙂 بتحب اقترحلك أماكن، ولا نبني خطة رحلة؟", "ممكن توضح أكتر؟ بقدر اقترح أماكن أو أساعدك تخطط رحلة."],
        "en": ["I didn't quite get that 🙂 Would you like some place suggestions, or shall we build a trip plan?", "Could you clarify a bit? I can suggest places or help plan a trip."],
    },
    "tool_error": {
        "ar": ["{message}", "للأسف، {message}"],
        "en": ["{message}", "Unfortunately, {message}"],
    },
}


def _pick(key: str, language: str) -> str:
    variants = T[key][language]
    return random.choice(variants)


def respond(
    key: str,
    language: str,
    state: ConversationState,
    cards: Optional[list[PlaceCard]] = None,
    plan: Optional[PlanObject] = None,
    comparison: Optional[CompareResponse] = None,
    **kwargs: Any,
) -> ChatResponse:
    """يبني الرد المنظم (docs/spec.md §2.5) من قالب `key` بلغة `language`."""
    template = _pick(key, language)
    format_kwargs = {k: v for k, v in kwargs.items() if isinstance(v, (str, int, float))}
    try:
        text = template.format(**format_kwargs)
    except KeyError:
        text = template
    return ChatResponse(reply=text.strip(), cards=cards, plan=plan, comparison=comparison, state=state)


def question_for_missing_field(field: str, language: str) -> str:
    return QUESTIONS[language][field]


def enrichment_question_for(field: str, language: str) -> str:
    return ENRICHMENT_QUESTIONS[language][field]


# أسئلة توضيح عقد conversation_context_v1 عند نقص معلومة إلزامية (للعرض عبر Laravel).
# كل مفتاح نقص له صيغة، وحالة مركّبة واحدة (الموقع + الاهتمامات معًا) بسؤال واحد
# التزامًا بقاعدة "سؤال واحد في الرد".
CONTEXT_CLARIFY: dict[str, dict[str, str]] = {
    "ar": {
        "governorate_or_city": "بأي محافظة أو مدينة ناوي تروح؟",
        "trip_interests": "شو نوع الأماكن يلي بتحبها — تاريخية، طبيعية، بحر، أكل...؟",
        "duration": "قديش مدة رحلتك؟",
        "group_type": "رايح لحالك، مع العيلة، ولا مع أصحاب؟",
        "governorate_or_city+trip_interests": "بأي محافظة رح تكون، وشو نوع الأماكن يلي بتفضلها؟",
    },
    "en": {
        "governorate_or_city": "Which governorate or city are you heading to?",
        "trip_interests": "What kind of places do you like — historical, nature, sea, food…?",
        "duration": "How long is your trip?",
        "group_type": "Traveling solo, with family, or with friends?",
        "governorate_or_city+trip_interests": "Which governorate will you be in, and what kind of places do you prefer?",
    },
}


def context_clarification(missing: list[str], language: str) -> Optional[str]:
    """يبني سؤال توضيح واحدًا من قائمة النواقص (أعلى أولوية أولًا). يدمج نقص
    الموقع والاهتمامات معًا بسؤال واحد إن غابا معًا (التزام: سؤال واحد بالرد)."""
    if not missing:
        return None
    table = CONTEXT_CLARIFY[language]
    if "governorate_or_city" in missing and "trip_interests" in missing:
        return table["governorate_or_city+trip_interests"]
    return table.get(missing[0], table["governorate_or_city"])


def place_display_name(name_ar: str, name_en: str, language: str) -> str:
    return name_ar if language == "ar" else name_en


def details_reply(place: PlaceDetails, language: str, state: ConversationState, plan_link_note: str = "") -> ChatResponse:
    name = place_display_name(place.place.name_ar, place.place.name_en, language)
    response = respond("place_details", language, state=state, cards=[place.place], name=name)
    if plan_link_note:
        response.reply = f"{response.reply} {plan_link_note}".strip()
    return response


def build_greeting(profile: ProfileResponse, language: str, state: ConversationState) -> ChatResponse:
    if profile.last_activity is None:
        return respond("greeting_new", language, state=state)
    activity = profile.last_activity
    if language == "ar":
        note = f"آخر مرة كنا عم نحضّر لرحلة بـ{activity.city} من {activity.days_ago} يوم"
    else:
        note = f"last time we were working on a trip to {activity.city} {activity.days_ago} days ago"
    return respond("greeting_returning", language, state=state, last_activity_note=note)


def plan_updated_reply(changes: list[PlanChange], plan: PlanObject, language: str, state: ConversationState) -> ChatResponse:
    note = "، ".join(c.change_ar for c in changes) if changes else ""
    return respond("plan_updated", language, state=state, plan=plan, change_note=note)


def infeasible_reply(feas: FeasibilityResponse, language: str, state: ConversationState) -> ChatResponse:
    suggestion = feas.suggestion.label_ar if feas.suggestion else ""
    return respond("infeasible", language, state=state, reason=feas.reason_ar, suggestion=suggestion)
