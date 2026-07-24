"""tests/test_flows.py — حوارات كاملة متسلسلة عبر /chat (الأهم بحسب CLAUDE.md).

يشغّل كل اختبار سلسلة رسائل بنفس session_id عبر نقطة /chat الفعلية (FastAPI
TestClient)، ويتحقق بكل خطوة من: النية المنفَّذة (عبر شكل الرد: cards/plan/
comparison)، المكان المحلول عبر محلّل الإشارات، تحديث ذاكرة العمل والحالة على
الخادم، ولغة الرد. يغطي السيناريوهات المرجعية بـ docs/spec.md §5 (خصوصًا
سيناريوهات تسلسل السياق) قدر ما تسمح به بنية العقد الموثّقة.
"""
from fastapi.testclient import TestClient

from app.config import settings
from app.main import _session_store, app

client = TestClient(app)
HEADERS = {"X-Internal-Secret": settings.internal_secret}


def send(session_id: str, message: str, user_id: str = "u1") -> dict:
    resp = client.post(
        "/chat",
        json={"session_id": session_id, "user_id": user_id, "message": message},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_cors_allows_test_client_origin():
    """web/test-client.html يُفتح غالبًا من file:// أو منفذ تطوير مختلف —
    يجب أن يرى رأس Access-Control-Allow-Origin كي يعمل الطلب من المتصفح."""
    resp = client.get("/health", headers={"Origin": "http://127.0.0.1:5500"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") in ("*", "http://127.0.0.1:5500")


def test_chat_rejects_missing_or_wrong_secret():
    resp = client.post(
        "/chat",
        json={"session_id": "s", "user_id": "u", "message": "مرحبا"},
        headers={"X-Internal-Secret": "wrong-secret"},
    )
    assert resp.status_code == 401


def test_full_context_flow_search_details_add_build_modify():
    """search → «شو قصة التاني؟» (ترتيبي) → «حلو ضيفو» (ضمير) →
    «ضيف الاول للخطة» (ترتيبي) → build (بالمحفوظات + feasibility) →
    modify(remove) جراحي. يغطي أهم سيناريو تسلسل سياق بـ docs/plan.md المخطط 3."""
    sid = "flow-context-1"

    # 1) بحث بملف مكتمل (مدينة+تصنيف+ميزانية+مجموعة) → بطاقات فورًا
    r1 = send(sid, "اقترحلي أماكن تاريخيه بدمشق لعيلتي واقتصادي")
    assert r1["cards"] and len(r1["cards"]) >= 2
    session1 = _session_store.load(sid)
    assert session1.memory.last_bot_action == "showed_recommendations"
    recs = session1.memory.last_recommendations
    assert len(recs) >= 2

    # 2) إشارة ترتيبية: «التاني» = ثاني عنصر بالقائمة المعروضة
    r2 = send(sid, "شو قصة التاني؟")
    assert r2["plan"] is None  # تفاصيل فقط — لا تخطيط أبدًا
    second_place_id = recs[1].place_id
    assert r2["cards"][0]["place_id"] == second_place_id
    session2 = _session_store.load(sid)
    assert session2.memory.last_mentioned_place == second_place_id

    # 3) ضمير: «ضيفو» يشير لآخر مكان مذكور (نتيجة الخطوة 2)
    r3 = send(sid, "حلو ضيفو")
    session3 = _session_store.load(sid)
    assert second_place_id in session3.state.saved_place_ids
    assert session3.memory.last_bot_action == "added_to_plan"

    # 4) إشارة ترتيبية أخرى: «الاول» = أول عنصر بنفس القائمة الأصلية
    first_place_id = recs[0].place_id
    r4 = send(sid, "ضيف الاول للخطة")
    session4 = _session_store.load(sid)
    assert first_place_id in session4.state.saved_place_ids
    assert {first_place_id, second_place_id} <= set(session4.state.saved_place_ids)

    # 5) بناء خطة بالمحفوظات — التصنيف/الميزانية/المجموعة موروثة من البحث،
    # وبقيت المدة فقط → تُبنى مباشرةً عند ذكرها
    r5 = send(sid, "رتبلي خطة يومين مع عيلتي")
    assert r5["plan"] is not None, r5["reply"]
    assert len(r5["plan"]["days"]) == 2
    plan_place_ids = {s["place_id"] for day in r5["plan"]["days"] for s in day["stops"]}
    assert first_place_id in plan_place_ids
    assert second_place_id in plan_place_ids
    session5 = _session_store.load(sid)
    assert session5.memory.current_plan is not None
    assert session5.memory.last_bot_action == "showed_plan"

    # 6) تعديل جراحي: حذف مكان بالاسم من الخطة
    # نحذف المكان الأول (بالاسم العربي) — يجب أن يبقى الثاني بالخطة (تعديل جراحي لا يمسح كل شيء)
    first_name_ar = next(
        c["name_ar"] for c in r1["cards"] if c["place_id"] == first_place_id
    )
    r6 = send(sid, f"شيل {first_name_ar} من الخطة")
    assert r6["plan"] is not None, r6["reply"]
    remaining_ids = {s["place_id"] for day in r6["plan"]["days"] for s in day["stops"]}
    assert first_place_id not in remaining_ids
    session6 = _session_store.load(sid)
    assert session6.memory.last_bot_action == "updated_plan"


def test_details_only_flow_never_triggers_planning():
    """سيناريو 3 بـ spec.md §5: سؤال تفاصيل صرف لا يفتح أسئلة التخطيط أبدًا."""
    sid = "flow-details-only"

    r1 = send(sid, "اقترحلي متاحف بدمشق لعيلتي واقتصادي")
    assert r1["cards"]

    r2 = send(sid, "شو اوقات دوام المتحف الوطني؟")
    assert r2["cards"] is not None
    assert r2["cards"][0]["place_id"] == "p07"
    assert r2["plan"] is None

    session = _session_store.load(sid)
    assert session.memory.current_plan is None  # التخطيط لم يُستدعَ إطلاقًا
    assert session.memory.last_mentioned_place == "p07"


def test_reject_flow_excludes_shown_places_from_next_search():
    """سيناريو 8: رفض → تشخيص → استبعاد المعروضات من أي بحث لاحق."""
    sid = "flow-reject"

    r1 = send(sid, "اقترحلي أماكن تاريخيه بدمشق لعيلتي واقتصادي")
    shown_ids = {c["place_id"] for c in r1["cards"]}

    r2 = send(sid, "ما عجبوني")
    assert r2["reply"]  # سؤال تشخيصي — قيمة فعلية بالرد
    session = _session_store.load(sid)
    assert shown_ids <= set(session.state.excluded_place_ids)

    r3 = send(sid, "اقترحلي أماكن تانيه بدمشق")
    new_ids = {c["place_id"] for c in (r3["cards"] or [])}
    assert new_ids.isdisjoint(shown_ids)


def test_multi_turn_plan_gathering_with_short_answers():
    """جمع الخطة الكامل عبر أدوار بأجوبة مقتضبة (مدينة → تصنيف → ميزانية →
    مجموعة → مدة) — كل جواب يُستأنف المسار لا يكسره، وتُبنى الخطة عند الاكتمال.
    (طلب المالك: "لازم ترجع تسأل عن البقية")."""
    sid = "flow-multiturn-plan"

    r1 = send(sid, "بدي اعمل رحلة")
    assert r1["plan"] is None and r1["reply"]  # سؤال عن الوجهة

    r2 = send(sid, "لحلب")  # جواب مقتضب — يُستأنف الجمع لا يضيع
    assert r2["plan"] is None
    s2 = _session_store.load(sid)
    assert s2.state.destination == ["حلب"]
    assert s2.memory.pending_intent == "build_plan"

    r3 = send(sid, "تاريخيه")   # التصنيف
    assert r3["plan"] is None
    r4 = send(sid, "متوسط")      # الميزانية
    assert r4["plan"] is None
    r5 = send(sid, "مع عيلتي")   # المجموعة
    assert r5["plan"] is None

    r6 = send(sid, "تلات ايام")  # آخر ناقص (المدة) → تُبنى الخطة الآن
    assert r6["plan"] is not None, r6["reply"]
    assert len(r6["plan"]["days"]) == 3
    s6 = _session_store.load(sid)
    assert s6.state.group_type == "family"
    assert s6.state.budget_level == "medium"
    assert "tag:historical" in s6.state.interests
    assert s6.memory.pending_intent is None
    assert s6.memory.last_bot_action == "showed_plan"


def test_language_follows_last_message_within_same_session():
    sid = "flow-language-switch"
    send(sid, "اقترحلي مطاعم بحلب")
    session_ar = _session_store.load(sid)
    assert session_ar.state.language == "ar"

    send(sid, "suggest restaurants in aleppo")
    session_en = _session_store.load(sid)
    assert session_en.state.language == "en"
