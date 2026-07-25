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
        "destination": "بدي ساعدك أكتر — لوين ناوي تروح؟",
        "duration_days": "حلو! كم يوم عندك للرحلة؟",
        "group_type": "طيب، رايح لحالك، مع العيلة، ولا مع أصحاب؟",
    },
    "en": {
        "destination": "Happy to help — where would you like to go?",
        "duration_days": "Nice! How many days do you have for the trip?",
        "group_type": "Got it — solo, with family, or with friends?",
    },
}

# أسئلة الجمع الموحّدة (نمط «اجمع أولًا ثم استدعِ») — سؤال واحد لكل حقل ناقص،
# بترتيب الأولوية المحدَّد في dialogue.py. مفاتيحها تغطي كل الحقول القابلة للجمع.
GATHER_QUESTIONS: dict[str, dict[str, str]] = {
    "ar": {
        "destination": "يلا نبدأ! لوين ناوي تروح؟ (أي مدينة بسوريا)",
        "interests": "شو نوع الأماكن يلي بتشدّك أكتر؟ تاريخية، طبيعية، بحر، أكل، أسواق، متاحف…",
        "budget_level": "قديش ميزانيتك تقريبًا؟ اقتصادية، متوسطة، ولا مرتاح أكتر؟",
        "group_type": "رايح لحالك، مع العيلة، ولا مع أصحاب؟",
        "duration_days": "كم يوم معك للرحلة؟",
    },
    "en": {
        "destination": "Let's get started — where would you like to go? (any city in Syria)",
        "interests": "What kind of places pull you in? Historical, nature, sea, food, markets, museums…",
        "budget_level": "What's your budget roughly? Budget, mid-range, or comfortable?",
        "group_type": "Traveling solo, with family, or with friends?",
        "duration_days": "How many days do you have?",
    },
}

T: dict[str, dict[str, list[str]]] = {
    "show_places": {
        "ar": ["يا هلا! لقيتلك {n} أماكن بتناسب ذوقك 👇 شو شدّ نظرك أكتر؟", "خبطتها معك — هي أفضل {n} خيارات ليك 👇 بتحب تعرف أكتر عن حدا منهن؟"],
        "en": ["Found {n} spots I think you'll love 👇 What catches your eye?", "Here are the top {n} picks for you 👇 Want details on any of them?"],
    },
    "show_places_partial": {
        "ar": ["يلا نبدأ بهيدول {n} اقتراحات مبدئية 👇 وبعدين بنضبطها أكتر مع بعض", "قبل ما نحدد كل شي، جبتلك {n} أفكار أولية 👇 خبرني شو عاجبك ونطورها"],
        "en": ["Let's start with these {n} initial picks 👇 we'll fine-tune together after", "Before locking in every detail, here are {n} early ideas 👇 tell me what clicks"],
    },
    "no_results": {
        "ar": ["{reason}. بدك نوسّع البحث شوي ونلاقي شي أحلى؟", "دقّقت وما لقيت شي مطابق تمامًا — {reason}. نجرب زاوية تانية؟"],
        "en": ["{reason}. Want me to widen the search and find something better?", "I looked closely and nothing matched exactly — {reason}. Want to try another angle?"],
    },
    "place_details": {
        "ar": ["هاد كل يلي عندي عن {name} 👆 عجبك؟ بتحب تضيفو للخطة؟", "تفاصيل {name} فوق 👆 شكلها حلوة صح؟ في شي تاني بدك تعرفو؟"],
        "en": ["Here's everything I've got on {name} 👆 Liking it? Want to add it to your plan?", "Details for {name} above 👆 Anything else you'd like to know?"],
    },
    "which_place": {
        "ar": ["ولا يهمك، بس عن أي مكان بالضبط؟", "قصدك مين من يلي حكينا عنهن؟"],
        "en": ["Happy to help — which place exactly?", "Which one did you mean?"],
    },
    "compare_result": {
        "ar": ["قارنتلك بينهن بعناية 👆 وبرأيي {winner} الأنسب، بس القرار إلك طبعًا", "هاي المقارنة 👆 شخصيًا بميل لـ{winner}، وإنت شو رأيك؟"],
        "en": ["Weighed them carefully for you 👆 I'd lean towards {winner}, but it's your call", "Compared them above 👆 {winner} looks like the strongest pick — what do you think?"],
    },
    "which_places_to_compare": {
        "ar": ["أكيد! بين أي مكانين بالضبط بدك تقارن؟", "قصدك تقارن بين مين ومين؟"],
        "en": ["Sure thing — which two places would you like to compare?", "Which ones exactly should I compare?"],
    },
    "ask": {
        "ar": ["{question}", "بس بدي اعرف كمان شي صغير: {question}"],
        "en": ["{question}", "Just one more thing: {question}"],
    },
    "plan_ready": {
        "ar": ["جهزتلك خطتك زي ما بتحبها بالظبط: {summary} ✨ شو رأيك فيها؟", "هيدي خطتك جاهزة: {summary} 👆 في شي بدك تعدلو؟"],
        "en": ["Put together a plan I think you'll love: {summary} ✨ What do you think?", "Your plan is ready: {summary} 👆 anything you'd like to adjust?"],
    },
    "plan_ready_defaults": {
        "ar": ["يلا نبدأ بخطة عامة هلأ، وفينا نضبطها أكتر بعدين: {summary} 👆", "بما إنه ما توضحت التفاصيل كلها، جهزتلك خطة أولية: {summary} 👆 وفينا نعدلها متل ما بدك"],
        "en": ["Let's start with a general plan for now, we can fine-tune later: {summary} 👆", "Since not every detail was clear, here's an initial plan: {summary} 👆 we can adjust it anytime"],
    },
    "infeasible": {
        "ar": ["بدي أكون صريح معك: {reason}. {suggestion}", "بصراحة، {reason}. بس {suggestion}"],
        "en": ["Being straight with you: {reason}. {suggestion}", "Honestly, {reason}. But {suggestion}"],
    },
    "no_plan_yet": {
        "ar": ["ما في خطة لسا — يلا نبنيلك وحدة؟", "لسا ما عنا خطة جاهزة، شو رأيك نعملها سوا هلق؟"],
        "en": ["There's no plan yet — want me to build you one?", "We don't have a plan yet — shall we make one together?"],
    },
    "which_place_in_plan": {
        "ar": ["أي واحد من أماكن الخطة تقصد بالضبط؟", "قصدك مين من محطات خطتك الحالية؟"],
        "en": ["Which stop in your plan do you mean exactly?", "Which one of your plan's places did you mean?"],
    },
    "which_change": {
        "ar": ["تمام، شو بالضبط بدك تغير بالخطة؟", "وضّحلي أكتر شو التعديل يلي بدك ياه؟"],
        "en": ["Got it — what exactly would you like to change in the plan?", "Could you clarify what change you'd like?"],
    },
    "plan_updated": {
        "ar": ["تم ✅ {change_note} — في شي تاني بدك تعدلو؟", "خلص ✅ {change_note}. شو رأيك فيها هلق؟"],
        "en": ["Done ✅ {change_note} — anything else to adjust?", "All set ✅ {change_note}. How does it look now?"],
    },
    "added_to_plan": {
        "ar": ["تمام، ضفتلك {name} ✅ في شي تاني بدك تضيفو؟", "خلص، {name} صار جزء من خطتك ✅ كمّل معايا؟"],
        "en": ["Done — added {name} to your plan ✅ Anything else to add?", "{name} is now saved ✅ Want to keep going?"],
    },
    "diagnose_rejection": {
        "ar": ["تمام، خليني افهم أكتر شو بيناسبك: النوعية، البعد، ولا شي تاني؟", "ولا يهمك، بس عرفني: شو المشكلة بيلي عرضتن — النوع، المسافة، ولا غيرها؟"],
        "en": ["Got it — help me understand what you're after: type of places, distance, or something else?", "No problem — what was off about them: category, distance, or something else?"],
    },
    "greeting_new": {
        "ar": ["أهلًا فيك! 👋 هون لأساعدك تلاقي أحلى الأماكن بسوريا — وين ناوي تروح؟", "يا هلا فيك! جاهز اقترحلك رحلة حلوة زي ما بتحبها — شو بخاطرك؟"],
        "en": ["Welcome! 👋 I'm here to help you find great places in Syria — where are you thinking of going?", "Hi there! Ready to plan something you'll love — what's on your mind?"],
    },
    "greeting_returning": {
        "ar": ["أهلا فيك من جديد! {last_activity_note} — نكمل منين ما وقفنا؟", "يا هلا رجعت! {last_activity_note}. شو رأيك نكمل سوا؟"],
        "en": ["Welcome back! {last_activity_note} — shall we continue where we left off?", "Great to see you again! {last_activity_note}."],
    },
    "out_of_scope": {
        "ar": ["هاد خارج تخصصي، أنا هون للسياحة داخل سوريا بس. بس لو ناوي رحلة بسوريا أنا جاهز أساعدك! 😊", "ما بقدر ساعدك بهالطلب — تخصصي السياحة بسوريا فقط. في وجهة سورية ببالك أخطط فيها معك؟"],
        "en": ["That's outside what I do — I specialize in tourism within Syria. But if you're planning a Syria trip, I'm all yours! 😊", "I can't help with that one — I only cover travel within Syria. Have a Syrian destination in mind?"],
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
        "ar": ["ما فهمت قصدك تمامًا 🙂 بتحب اقترحلك أماكن، ولا نبني خطة رحلة سوا؟", "ممكن توضح أكتر؟ بقدر اقترح أماكن أو أساعدك تخطط رحلة زي ما بتحب."],
        "en": ["I didn't quite get that 🙂 Would you like some place suggestions, or shall we build a trip plan together?", "Could you clarify a bit? I can suggest places or help plan a trip."],
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


def gather_question_for(field: str, language: str) -> str:
    """سؤال جمع حقل واحد (نمط اجمع-أولًا) — يغطي كل الحقول القابلة للجمع."""
    return GATHER_QUESTIONS[language][field]


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
