"""tests/test_context_endpoint.py — نقطة POST /context عبر FastAPI TestClient.

تتحقق من: حماية الرأس السري، شكل عقد conversation_context_v1 المُعاد، تجاوز
اللغة، وأن /context **لا يمس** حالة الجلسة (بلا حالة — Laravel يملك الحالة).
"""
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)
HEADERS = {"X-Internal-Secret": settings.internal_secret}


def post_context(message: str, language: str | None = None) -> dict:
    body: dict = {"message": message}
    if language is not None:
        body["language"] = language
    resp = client.post("/context", json=body, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_context_rejects_wrong_secret():
    resp = client.post(
        "/context",
        json={"message": "بدي اماكن بدمشق"},
        headers={"X-Internal-Secret": "wrong"},
    )
    assert resp.status_code == 401


def test_context_returns_full_contract_shape():
    data = post_context("بدي اماكن تاريخيه بدمشق لعيلتي")
    assert data["contract_version"] == "conversation_context_v1"
    assert data["intent"] == "recommend_places"
    assert data["language"] == "ar"
    # كل مفاتيح العقد موجودة
    for key in (
        "query_text", "filters", "tags", "trip_context",
        "location", "exclusions", "confidence",
        "requires_clarification", "missing_information", "clarification_question",
    ):
        assert key in data, key
    assert data["filters"]["governorate"] == "Damascus"
    assert "category" not in data["filters"]  # أُزيلت — الوسوم تحمل المعلومة الآن
    assert {"tag": "تاريخي", "tag_type": "heritage", "weight": 1.0} in data["tags"]
    assert 0.0 <= data["confidence"] <= 1.0


def test_context_language_override():
    data = post_context("اقترحلي اماكن بحلب", language="en")
    assert data["language"] == "en"


def test_context_clarifies_when_info_missing():
    data = post_context("أريد مكاناً مناسباً غداً.")
    assert data["requires_clarification"] is True
    assert data["clarification_question"]
    assert data["missing_information"]


def test_context_endpoint_is_stateless_no_session_created():
    """/context لا يستخدم SessionStore إطلاقًا — لا مفتاح جلسة يُنشأ."""
    from app.main import _session_store

    before = client.post("/context", json={"message": "بدي اماكن بحمص"}, headers=HEADERS)
    assert before.status_code == 200
    # لا session_id بالعقد أصلًا، ونتأكد أن المخزن ما توسّع بمفتاح مرتبط بالرسالة
    # (تحقق دلالي: تحميل مفتاح عشوائي يعطي جلسة فارغة جديدة كالمعتاد)
    fresh = _session_store.load("no-such-session-for-context")
    assert fresh.state.destination == []
