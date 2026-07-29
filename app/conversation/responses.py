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
    PlaceCard,
    PlaceDetails,
    PlanChange,
    PlanObject,
    ProfileResponse,
)

# أسئلة الجمع الموحّدة (نمط «اجمع أولًا ثم استدعِ») — سؤال واحد لكل حقل ناقص،
# بترتيب الأولوية المحدَّد في dialogue.py. مفاتيحها تغطي كل الحقول القابلة للجمع.
GATHER_QUESTIONS: dict[str, dict[str, str]] = {
    "ar": {
        "trip_purpose": "شو أكتر شي بتدور عليه بالرحلة؟ استجمام وراحة، مغامرة ونشاط، أجواء ثقافية وتاريخية، متعة عائلية، ولا أجواء رومانسية؟",
        "destination": "يلا نبدأ! لوين ناوي تروح؟ (أي مدينة بسوريا)",
        "interests": "شو نوع الأماكن يلي بتشدّك أكتر؟ تاريخية، طبيعية، بحر، أكل، أسواق، متاحف…",
        "budget_level": "قديش ميزانيتك تقريبًا؟ اقتصادية، متوسطة، ولا مرتاح أكتر؟",
        "group_type": "رايح لحالك، مع العيلة، ولا مع أصحاب؟",
        "duration_days": "كم يوم معك للرحلة؟",
    },
    "en": {
        "trip_purpose": "What are you mainly after on this trip? Relaxation, adventure, culture and history, family fun, or something romantic?",
        "destination": "Let's get started — where would you like to go? (any city in Syria)",
        "interests": "What kind of places pull you in? Historical, nature, sea, food, markets, museums…",
        "budget_level": "What's your budget roughly? Budget, mid-range, or comfortable?",
        "group_type": "Traveling solo, with family, or with friends?",
        "duration_days": "How many days do you have?",
    },
}

T: dict[str, dict[str, list[str]]] = {
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
    "ask_again": {
        "ar": ["ما فهمت جوابك منيح، ممكن توضحلي: {question}", "عذرًا، ما وضح لي — {question}"],
        "en": ["I didn't quite catch that — could you clarify: {question}", "Sorry, that wasn't clear to me — {question}"],
    },
    # نهاية مسار الجمع (قرار المالك): طبقة المحادثة تتوقف هنا — لا تستدعي
    # recommender/planner ولا تعرض أماكن أو خططًا؛ فقط تؤكد اكتمال الجمع.
    # الجيسون الجاهز للإرسال يُعرض بقسم منفصل بواجهة الاختبار، لا هنا.
    "gathered_ready": {
        "ar": ["تمام، جمعت كل معلومات {target} ✅ جاهزة نبعتها لطبقة التوصية.", "خلص، صار عندي كل يلي بلزم لـ{target} ✅"],
        "en": ["Got it — I've gathered everything needed for {target} ✅ ready to send to the recommendation layer.", "All set for {target} ✅ I have everything I need."],
    },
    "gathered_partial": {
        "ar": ["تمام، جمعت يلي قدرت عليه من معلومات {target} ✅ منضبط الباقي بعدين.", "خلص هلق بيلي توفر لـ{target} ✅ ونكمل التفاصيل لاحقًا."],
        "en": ["Got what I could for {target} for now ✅ we'll refine the rest later.", "That's enough to work with for {target} ✅ we can fine-tune later."],
    },
    # تعديل/إزالة معلومة مجموعة (لا خطة) — إضافة تعمل ضمنيًا بذكر المعلومة عاديًا
    "removed_value": {
        "ar": ["تمام، شلت {value} ✅ في شي تاني بدك تعدلو؟", "خلص، ألغيت {value} ✅ شو رأيك نكمل؟"],
        "en": ["Done — removed {value} ✅ anything else to adjust?", "Got it, {value} is gone ✅ shall we continue?"],
    },
    "confirm_reset": {
        "ar": ["متأكد بدك تبلش خطة جديدة؟ رح ننسى كل المعلومات يلي جمعناها لهلق 🗑️ (قلي أكيد/لا)", "بس تأكيد: هيك رح نمسح كل شي وبنبلش من الصفر — متأكد؟ (أكيد/لا)"],
        "en": ["Are you sure you want to start a new plan? We'll forget everything gathered so far 🗑️ (confirm/no)", "Just to confirm: this will erase everything and start fresh — sure? (yes/no)"],
    },
    "reset_confirmed": {
        "ar": ["تمام ✅ نسينا كل شي وبلشنا من جديد — {question}", "خلص، صفحة بيضاء ✅ {question}"],
        "en": ["Done ✅ clean slate, starting fresh — {question}", "All forgotten ✅ {question}"],
    },
    "reset_declined": {
        "ar": ["تمام، ما لمسنا شي — نكمل متل ما إحنا 🙂", "ولا يهمك، خلينا نكمل عادي."],
        "en": ["No problem, nothing changed — let's continue as we are 🙂", "Sure, we'll keep going as-is."],
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


# تسمية عرض نظيفة لكل وسم اهتمام (tag:xxx) بلغتين — لرسائل مثل "شلت الأماكن التاريخية ✅"
_TAG_DISPLAY: dict[str, dict[str, str]] = {
    "tag:historical": {"ar": "الأماكن التاريخية", "en": "historical places"},
    "tag:religious": {"ar": "الأماكن الدينية", "en": "religious places"},
    "tag:nature": {"ar": "الأماكن الطبيعية", "en": "nature spots"},
    "tag:sea": {"ar": "أماكن البحر", "en": "sea spots"},
    "tag:market": {"ar": "الأسواق", "en": "markets"},
    "tag:food": {"ar": "أماكن الأكل", "en": "food spots"},
    "tag:family_fun": {"ar": "أماكن العائلة", "en": "family places"},
    "tag:adventure": {"ar": "أماكن المغامرة", "en": "adventure spots"},
    "tag:quiet": {"ar": "الأماكن الهادئة", "en": "quiet places"},
    "tag:museum": {"ar": "المتاحف", "en": "museums"},
}


def tag_display_label(tag: str, language: str) -> str:
    entry = _TAG_DISPLAY.get(tag)
    if not entry:
        return tag.replace("tag:", "")
    return entry.get(language, entry["ar"])


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
