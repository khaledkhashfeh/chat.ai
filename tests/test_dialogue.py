"""اختبارات مدير الحوار: اختبارات وحدة لكل نية × (حالة مكتملة/ناقصة/بلا سياق)
وفق docs/plan.md (الجلسة 6)، وتحديث ذاكرة العمل الإلزامي بعد كل رد."""
import asyncio
from datetime import date

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


def test_search_confirms_after_full_profile_gathered():
    """قرار المالك: عرض الأماكن ليس شغل هذه الطبقة. رسالة تحمل المدينة +
    التصنيف + الميزانية + المجموعة (+ trip_purpose معروف مسبقًا) → تؤكّد
    اكتمال الجمع فقط، بلا استدعاء recommender.search وبلا بطاقات — الحالة
    نفسها هي "الناتج" لهذه الطبقة."""
    session = new_session()
    session.state.trip_purpose = "family_fun"  # حقل إلزامي أعلى أولوية — نزرعه هنا لعزل بقية الحقول بالاختبار
    response = run(dialogue.handle_turn(session, "اقترحلي مطاعم رخيصة بحلب لعيلتي", "u1"))
    assert response.cards is None
    assert session.memory.last_bot_action == "gathered_info"
    assert session.state.destination == ["حلب"]
    assert session.state.interests == ["tag:food"]
    assert session.state.budget_level == "low"
    assert session.state.group_type == "family"


def test_recommendations_phrasing_classifies_as_search_not_out_of_scope():
    """بق تقرير المالك: "بدي توصيات" كانت تُصنَّف out_of_scope (فجوة ببيانات
    التدريب — كلمة "توصيات" لم تكن ممثَّلة إطلاقًا) وترجع اعتذارًا خارج
    التخصص. يجب أن تُفهَم كطلب توصية سياحي عادي."""
    session = new_session()
    response = run(dialogue.handle_turn(session, "طيب بدي توصيات", "u1"))
    assert session.memory.last_bot_action != "out_of_scope"
    assert response.reply


def test_destinations_accumulate_across_turns_and_within_one_message():
    """طلب المالك: وجهة واحدة أو أكثر، وتتراكم عبر الأدوار (لا تُستبدَل) — تمامًا
    متل الاهتمامات."""
    session = new_session()
    run(dialogue.handle_turn(session, "بدي روح دمشق", "u1"))
    assert session.state.destination == ["دمشق"]
    run(dialogue.handle_turn(session, "وحلب كمان", "u1"))
    assert session.state.destination == ["دمشق", "حلب"]


# ---------------------------------------------------------------------------
# تعديل المعلومات المجموعة (إضافة/إزالة) — طلب المالك
# ---------------------------------------------------------------------------


def test_explicit_removal_of_destination():
    """«بدي الغي دمشق» يجب أن يزيل دمشق لا يضيفها (البق: أي ذِكر لاسم مدينة
    كان يُقرأ كإضافة بصرف النظر عن الفعل المرافق)."""
    session = new_session()
    session.state.destination = ["دمشق", "حلب"]
    response = run(dialogue.handle_turn(session, "بدي الغي دمشق", "u1"))
    assert session.state.destination == ["حلب"]
    assert session.memory.last_bot_action == "removed_destination"
    assert response.reply


def test_removal_verb_only_removes_the_targeted_city_not_others():
    """«شيل دمشق وضيف حلب» يزيل دمشق فقط — حلب تُضاف عاديًا لا تُزال سهوًا
    (الفعل يجب أن يسبق اسم المدينة تحديدًا ضمن نافذة قصيرة)."""
    session = new_session()
    session.state.destination = ["دمشق"]
    run(dialogue.handle_turn(session, "شيل دمشق وضيف حلب", "u1"))
    assert "دمشق" not in session.state.destination
    assert "حلب" in session.state.destination


def test_explicit_removal_of_interest_tag():
    session = new_session()
    session.state.interests = ["tag:historical", "tag:food"]
    response = run(dialogue.handle_turn(session, "الغي التاريخي", "u1"))
    assert session.state.interests == ["tag:food"]
    assert session.memory.last_bot_action == "removed_interests"
    assert response.reply


def test_removal_defers_to_modify_plan_when_message_references_the_plan():
    """«شيل قلعة دمشق من الخطة» يعني التعديل على خطة قائمة (اسم المكان يتضمن
    اسم مدينة بالحالة صدفة) — يجب ألّا تخطفها آلية إزالة الوجهات؛ يبقى
    modify_plan (أو "لا خطة لسا" إن لم توجد) هو المسار الصحيح."""
    session = new_session()
    session.state.destination = ["دمشق"]
    response = run(dialogue.handle_turn(session, "شيل قلعة دمشق من الخطة", "u1"))
    assert session.memory.last_bot_action == "no_plan"  # لا "removed_destination"
    assert "دمشق" in session.state.destination  # لم تُمَس أصلًا
    assert response.plan is None


# ---------------------------------------------------------------------------
# إعادة البدء من الصفر بتأكيد صريح — طلب المالك
# ---------------------------------------------------------------------------


def test_reset_trigger_asks_for_confirmation_without_wiping_state():
    session = new_session()
    session.state.destination = ["دمشق"]
    session.state.trip_purpose = "leisure"
    response = run(dialogue.handle_turn(session, "بدي خطة جديدة", "u1"))
    assert session.memory.pending_confirmation == "reset_plan"
    assert session.memory.last_bot_action == "asked_reset_confirmation"
    # لا مسح قبل التأكيد
    assert session.state.destination == ["دمشق"]
    assert session.state.trip_purpose == "leisure"
    assert response.reply


def test_reset_confirmed_wipes_state_and_asks_trip_purpose_again():
    session = new_session()
    session.state.destination = ["دمشق"]
    session.state.interests = ["tag:historical"]
    session.state.trip_purpose = "leisure"
    session.memory.gather_asks = 2
    run(dialogue.handle_turn(session, "بدي خطة جديدة", "u1"))
    response = run(dialogue.handle_turn(session, "اكيد", "u1"))
    assert session.state.destination == []
    assert session.state.interests == []
    assert session.state.trip_purpose is None
    assert session.memory.gather_asks == 0
    assert session.memory.pending_confirmation is None
    assert session.memory.last_bot_action == "reset_confirmed"
    assert response.reply  # يعيد سؤال الهدف فورًا بنفس الرد


def test_reset_declined_keeps_state_untouched():
    session = new_session()
    session.state.destination = ["دمشق"]
    session.state.trip_purpose = "leisure"
    run(dialogue.handle_turn(session, "بدي خطة جديدة", "u1"))
    response = run(dialogue.handle_turn(session, "لا خليها", "u1"))
    assert session.state.destination == ["دمشق"]
    assert session.state.trip_purpose == "leisure"
    assert session.memory.pending_confirmation is None
    assert session.memory.last_bot_action == "reset_declined"
    assert response.reply


def test_short_trip_purpose_answer_rescued_from_unclear():
    """جواب مقتضب لسؤال الهدف («بدي استجمام») قد يُصنَّف unclear بمفرده —
    يجب أن يُفهَم كبداية توصية لا رسالة غامضة عامة."""
    session = new_session()
    response = run(dialogue.handle_turn(session, "بدي استجمام", "u1"))
    assert session.state.trip_purpose == "leisure"
    assert session.memory.last_bot_action != "asked_clarification"
    assert response.reply


def test_start_date_combines_with_duration_into_date_range():
    """تاريخ بدء اختياري (طلب المالك) — يُستخرج فقط إن ذُكر، ويُحسب dates.end
    تلقائيًا بمعرفة duration_days الموروثة (بلا سؤال إطلاقًا عن التاريخ)."""
    session = new_session()
    run(dialogue.handle_turn(session, "بدي رحلة 3 ايام بدمشق", "u1"))
    assert session.state.dates is None  # ما ذُكر تاريخ لسا

    run(dialogue.handle_turn(session, "بدي ابلش بعد 5 ايام", "u1"))
    assert session.state.dates is not None
    start = date.fromisoformat(session.state.dates.start)
    end = date.fromisoformat(session.state.dates.end)
    assert (end - start).days == 2  # 3 ايام = يوم البداية + يومين


def test_start_date_never_asked_as_missing_field():
    """التاريخ ليس ضمن حقول الجمع الإلزامية أو المُثرية — لا يُسأل عنه إطلاقًا،
    ويبقى None ما لم يذكره المستخدم صراحة عبر عدّة أدوار."""
    session = new_session()
    run(dialogue.handle_turn(session, "بدي روح ع حلب", "u1"))
    run(dialogue.handle_turn(session, "اماكن تاريخيه", "u1"))
    run(dialogue.handle_turn(session, "اقتصادي", "u1"))
    run(dialogue.handle_turn(session, "مع عيلتي", "u1"))
    assert session.state.dates is None


def test_search_gathers_destination_first_when_nothing_known():
    """لا معلومات → أول سؤال عن المدينة (اجمع-أولًا)، بلا استدعاء أي أداة،
    ويُعلَّق المسار كـ recommend كي تُستأنف الأجوبة المقتضبة."""
    session = new_session()
    response = run(dialogue._handle_search(session.state, session.memory, False))
    assert response.cards is None
    assert session.memory.last_bot_action == "asked_missing_info"
    assert session.memory.pending_intent == "recommend"


def test_search_gathers_five_fields_in_order_then_confirms():
    """يجمع الهدف → المدينة → التصنيفات → الميزانية → المجموعة (سؤال واحد
    بالدور) ثم يتوقف عند تأكيد الجاهزية — لا يستدعي التوصية ولا يعرض بطاقات.
    trip_purpose أعلى أولوية (docs/contract.md) فيُسأل عنه أولًا حتى لو ذُكرت
    الوجهة بنفس رسالة البداية."""
    session = new_session()
    r1 = run(dialogue.handle_turn(session, "بدي روح ع حلب", "u1"))   # الوجهة موجودة، لكن trip_purpose أعلى أولوية وناقص
    assert r1.cards is None and ("بتدور عليه" in r1.reply)
    assert session.state.destination == ["حلب"]
    r2 = run(dialogue.handle_turn(session, "بدي استجمام", "u1"))      # → يسأل التصنيف (الوجهة موجودة أصلًا)
    assert r2.cards is None and ("نوع الأماكن" in r2.reply)
    r3 = run(dialogue.handle_turn(session, "تاريخيه", "u1"))          # → يسأل الميزانية
    assert r3.cards is None and ("ميزانيتك" in r3.reply)
    r4 = run(dialogue.handle_turn(session, "اقتصادي", "u1"))          # → يسأل المجموعة
    assert r4.cards is None and r4.reply
    r5 = run(dialogue.handle_turn(session, "مع عيلتي", "u1"))         # اكتمل → تأكيد بلا بطاقات
    assert r5.cards is None and r5.reply
    assert session.memory.last_bot_action == "gathered_info"
    assert session.memory.pending_intent is None
    assert session.state.trip_purpose == "leisure"


def test_unclear_classification_with_extracted_destination_rescued_into_search():
    """ثغرة تقرير المالك: "أريد الذهاب لدمشق" (فصحى) يصنّفها المصنّف unclear رغم
    استخراج الوجهة بنجاح — يجب ألّا نهدر الاستخراج بردّ عام، بل نعامله كبداية
    توصية ونسأل عن أعلى حقل ناقص أولوية (لا سؤال تخطيطي مفتوح)."""
    session = new_session()
    response = run(dialogue.handle_turn(session, "اريد الذهاب لدمشق", "u1"))
    assert session.state.destination == ["دمشق"]
    assert session.memory.pending_intent == "recommend"
    assert session.memory.last_bot_action == "asked_missing_info"
    assert response.cards is None and response.plan is None


def test_followup_answer_after_rescue_links_back_to_pending_gather():
    """تكملة الثغرة أعلاه: الجواب المقتضب التالي ("مع عائلتي") يُصنَّف unclear
    أيضًا بثقة متدنية جدًا — يجب أن يُستأنف مسار recommend المعلَّق لا أن يضيع
    (البق الأصلي: الوجهة السابقة "نُسيت" ولم يُربط الجواب بالسؤال)."""
    session = new_session()
    run(dialogue.handle_turn(session, "اريد الذهاب لدمشق", "u1"))
    response = run(dialogue.handle_turn(session, "مع عائلتي", "u1"))
    assert session.state.destination == ["دمشق"]  # لم تُنسَ
    assert session.state.group_type == "family"
    assert session.memory.last_bot_action == "asked_missing_info"  # يكمل الجمع لا يستسلم
    assert response.reply  # سؤال متابعة فعلي لا ردّ عام


def test_unresolved_answer_repeats_same_field_with_ask_again_wording():
    """طلب المالك: "يجب عليه ان يعيد السؤال ان لم يفهم من المستخدم" — إجابة لا
    تحمل أي معلومة قابلة للاستخراج تُبقي نفس الحقل الناقص، وبصيغة تصرّح بعدم
    الفهم صراحة (لا تكرار حرفي صامت لنفس السؤال)."""
    session = new_session()
    run(dialogue.handle_turn(session, "بدي روح ع حلب", "u1"))  # يسأل عن الاهتمامات
    response = run(dialogue.handle_turn(session, "اسحب زحمة الموضوع", "u1"))  # جواب غير قابل للاستخراج
    assert session.state.interests == []  # لسا ناقص
    ask_again_markers = ("ما فهمت جوابك", "ما وضح لي", "didn't quite catch", "wasn't clear")
    assert any(m in response.reply or m in response.reply.lower() for m in ask_again_markers), response.reply


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


def test_build_plan_complete_info_in_one_message_confirms_gathering():
    """قرار المالك: عرض الخطة ليس شغل هذه الطبقة. ملف الخطة الكامل بجملة واحدة
    (مدينة + تصنيف + ميزانية + مجموعة + مدة، مع trip_purpose معروف مسبقًا) →
    تأكيد جاهزية فقط، بلا استدعاء planner.build وبلا خطة معروضة."""
    session = new_session()
    session.state.trip_purpose = "cultural"  # حقل إلزامي أعلى أولوية — نزرعه لعزل بقية الحقول بالاختبار
    response = run(
        dialogue.handle_turn(session, "رتبلي خطة 3 ايام بدمشق لعيلتي اماكن تاريخيه رخيصه", "u1")
    )
    assert session.memory.last_bot_action == "gathered_info"
    assert session.memory.current_plan is None
    assert response.plan is None
    assert session.state.destination == ["دمشق"]
    assert session.state.duration_days == 3
    assert session.state.group_type == "family"
    assert session.state.budget_level == "low"
    assert session.state.interests == ["tag:historical"]


def test_build_plan_stops_asking_after_two_missed_questions_without_calling_tool():
    """سقف التهرّب: بعد دورين بلا تقدّم فعلي، تتوقف الأسئلة وتُصرَّح الجاهزية
    الجزئية — بلا استدعاء planner.build وبلا خطة (طلب المالك: عرض الخطط ليس
    شغل هذه الطبقة، حتى عند التهرّب)."""
    session = new_session()

    r1 = run(dialogue.handle_turn(session, "اعمل خطة سياحية شاملة", "u1"))
    assert session.memory.gather_asks == 1
    assert r1.plan is None

    r2 = run(dialogue.handle_turn(session, "خطط لي رحلة", "u1"))
    assert session.memory.gather_asks == 2
    assert r2.plan is None

    r3 = run(dialogue.handle_turn(session, "رتب لي رحلة سياحية", "u1"))
    assert r3.plan is None
    assert session.memory.current_plan is None
    assert session.memory.last_bot_action == "gathered_info_partial"
    assert session.memory.gather_asks == 0
    assert session.memory.pending_intent is None


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
