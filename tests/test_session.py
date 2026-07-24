"""اختبارات الحالة وذاكرة العمل وتخزين/تحميل الجلسة."""
from app.conversation.session import InMemoryRedis, SessionData, SessionStore
from app.shared.models import ConversationState, RecommendedPlaceRef, WorkingMemory


def make_store() -> SessionStore:
    # نستخدم النسخة البديلة بالذاكرة كي لا يعتمد الاختبار على خادم Redis حقيقي.
    return SessionStore(client=InMemoryRedis())


def test_new_session_is_empty_defaults():
    store = make_store()
    data = store.load("sess-1")
    assert data.state == ConversationState()
    assert data.memory == WorkingMemory()


def test_save_and_reload_roundtrip_preserves_state_and_memory():
    store = make_store()
    data = store.load("sess-2")
    data.state.destination = ["دمشق"]
    data.state.duration_days = 4
    data.state.group_type = "family"
    data.state.language = "ar"
    data.memory.last_recommendations = [
        RecommendedPlaceRef(pos=1, place_id="p17", name_ar="قلعة دمشق", name_en="Damascus Citadel"),
    ]
    data.memory.last_mentioned_place = "p17"
    data.memory.last_bot_action = "showed_recommendations"
    store.save("sess-2", data)

    reloaded = store.load("sess-2")
    assert reloaded.state.destination == ["دمشق"]
    assert reloaded.state.duration_days == 4
    assert reloaded.state.group_type == "family"
    assert reloaded.memory.last_mentioned_place == "p17"
    assert reloaded.memory.last_recommendations[0].place_id == "p17"
    assert reloaded.memory.last_bot_action == "showed_recommendations"


def test_state_inheritance_across_many_messages():
    """معلومة ذُكرت مرة يجب أن تبقى محفوظة حتى بعد عشرات الرسائل (قاعدة الوراثة)."""
    store = make_store()
    session_id = "sess-inherit"
    data = store.load(session_id)
    data.state.destination = ["اللاذقية"]
    store.save(session_id, data)

    for i in range(20):
        data = store.load(session_id)
        data.state.group_size = i  # تعديلات لاحقة لا تمحو الوجهة
        store.save(session_id, data)

    final = store.load(session_id)
    assert final.state.destination == ["اللاذقية"]


def test_clear_removes_session():
    store = make_store()
    session_id = "sess-clear"
    data = store.load(session_id)
    data.state.destination = ["حلب"]
    store.save(session_id, data)
    store.clear(session_id)

    fresh = store.load(session_id)
    assert fresh.state.destination == []


def test_session_data_json_roundtrip_with_plan():
    from app.shared.models import CostEstimate, PlanDay, PlanObject, PlanStop

    plan = PlanObject(
        plan_id="pl_1",
        summary_ar="خطة تجريبية",
        summary_en="test plan",
        total_cost_estimate=CostEstimate(activities=10, food=10, transport=5),
        days=[
            PlanDay(
                day_number=1,
                title_ar="اليوم الأول",
                day_cost_estimate=25,
                stops=[
                    PlanStop(
                        place_id="p1",
                        name_ar="مكان",
                        name_en="place",
                        stop_type="visit",
                        arrival="09:00",
                        departure="10:00",
                        visit_duration_min=60,
                        travel_from_prev_min=0,
                        cost_estimate=0,
                        why_here="سبب",
                    )
                ],
            )
        ],
    )
    session = SessionData(memory=WorkingMemory(current_plan=plan))
    raw = session.to_json()
    reloaded = SessionData.from_json(raw)
    assert reloaded.memory.current_plan.plan_id == "pl_1"
    assert reloaded.memory.current_plan.days[0].stops[0].place_id == "p1"
