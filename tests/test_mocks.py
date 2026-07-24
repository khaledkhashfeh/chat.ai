"""اختبارات مطابقة النسخ الوهمية (mocks/recommender.py و mocks/planner.py) لعقد
docs/contract.md: نفس أسماء الحقول والبنية والالتزامات (استبعاد excluded_place_ids،
الأسماء العربية والإنكليزية معًا، إلخ).

الأدوات async — نستدعيها عبر asyncio.run() بدل pytest-asyncio لأن هذه المكتبة
غير مدرجة بقائمة CLAUDE.md المسموحة (asyncio من المكتبة القياسية فقط).
"""
import asyncio
from datetime import datetime

import pytest

from app.mocks import planner, recommender
from app.shared.models import ConversationState, ErrorObject, Modification, SearchScope


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# recommender.search
# ---------------------------------------------------------------------------


def test_search_respects_excluded_place_ids():
    state = ConversationState(language="ar", excluded_place_ids=["p01"])
    resp = run(recommender.search(query="", state=state, top_k=20))
    ids = [c.place_id for c in resp.results]
    assert "p01" not in ids


def test_search_filters_by_state_destination_city():
    state = ConversationState(language="ar", destination=["اللاذقية"])
    resp = run(recommender.search(query="", state=state, top_k=20))
    assert all(c.city == "اللاذقية" for c in resp.results)
    assert len(resp.results) > 0


def test_search_scope_overrides_state_destination():
    state = ConversationState(language="ar", destination=["دمشق"])
    resp = run(recommender.search(query="", state=state, top_k=20, scope=SearchScope(city="حلب")))
    assert all(c.city == "حلب" for c in resp.results)


def test_search_results_have_bilingual_names_and_ranked():
    state = ConversationState(language="ar")
    resp = run(recommender.search(query="", state=state, top_k=8))
    assert len(resp.results) == 8
    for card in resp.results:
        assert card.name_ar
        assert card.name_en
        assert card.recommendation_reason
    assert resp.fallback is None


def test_search_no_results_returns_fallback_not_error():
    state = ConversationState(language="ar", destination=["مدينة_وهمية_غير_موجودة"])
    resp = run(recommender.search(query="", state=state, top_k=8))
    assert resp.results == []
    assert resp.fallback is not None
    assert resp.fallback.suggestion.action == "expand_search"


def test_search_language_affects_ranking_note():
    ar_resp = run(recommender.search(query="", state=ConversationState(language="ar"), top_k=1))
    en_resp = run(recommender.search(query="", state=ConversationState(language="en"), top_k=1))
    assert ar_resp.ranking_note != en_resp.ranking_note


# ---------------------------------------------------------------------------
# recommender.details
# ---------------------------------------------------------------------------


def test_details_known_place_arabic():
    result = run(recommender.details(place_id="p01", language="ar"))
    assert not isinstance(result, ErrorObject)
    assert result.place.place_id == "p01"
    assert result.place.name_ar == "قلعة دمشق"
    assert result.description


def test_details_unknown_place_returns_error_object():
    result = run(recommender.details(place_id="p999", language="ar"))
    assert isinstance(result, ErrorObject)
    assert result.error.type == "no_results"


def test_details_closed_monday_for_museum():
    # المتحف الوطني (p07) مغلق يوم الاثنين — الاثنين هنا 2026-07-27
    monday = datetime(2026, 7, 27, 10, 0)
    assert monday.weekday() == 0  # Monday
    result = run(recommender.details(place_id="p07", language="ar", now=monday))
    assert result.opening_hours.mon is None
    assert result.open_now is False


def test_details_open_now_true_within_hours():
    monday = datetime(2026, 7, 27, 10, 0)
    result = run(recommender.details(place_id="p01", language="ar", now=monday))
    assert result.open_now is True


def test_details_description_switches_with_language():
    ar = run(recommender.details(place_id="p01", language="ar"))
    en = run(recommender.details(place_id="p01", language="en"))
    assert ar.description != en.description


# ---------------------------------------------------------------------------
# recommender.compare
# ---------------------------------------------------------------------------


def test_compare_requires_between_2_and_4_places():
    state = ConversationState(language="ar")
    too_few = run(recommender.compare(place_ids=["p01"], state=state, language="ar"))
    assert isinstance(too_few, ErrorObject)
    assert too_few.error.type == "missing_input"

    too_many = run(recommender.compare(place_ids=["p01", "p02", "p03", "p04", "p05"], state=state, language="ar"))
    assert isinstance(too_many, ErrorObject)


def test_compare_unknown_place_returns_error():
    state = ConversationState(language="ar")
    result = run(recommender.compare(place_ids=["p01", "p999"], state=state, language="ar"))
    assert isinstance(result, ErrorObject)
    assert result.error.type == "no_results"


def test_compare_returns_all_required_axes_and_verdict():
    state = ConversationState(language="ar", group_type="family")
    result = run(recommender.compare(place_ids=["p01", "p05"], state=state, language="ar"))
    axis_names = {a.axis for a in result.axes}
    assert axis_names == {"rating", "group_fit", "visit_duration_min", "distance_km", "price_level"}
    assert result.verdict.winner_place_id in ("p01", "p05")
    assert result.verdict.reason


# ---------------------------------------------------------------------------
# recommender.log / profile
# ---------------------------------------------------------------------------


def test_log_returns_ok_true():
    resp = run(recommender.log(user_id="u1", place_id="p01", event="added_to_plan", source="chat"))
    assert resp.ok is True


def test_profile_new_user_returns_empty_defaults():
    profile = run(recommender.profile(user_id="brand_new_user_xyz"))
    assert profile.top_tags == []
    assert profile.visited_cities == []
    assert profile.usual_group_type is None
    assert profile.last_activity is None


def test_profile_known_demo_user_has_history():
    profile = run(recommender.profile(user_id="u_demo"))
    assert profile.last_activity is not None
    assert profile.usual_group_type == "family"


# ---------------------------------------------------------------------------
# planner.build
# ---------------------------------------------------------------------------


def test_build_requires_minimum_state_fields():
    state = ConversationState(language="ar")  # لا وجهة ولا مدة ولا مجموعة
    result = run(planner.build(state=state))
    assert isinstance(result, ErrorObject)
    assert result.error.type == "missing_input"


def test_build_creates_correct_number_of_days():
    state = ConversationState(language="ar", destination=["دمشق"], duration_days=3, group_type="family")
    plan = run(planner.build(state=state, mandatory_place_ids=["p01", "p02"]))
    assert not isinstance(plan, ErrorObject)
    assert len(plan.days) == 3


def test_build_includes_all_mandatory_places():
    state = ConversationState(language="ar", destination=["دمشق"], duration_days=2, group_type="family")
    plan = run(planner.build(state=state, mandatory_place_ids=["p01", "p02"]))
    all_place_ids = {stop.place_id for day in plan.days for stop in day.stops}
    assert "p01" in all_place_ids
    assert "p02" in all_place_ids


def test_build_stops_have_bilingual_names_and_why_here():
    state = ConversationState(language="ar", destination=["دمشق"], duration_days=1, group_type="solo")
    plan = run(planner.build(state=state, mandatory_place_ids=["p01"]))
    visit_stops = [s for day in plan.days for s in day.stops if s.stop_type == "visit"]
    for s in visit_stops:
        assert s.name_ar and s.name_en and s.why_here


# ---------------------------------------------------------------------------
# planner.modify
# ---------------------------------------------------------------------------


def test_modify_unknown_plan_id_returns_error():
    result = run(planner.modify(plan_id="pl_does_not_exist", modification=Modification(type="remove", target_place_id="p01")))
    assert isinstance(result, ErrorObject)
    assert result.error.type == "unsolvable"


def test_modify_remove_only_touches_affected_day():
    state = ConversationState(language="ar", destination=["دمشق"], duration_days=2, group_type="family")
    plan = run(planner.build(state=state, mandatory_place_ids=["p01"], candidate_place_ids=["p13"]))
    assert not isinstance(plan, ErrorObject)

    day2_before = [s.place_id for s in plan.days[1].stops]

    result = run(planner.modify(plan_id=plan.plan_id, modification=Modification(type="remove", target_place_id="p01")))
    assert not isinstance(result, ErrorObject)
    assert len(result.changes) == 1

    remaining_ids = {s.place_id for day in result.plan.days for s in day.stops}
    assert "p01" not in remaining_ids
    # اليوم الثاني (غير المتأثر) يجب ألا يتغير محتواه
    day2_after = [s.place_id for s in result.plan.days[1].stops]
    assert day2_after == day2_before


def test_modify_shrink_days_reports_dropped_places_transparently():
    state = ConversationState(language="ar", destination=["دمشق"], duration_days=2, group_type="family")
    plan = run(planner.build(state=state, mandatory_place_ids=["p01"], candidate_place_ids=["p13", "p07"]))
    result = run(planner.modify(plan_id=plan.plan_id, modification=Modification(type="shrink_days")))
    assert not isinstance(result, ErrorObject)
    assert len(result.plan.days) == 1
    assert result.changes  # لا إخفاء صامت للأثر


# ---------------------------------------------------------------------------
# planner.feasibility
# ---------------------------------------------------------------------------


def test_feasibility_comfortable():
    result = run(planner.feasibility(place_ids=["p01", "p02"], duration_days=2, group_type="solo"))
    assert result.verdict == "comfortable"
    assert result.suggestion is None


def test_feasibility_tight_suggests_extend_days():
    result = run(planner.feasibility(place_ids=["p01", "p02", "p03", "p04", "p05", "p06"], duration_days=2, group_type="solo"))
    assert result.verdict == "tight"
    assert result.suggestion is not None
    assert result.suggestion.action == "extend_days"


def test_feasibility_unrealistic():
    result = run(
        planner.feasibility(
            place_ids=["p01", "p02", "p03", "p04", "p05", "p06", "p08", "p09"],
            duration_days=2,
            group_type="solo",
        )
    )
    assert result.verdict == "unrealistic"
