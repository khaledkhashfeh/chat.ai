"""اختبارات محلّل الإشارات: ترتيبي، اسمي (مطابقة غامضة)، ضمير — وفق الأمثلة
الحرفية بـ docs/plan.md (الجلسة 4)."""
from app.conversation.normalizer import normalize
from app.conversation.resolver import resolve_references
from app.shared.models import CostEstimate, PlanDay, PlanObject, PlanStop, RecommendedPlaceRef, WorkingMemory


def make_memory_with_recs() -> WorkingMemory:
    return WorkingMemory(
        last_recommendations=[
            RecommendedPlaceRef(pos=1, place_id="p01", name_ar="قلعة دمشق", name_en="Damascus Citadel"),
            RecommendedPlaceRef(pos=2, place_id="p02", name_ar="الجامع الأموي", name_en="Umayyad Mosque"),
            RecommendedPlaceRef(pos=3, place_id="p03", name_ar="سوق الحميدية", name_en="Al-Hamidiyah Souq"),
        ],
        last_mentioned_place="p02",
    )


def make_plan_object() -> PlanObject:
    return PlanObject(
        plan_id="pl_test",
        summary_ar="خطة تجريبية", summary_en="test plan",
        total_cost_estimate=CostEstimate(activities=0, food=0, transport=0),
        days=[
            PlanDay(
                day_number=1, title_ar="اليوم الأول", day_cost_estimate=0,
                stops=[
                    PlanStop(
                        place_id="p05", name_ar="قلعة الحصن", name_en="Krak des Chevaliers",
                        stop_type="visit", arrival="09:00", departure="10:00",
                        visit_duration_min=60, travel_from_prev_min=0, cost_estimate=0,
                        why_here="سبب",
                    )
                ],
            )
        ],
    )


def test_ordinal_second_arabic():
    memory = make_memory_with_recs()
    ref = resolve_references(normalize("شو قصة التاني؟"), memory)
    assert ref == {"place_id": "p02", "source": "ordinal"}


def test_ordinal_last_english():
    memory = make_memory_with_recs()
    ref = resolve_references(normalize("tell me about the last one"), memory)
    assert ref["place_id"] == "p03"
    assert ref["source"] == "ordinal"


def test_ordinal_first_english():
    memory = make_memory_with_recs()
    ref = resolve_references(normalize("what about the first one"), memory)
    assert ref["place_id"] == "p01"
    assert ref["source"] == "ordinal"


def test_fuzzy_name_with_typo_missing_letter():
    memory = make_memory_with_recs()
    ref = resolve_references(normalize("سو الحميديه"), memory)  # ناقصة القاف من "سوق"
    assert ref["place_id"] == "p03"
    assert ref["source"] == "name"


def test_fuzzy_name_partial_arabic_from_plan():
    memory = WorkingMemory(current_plan=make_plan_object())
    ref = resolve_references(normalize("الحصن"), memory)
    assert ref["place_id"] == "p05"
    assert ref["source"] == "name"


def test_fuzzy_name_partial_english_from_plan():
    memory = WorkingMemory(current_plan=make_plan_object())
    ref = resolve_references(normalize("krak"), memory)
    assert ref["place_id"] == "p05"
    assert ref["source"] == "name"


def test_pronoun_arabic():
    memory = make_memory_with_recs()
    ref = resolve_references(normalize("عنه"), memory)
    assert ref == {"place_id": "p02", "source": "pronoun"}


def test_pronoun_english_short_it_does_not_false_match_a_name():
    memory = make_memory_with_recs()
    ref = resolve_references(normalize("it"), memory)
    assert ref == {"place_id": "p02", "source": "pronoun"}


def test_no_reference_returns_empty_dict():
    memory = make_memory_with_recs()
    ref = resolve_references(normalize("اقترحلي اماكن جديدة بحلب"), memory)
    assert ref == {}


def test_empty_memory_returns_empty_dict():
    memory = WorkingMemory()
    ref = resolve_references(normalize("شو قصة التاني"), memory)
    assert ref == {}


def test_reference_resolves_against_plan_places_not_only_recommendations():
    memory = WorkingMemory(current_plan=make_plan_object())
    ref = resolve_references(normalize("krak"), memory)
    assert ref["place_id"] == "p05"


def test_ordinal_does_not_apply_to_plan_only_memory():
    # لا last_recommendations هنا — الترتيبية لا تنطبق على أماكن الخطة إطلاقًا
    memory = WorkingMemory(current_plan=make_plan_object())
    ref = resolve_references(normalize("شيل التاني من الخطة"), memory)
    assert ref == {}
