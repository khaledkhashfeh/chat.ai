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


def test_details_by_name_then_add_to_plan_via_pronoun_flow():
    """أهم تسلسل سياق ما زال قابلًا للتحقق عبر /chat الحقيقية بعد قرار المالك
    (طبقة المحادثة لا تعرض توصيات ولا خططًا — تجمع وتُسلّم فقط): تفاصيل مكان
    بالاسم من الكتالوج الكامل ← ضمير يشير له ← إضافته للمحفوظات."""
    sid = "flow-details-add"

    # 1) تفاصيل بالاسم مباشرةً (بلا سياق سابق) — النية الاستعلامية الوحيدة
    # التي ما زالت تستدعي أداة وتعرض نتيجتها كما هي (لا تخطيط، لا جمع)
    r1 = send(sid, "عطيني معلومات عن قلعة حلب")
    assert r1["cards"] is not None and r1["cards"][0]["place_id"] == "p11"
    assert r1["plan"] is None
    session1 = _session_store.load(sid)
    assert session1.memory.last_mentioned_place == "p11"
    assert session1.memory.pending_intent is None  # التفاصيل لا تفتح جمعًا

    # 2) ضمير: «ضيفو» يشير لآخر مكان مذكور (نتيجة الخطوة 1)
    r2 = send(sid, "حلو ضيفو")
    assert "p11" in (r2["state"]["saved_place_ids"] or [])
    session2 = _session_store.load(sid)
    assert session2.memory.last_bot_action == "added_to_plan"


def test_details_only_flow_never_triggers_planning():
    """سيناريو 3 بـ spec.md §5: سؤال تفاصيل صرف لا يفتح أسئلة التخطيط أبدًا،
    ولا يمسّ حالة جمع أي نية أخرى."""
    sid = "flow-details-only"

    r1 = send(sid, "شو اوقات دوام المتحف الوطني؟")
    assert r1["cards"] is not None
    assert r1["cards"][0]["place_id"] == "p07"
    assert r1["plan"] is None

    session = _session_store.load(sid)
    assert session.memory.current_plan is None  # التخطيط لم يُستدعَ إطلاقًا
    assert session.memory.pending_intent is None
    assert session.memory.last_mentioned_place == "p07"


def test_reject_with_no_prior_recommendations_still_asks_diagnostic_question():
    """سيناريو 8 (بعد قرار المالك — search ما عادت تعرض بطاقات فلا مقترحات
    سابقة لاستبعادها): الرفض يبقى يعمل بأمان ويسأل سؤالًا تشخيصيًا حقيقيًا."""
    sid = "flow-reject"
    r1 = send(sid, "ما عجبوني")
    assert r1["reply"]
    session = _session_store.load(sid)
    assert session.memory.last_bot_action == "asked_rejection_reason"


def test_multi_turn_plan_gathering_with_short_answers():
    """جمع الخطة الكامل عبر أدوار بأجوبة مقتضبة (الهدف → مدينة → تصنيف →
    ميزانية → مجموعة → مدة) — كل جواب يُستأنف المسار لا يكسره. عند الاكتمال:
    تأكيد جاهزية فقط بلا استدعاء planner.build وبلا خطة معروضة (قرار المالك).
    (طلب المالك الأصلي: "لازم ترجع تسأل عن البقية" — ما زال محقَّقًا هنا)."""
    sid = "flow-multiturn-plan"

    r1 = send(sid, "بدي اعمل رحلة")
    assert r1["plan"] is None and r1["reply"]  # سؤال عن الهدف (أعلى أولوية)

    r2 = send(sid, "بدي مغامرة")  # trip_purpose — جواب مقتضب يُستأنف الجمع لا يضيع
    assert r2["plan"] is None
    s2 = _session_store.load(sid)
    assert s2.state.trip_purpose == "adventure"
    assert s2.memory.pending_intent == "build_plan"

    r3 = send(sid, "لحلب")  # الوجهة
    assert r3["plan"] is None
    s3 = _session_store.load(sid)
    assert s3.state.destination == ["حلب"]

    r4 = send(sid, "تاريخيه")   # التصنيف
    assert r4["plan"] is None
    r5 = send(sid, "متوسط")      # الميزانية
    assert r5["plan"] is None
    r6 = send(sid, "مع عيلتي")   # المجموعة
    assert r6["plan"] is None

    r7 = send(sid, "تلات ايام")  # آخر ناقص (المدة) → تأكيد الجاهزية، بلا خطة
    assert r7["plan"] is None
    assert r7["reply"]
    s7 = _session_store.load(sid)
    assert s7.state.group_type == "family"
    assert s7.state.budget_level == "medium"
    assert "tag:historical" in s7.state.interests
    assert s7.state.duration_days == 3
    assert s7.memory.current_plan is None
    assert s7.memory.pending_intent is None
    assert s7.memory.last_bot_action == "gathered_info"


def test_language_follows_last_message_within_same_session():
    sid = "flow-language-switch"
    send(sid, "اقترحلي مطاعم بحلب")
    session_ar = _session_store.load(sid)
    assert session_ar.state.language == "ar"

    send(sid, "suggest restaurants in aleppo")
    session_en = _session_store.load(sid)
    assert session_en.state.language == "en"


def test_edit_flow_add_city_then_cancel_it_via_chat():
    """طلب المالك: تعديل المعلومات المجموعة عبر /chat الفعلية — إضافة وجهة
    (تراكميًا، كما تعمل أصلًا) ثم إلغاؤها صراحة («بدي الغي دمشق»)."""
    sid = "flow-edit-remove-destination"

    r1 = send(sid, "بدي روح دمشق")
    assert r1["plan"] is None
    session1 = _session_store.load(sid)
    assert session1.state.destination == ["دمشق"]

    r2 = send(sid, "وحلب كمان")
    session2 = _session_store.load(sid)
    assert session2.state.destination == ["دمشق", "حلب"]

    r3 = send(sid, "بدي الغي دمشق")
    assert r3["reply"]
    session3 = _session_store.load(sid)
    assert session3.state.destination == ["حلب"]
    assert session3.memory.last_bot_action == "removed_destination"


def test_reset_flow_via_chat_requires_confirmation_before_wiping():
    """طلب المالك: «اريد انشاء خطة جديدة» يُلغي المعلومات القديمة ويبدأ الجمع
    من جديد — لكن **بعد** سؤال تأكيد صريح فقط، لا مباشرة."""
    sid = "flow-reset-confirm"

    send(sid, "بدي روح دمشق")
    send(sid, "بدي مغامرة")
    session_before = _session_store.load(sid)
    assert session_before.state.destination == ["دمشق"]
    assert session_before.state.trip_purpose == "adventure"

    r_trigger = send(sid, "اريد انشاء خطة جديدة")
    assert r_trigger["reply"]
    session_pending = _session_store.load(sid)
    assert session_pending.memory.pending_confirmation == "reset_plan"
    # لا مسح قبل التأكيد
    assert session_pending.state.destination == ["دمشق"]
    assert session_pending.state.trip_purpose == "adventure"

    r_confirm = send(sid, "ايوا اكيد")
    assert r_confirm["reply"]
    session_after = _session_store.load(sid)
    assert session_after.state.destination == []
    assert session_after.state.trip_purpose is None
    assert session_after.memory.pending_confirmation is None
    assert session_after.memory.last_bot_action == "reset_confirmed"


def test_reset_flow_via_chat_decline_keeps_everything():
    sid = "flow-reset-decline"

    send(sid, "بدي روح دمشق")
    r_trigger = send(sid, "بدي خطة جديدة")
    assert r_trigger["reply"]

    r_decline = send(sid, "لا خليها")
    assert r_decline["reply"]
    session_after = _session_store.load(sid)
    assert session_after.state.destination == ["دمشق"]
    assert session_after.memory.pending_confirmation is None
    assert session_after.memory.last_bot_action == "reset_declined"
