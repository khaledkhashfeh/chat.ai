"""اختبارات مدير الحوار: اختبارات وحدة لكل نية × (حالة مكتملة/ناقصة/بلا سياق)
وفق docs/plan.md (الجلسة 6)، وتحديث ذاكرة العمل الإلزامي بعد كل رد."""
import asyncio

from app.conversation import dialogue
from app.conversation.session import SessionData
from app.mocks import planner
from app.shared.models import ConversationState, RecommendedPlaceRef, WorkingMemory


def run(coro):
    return asyncio.run(coro)


def new_session() -> SessionData:
    return SessionData()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_recommends_after_full_profile_gathered():
    """نمط اجمع-أولًا: رسالة تحمل المدينة + التصنيف + الميزانية + المجموعة →
    يستدعي التوصية مباشرةً ويعرض البطاقات (لا يبقى يسأل)."""
    session = new_session()
    response = run(dialogue.handle_turn(session, "اقترحلي مطاعم رخيصة بحلب لعيلتي", "u1"))
    assert response.cards is not None and len(response.cards) > 0
    assert session.memory.last_bot_action == "showed_recommendations"
    assert len(session.memory.last_recommendations) == len(response.cards)
    assert session.memory.last_recommendations[0].pos == 1


def test_search_no_results_does_not_crash_and_offers_fallback():
    session = new_session()
    # ملف مكتمل حتى يصل لاستدعاء البحث فعلًا (المدينة غير موجودة → لا نتائج)
    session.state.destination = ["مدينه_غير_موجوده_ابدا"]
    session.state.interests = ["tag:historical"]
    session.state.budget_level = "low"
    session.state.group_type = "solo"
    response = run(dialogue.handle_turn(session, "اقترحلي اماكن حلوه", "u1"))
    assert response.cards is None or response.cards == []
    assert session.memory.last_bot_action == "search_no_results"


def test_search_gathers_destination_first_when_nothing_known():
    """لا معلومات → أول سؤال عن المدينة (اجمع-أولًا)، بلا عرض بطاقات، ويُعلَّق
    المسار كـ recommend كي تُستأنف الأجوبة المقتضبة."""
    session = new_session()
    response = run(dialogue._handle_search("بدي روح رحلة", session.state, session.memory, "u_demo", False))
    assert response.cards is None
    assert session.memory.last_bot_action == "asked_missing_info"
    assert session.memory.pending_intent == "recommend"


def test_search_gathers_four_fields_in_order_then_recommends():
    """يجمع المدينة → التصنيفات → الميزانية → المجموعة (سؤال واحد بالدور) ثم يوصّي."""
    session = new_session()
    r1 = run(dialogue.handle_turn(session, "بدي روح ع حلب", "u1"))   # المدينة موجودة → يسأل التصنيف
    assert r1.cards is None and ("نوع الأماكن" in r1.reply)
    r2 = run(dialogue.handle_turn(session, "تاريخيه", "u1"))          # → يسأل الميزانية
    assert r2.cards is None and ("ميزانيتك" in r2.reply)
    r3 = run(dialogue.handle_turn(session, "اقتصادي", "u1"))          # → يسأل المجموعة
    assert r3.cards is None and r3.reply
    r4 = run(dialogue.handle_turn(session, "مع عيلتي", "u1"))         # اكتمل → بطاقات
    assert r4.cards is not None and len(r4.cards) > 0
    assert session.memory.pending_intent is None


# ---------------------------------------------------------------------------
# details — لا يفتح أسئلة التخطيط أبدًا
# ---------------------------------------------------------------------------


def test_details_no_context_asks_which_place():
    session = new_session()
    response = run(dialogue.handle_turn(session, "شو قصة هالمكان؟", "u1"))
    assert session.memory.last_bot_action == "asked_which_place"
    assert session.memory.current_plan is None  # لم يُستدعَ التخطيط إطلاقًا


def test_details_resolves_ordinal_from_last_recommendations():
    session = new_session()
    session.memory.last_recommendations = [
        RecommendedPlaceRef(pos=1, place_id="p01", name_ar="قلعة دمشق", name_en="Damascus Citadel"),
        RecommendedPlaceRef(pos=2, place_id="p02", name_ar="الجامع الأموي", name_en="Umayyad Mosque"),
    ]
    response = run(dialogue.handle_turn(session, "شو قصة التاني؟", "u1"))
    assert session.memory.last_mentioned_place == "p02"
    assert session.memory.last_bot_action == "showed_details"
    assert response.cards is not None and response.cards[0].place_id == "p02"


def test_details_by_name_from_catalog_without_prior_context():
    """«عطيني معلومات عن قلعة حلب» بأول رسالة (بلا مقترحات سابقة) → يُحلّ الاسم
    من الكتالوج الكامل ويعرض التفاصيل مباشرة، لا يسأل «أي مكان؟»."""
    session = new_session()
    response = run(dialogue.handle_turn(session, "عطيني معلومات عن قلعة حلب", "u1"))
    assert response.cards is not None and response.cards[0].place_id == "p11"
    assert session.memory.last_bot_action == "showed_details"
    assert session.memory.current_plan is None  # التفاصيل لا تفتح التخطيط


def test_details_unknown_name_still_asks_which_place():
    """اسم غير موجود بالكتالوج ولا سياق → يبقى يسأل «أي مكان؟» (لا تطابق كاذب)."""
    session = new_session()
    response = run(dialogue.handle_turn(session, "شو قصة هالمكان؟", "u1"))
    assert session.memory.last_bot_action == "asked_which_place"


def test_details_falls_back_to_last_mentioned_place_via_pronoun():
    session = new_session()
    session.memory.last_recommendations = [
        RecommendedPlaceRef(pos=1, place_id="p01", name_ar="قلعة دمشق", name_en="Damascus Citadel"),
    ]
    session.memory.last_mentioned_place = "p01"
    response = run(dialogue.handle_turn(session, "احكيلي عنه أكتر", "u1"))
    assert response.cards[0].place_id == "p01"


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def test_compare_resolves_ordinal_pair():
    session = new_session()
    session.memory.last_recommendations = [
        RecommendedPlaceRef(pos=1, place_id="p01", name_ar="قلعة دمشق", name_en="Damascus Citadel"),
        RecommendedPlaceRef(pos=2, place_id="p02", name_ar="الجامع الأموي", name_en="Umayyad Mosque"),
        RecommendedPlaceRef(pos=3, place_id="p03", name_ar="سوق الحميدية", name_en="Al-Hamidiyah Souq"),
    ]
    response = run(dialogue.handle_turn(session, "ايهن احسن الاول ولا التالت", "u1"))
    assert session.memory.last_bot_action == "showed_comparison"
    assert response.comparison is not None
    compared_ids = {p.place_id for p in response.comparison.places}
    assert compared_ids == {"p01", "p03"}


def test_compare_without_enough_context_asks_which_places():
    session = new_session()
    response = run(dialogue.handle_turn(session, "قارن لي القلعة والمتحف", "u1"))
    assert session.memory.last_bot_action == "asked_which_places_to_compare"
    assert response.comparison is None


# ---------------------------------------------------------------------------
# build_plan
# ---------------------------------------------------------------------------


def test_build_plan_missing_info_asks_single_question_about_destination():
    session = new_session()
    response = run(dialogue.handle_turn(session, "اعمل خطة سياحية شاملة", "u1"))
    assert session.memory.last_bot_action == "asked_missing_info"
    assert session.memory.current_plan is None
    assert response.plan is None
    assert response.reply  # قيمة فعلية بالرد رغم النقص


def test_build_plan_complete_info_in_one_message_builds_plan():
    session = new_session()
    # ملف الخطة الكامل بجملة واحدة: مدينة + تصنيف + ميزانية + مجموعة + مدة
    response = run(
        dialogue.handle_turn(session, "رتبلي خطة 3 ايام بدمشق لعيلتي اماكن تاريخيه رخيصه", "u1")
    )
    assert session.memory.last_bot_action == "showed_plan"
    assert session.memory.current_plan is not None
    assert response.plan is not None
    assert len(response.plan.days) == 3


def test_build_plan_forces_call_with_defaults_after_two_missed_questions():
    """سقف التهرّب: بعد دورين بلا تقدّم فعلي، يُستدعى planner.build بقيم افتراضية
    بدل الاستمرار بالسؤال (طلب المالك: نتابع بما توفّر ونصرّح بذلك)."""
    session = new_session()

    r1 = run(dialogue.handle_turn(session, "اعمل خطة سياحية شاملة", "u1"))
    assert session.memory.gather_asks == 1
    assert r1.plan is None

    r2 = run(dialogue.handle_turn(session, "خطط لي رحلة", "u1"))
    assert session.memory.gather_asks == 2
    assert r2.plan is None

    r3 = run(dialogue.handle_turn(session, "رتب لي رحلة سياحية", "u1"))
    assert r3.plan is not None
    assert session.memory.last_bot_action == "showed_plan_with_defaults"
    assert session.memory.gather_asks == 0
    assert session.state.destination and session.state.duration_days and session.state.group_type


# ---------------------------------------------------------------------------
# modify_plan
# ---------------------------------------------------------------------------


def _session_with_plan() -> SessionData:
    session = new_session()
    session.state.destination = ["دمشق"]
    session.state.duration_days = 1
    session.state.group_type = "family"
    session.state.language = "ar"
    plan = run(planner.build(state=session.state, mandatory_place_ids=["p01"]))
    session.memory.current_plan = plan
    return session


def test_modify_plan_no_plan_yet():
    session = new_session()
    response = run(dialogue.handle_turn(session, "شيل المتحف من الخطة", "u1"))
    assert session.memory.last_bot_action == "no_plan"
    assert response.plan is None


def test_modify_plan_remove_by_name_is_surgical():
    session = _session_with_plan()
    response = run(dialogue.handle_turn(session, "شيل قلعة دمشق من الخطة", "u1"))
    assert session.memory.last_bot_action == "updated_plan"
    remaining_ids = {s.place_id for day in response.plan.days for s in day.stops}
    assert "p01" not in remaining_ids


def test_modify_plan_ambiguous_target_asks_which_place_in_plan():
    session = _session_with_plan()
    response = run(dialogue.handle_turn(session, "بدل مكان الزيارة بغيره", "u1"))
    assert session.memory.last_bot_action == "asked_which_place_in_plan"
    assert response.plan is None


# ---------------------------------------------------------------------------
# add_to_plan
# ---------------------------------------------------------------------------


def test_add_to_plan_falls_back_to_last_mentioned_place():
    session = new_session()
    session.memory.last_mentioned_place = "p02"
    response = run(dialogue.handle_turn(session, "ضيف هاد المكان", "u1"))
    assert "p02" in session.state.saved_place_ids
    assert session.memory.last_bot_action == "added_to_plan"
    assert response.reply


def test_add_to_plan_no_context_asks_which_place():
    session = new_session()
    response = run(dialogue.handle_turn(session, "add it to my plan", "u1"))
    assert session.memory.last_bot_action == "asked_which_place"
    assert session.state.saved_place_ids == []


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


def test_reject_excludes_all_shown_places_and_asks_diagnostic_question():
    session = new_session()
    session.memory.last_recommendations = [
        RecommendedPlaceRef(pos=1, place_id="p01", name_ar="قلعة دمشق", name_en="Damascus Citadel"),
        RecommendedPlaceRef(pos=2, place_id="p02", name_ar="الجامع الأموي", name_en="Umayyad Mosque"),
    ]
    response = run(dialogue.handle_turn(session, "ما عجبوني", "u1"))
    assert set(session.state.excluded_place_ids) == {"p01", "p02"}
    assert session.memory.last_bot_action == "asked_rejection_reason"
    assert response.reply


# ---------------------------------------------------------------------------
# greeting_thanks / out_of_scope
# ---------------------------------------------------------------------------


def test_greeting_new_user():
    session = new_session()
    response = run(dialogue.handle_turn(session, "مرحبا", "brand_new_user"))
    assert session.memory.last_bot_action == "greeted"
    assert response.reply


def test_greeting_returning_user_mentions_last_activity():
    session = new_session()
    response = run(dialogue.handle_turn(session, "مرحبا", "u_demo"))
    assert "اللاذقية" in response.reply


def test_out_of_scope_does_not_call_any_tool_side_effects():
    session = new_session()
    response = run(dialogue.handle_turn(session, "بدي احجز رحلة طيران لمصر", "u1"))
    assert session.memory.last_bot_action == "out_of_scope"
    assert response.cards is None
    assert response.plan is None


# ---------------------------------------------------------------------------
# unclear — يُختبر مباشرة (تجنبًا لعشوائية تصنيف نصوص غامضة حقيقية)
# ---------------------------------------------------------------------------


def test_unclear_with_plan_context_suggests_plan_edit():
    build_state = ConversationState(destination=["دمشق"], duration_days=1, group_type="solo", language="ar")
    plan = run(planner.build(state=build_state))
    memory = WorkingMemory(current_plan=plan)
    state = ConversationState(language="ar")

    response = dialogue._handle_unclear(state, memory)
    assert memory.last_bot_action == "asked_clarification"
    assert response.reply


def test_unclear_with_recommendations_context():
    state = ConversationState(language="ar")
    memory = WorkingMemory(
        last_recommendations=[RecommendedPlaceRef(pos=1, place_id="p01", name_ar="أ", name_en="a")]
    )
    response = dialogue._handle_unclear(state, memory)
    assert memory.last_bot_action == "asked_clarification"
    assert response.reply


def test_unclear_with_no_context_at_all():
    state = ConversationState(language="ar")
    memory = WorkingMemory()
    response = dialogue._handle_unclear(state, memory)
    assert response.reply


# ---------------------------------------------------------------------------
# قاعدة الوراثة — معلومة تُذكر مرة لا تُنسى عبر رسائل لاحقة غير متعلقة
# ---------------------------------------------------------------------------


def test_state_inheritance_not_erased_by_unrelated_message():
    session = new_session()
    session.state.duration_days = 3
    run(dialogue.handle_turn(session, "اقترحلي مطاعم بحلب", "u1"))
    assert session.state.duration_days == 3


def test_language_switches_immediately_within_session():
    session = new_session()
    run(dialogue.handle_turn(session, "اقترحلي مطاعم بحلب", "u1"))
    assert session.state.language == "ar"
    run(dialogue.handle_turn(session, "suggest restaurants in aleppo", "u1"))
    assert session.state.language == "en"
