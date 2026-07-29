"""tests/test_context_extractor.py — عقد conversation_context_v1 (طبقة المحادثة → Laravel).

يغطّي: تحويل النوايا 9→6، بناء القيود (محافظة/مدينة/ميزانية)، الوسوم المصنَّفة
{tag, tag_type, weight}، سياق الرحلة، الاستبعادات والنفي، تنظيف query_text،
ومنطق نقص المعلومات + سؤال التوضيح. المثالان الحرفيان من مواصفة المالك يُختبران
كحالتَي قبول.
"""
from app.conversation import context_extractor as ctx_mod
from app.conversation.context_extractor import extract_context
from app.conversation.normalizer import normalize
from app.shared.models import ContextFilters, ContextTag


def tag_weight(tags: list[ContextTag], tag_type: str, tag: str | None = None):
    """يرجع وزن أول وسم مطابق tag_type (واختياريًا tag) أو None إن لم يوجد."""
    for t in tags:
        if t.tag_type == tag_type and (tag is None or t.tag == tag):
            return t.weight
    return None


def has_tag(tags: list[ContextTag], tag_type: str, tag: str) -> bool:
    return any(t.tag_type == tag_type and t.tag == tag for t in tags)


# ---------------------------------------------------------------------------
# تحويل النوايا 9→6 (منطق صرف — لا يعتمد على المصنّف الاحتمالي)
# ---------------------------------------------------------------------------


def test_intent_map_covers_all_nine_internal_intents():
    assert ctx_mod._map_intent("search", "اقترحلي اماكن") == "recommend_places"
    assert ctx_mod._map_intent("details", "شو قصته") == "place_details"
    assert ctx_mod._map_intent("compare", "ايهن احسن") == "general_question"
    assert ctx_mod._map_intent("build_plan", "اعمل خطه") == "create_plan"
    assert ctx_mod._map_intent("modify_plan", "شيل المتحف") == "modify_plan"
    assert ctx_mod._map_intent("add_to_plan", "ضيفه للخطه") == "modify_plan"
    assert ctx_mod._map_intent("reject", "ما عجبوني") == "recommend_places"
    assert ctx_mod._map_intent("greeting_thanks", "مرحبا") == "general_question"
    assert ctx_mod._map_intent("out_of_scope", "احجزلي طيران") == "general_question"


def test_explicit_search_verb_becomes_search_places():
    assert ctx_mod._map_intent("search", normalize("دورلي مطاعم")) == "search_places"
    assert ctx_mod._map_intent("search", normalize("ابحث عن اماكن")) == "search_places"
    # بلا فعل بحث صريح → توصية استباقية
    assert ctx_mod._map_intent("search", normalize("اقترحلي اماكن")) == "recommend_places"


def test_low_confidence_defaults_intent_to_recommend_places():
    # جملة مبهمة بلا وجهة ولا اهتمامات (المثال الثاني بالعقد)
    ctx = extract_context("أريد مكاناً مناسباً غداً.")
    assert ctx.intent == "recommend_places"
    assert ctx.requires_clarification is True


# ---------------------------------------------------------------------------
# القيود (filters)
# ---------------------------------------------------------------------------


def test_governorate_mapping_capital_city_leaves_city_null():
    ctx = extract_context("اقترحلي اماكن بدمشق")
    assert ctx.filters.governorate == "Damascus"
    assert ctx.filters.city is None


def test_town_maps_to_governorate_and_city():
    ctx = extract_context("شو في ببصرى")
    assert ctx.filters.governorate == "Daraa"
    assert ctx.filters.city == "Bosra"


def test_interest_tags_produce_typed_tags_not_flat_category():
    """قرار المالك: لا filters.category — الوسوم تُخرَج بصيغة {tag, tag_type} صريحة."""
    assert has_tag(extract_context("اقترحلي اماكن تاريخيه بحلب").tags, "heritage", "تاريخي")
    assert has_tag(extract_context("بدي مطاعم بحلب").tags, "food", "طعام")
    assert has_tag(extract_context("بدي متاحف بدمشق").tags, "heritage", "متحف")


def test_budget_tier_detected_from_text():
    assert extract_context("بدي مكان مجاني بدمشق").filters.budget_tier == "free"
    assert extract_context("بدي مطعم رخيص بحلب").filters.budget_tier == "cheap"
    assert extract_context("بدي فندق فخم بدمشق").filters.budget_tier == "expensive"


def test_no_signals_leaves_filters_empty():
    filters = extract_context("مرحبا شلونك").filters
    assert filters == ContextFilters()


# ---------------------------------------------------------------------------
# الوسوم المصنَّفة {tag, tag_type, weight} — تحل محل preferences+category القديمين
# ---------------------------------------------------------------------------


def test_tags_weighted_from_interests_and_group():
    tags = extract_context("اقترحلي اماكن تاريخيه لعيلتي بدمشق").tags
    assert tag_weight(tags, "heritage", "تاريخي") == 1.0
    assert tag_weight(tags, "audience", "عائلي") == 0.8


def test_quiet_becomes_positive_vibe_tag():
    """الوسم يمثّل الآن ما يريده المستخدم مباشرة (لا مفتاح سالب مقابل مثل crowdedness)."""
    tags = extract_context("بدي مكان هادئ بدمشق").tags
    assert tag_weight(tags, "vibe", "هادئ") == 0.9


def test_photography_keyword_adds_activity_tag():
    tags = extract_context("بدي مكان حلو للتصوير بحلب").tags
    assert tag_weight(tags, "activity", "تصوير") == 0.9


def test_couple_group_adds_romantic_vibe_tag():
    tags = extract_context("بدي مكان مع خطيبتي بدمشق").tags
    assert tag_weight(tags, "vibe", "رومانسي") == 0.8


# ---------------------------------------------------------------------------
# سياق الرحلة (trip_context)
# ---------------------------------------------------------------------------


def test_group_type_extracted():
    assert extract_context("بدي اماكن مع اصحابي بحلب").trip_context.group_type == "friends"


def test_physical_difficulty_inferred():
    assert extract_context("بدي اماكن مغامره بطرطوس").trip_context.physical_difficulty == "moderate"
    assert extract_context("بدي اماكن هاديه لعيلتي بدمشق").trip_context.physical_difficulty == "easy"


def test_preferred_time_and_season():
    assert extract_context("بدي طلعه الصبح بدمشق").trip_context.preferred_time == "morning"
    assert extract_context("وين اروح بالصيف بطرطوس").trip_context.season == "summer"


def test_duration_minutes_from_hours():
    assert extract_context("بدي مكان لساعتين بدمشق").trip_context.duration_minutes == 120
    assert extract_context("بدي زياره 3 ساعات بحلب").trip_context.duration_minutes == 180


# ---------------------------------------------------------------------------
# الاستبعادات والنفي (exclusions)
# ---------------------------------------------------------------------------


def test_negated_category_is_excluded_not_positive():
    ctx = extract_context("بدي مكان طبيعي بطرطوس بدون متاحف")
    assert "متحف" in ctx.exclusions.categories  # التسمية العربية (تطابق tags الآن)
    assert not has_tag(ctx.tags, "heritage", "متحف")  # النفي لا يصير وسمًا إيجابيًا


def test_accessibility_requirement_detected():
    ctx = extract_context("بدي مكان مناسب لكرسي متحرك بدمشق")
    assert "wheelchair_accessible" in ctx.exclusions.requirements


# ---------------------------------------------------------------------------
# تنظيف query_text
# ---------------------------------------------------------------------------


def test_query_text_strips_request_verb_and_structured_filters():
    q = extract_context("أريد مكاناً أثرياً هادئاً ومجانياً في دمشق مناسباً للعائلة والتصوير").query_text
    # لا لفظ طلب، ولا مدينة، ولا لفظ ميزانية (صارت قيودًا منظمة)
    assert "اريد" not in q
    assert "دمشق" not in q
    assert "مجاني" not in q
    # يبقى جوهر الوصف الدلالي
    assert "اثري" in q and "للعائله" in q


def test_query_text_removes_orphan_preposition_after_city():
    q = extract_context("plan a trip to latakia for my family").query_text
    assert "latakia" not in q
    assert " to " not in f" {q} "  # حرف الجر اليتيم أُزيل


def test_query_text_falls_back_when_everything_stripped():
    # لو صار النص كله ألفاظ محذوفة، لا نرجع فراغًا
    q = extract_context("بدمشق").query_text
    assert q  # غير فارغ


# ---------------------------------------------------------------------------
# نقص المعلومات + التوضيح
# ---------------------------------------------------------------------------


def test_recommend_missing_location_and_interests_asks_combined():
    ctx = extract_context("أريد مكاناً مناسباً غداً.")
    assert set(ctx.missing_information) == {"governorate_or_city", "trip_interests"}
    assert ctx.requires_clarification is True
    assert ctx.clarification_question  # سؤال واحد مركّب


def test_recommend_with_enough_info_needs_no_clarification():
    ctx = extract_context("بدي اماكن تاريخيه بدمشق")
    assert ctx.missing_information == []
    assert ctx.requires_clarification is False
    assert ctx.clarification_question is None


def test_create_plan_missing_duration_and_group():
    ctx = extract_context("اعملي خطه بدمشق")
    assert "duration" in ctx.missing_information
    assert "group_type" in ctx.missing_information
    assert ctx.requires_clarification is True


def test_clarification_question_follows_language():
    assert extract_context("I want a nice place tomorrow").clarification_question is not None
    en_q = extract_context("I want a nice place tomorrow").clarification_question
    assert any(w in en_q.lower() for w in ("governorate", "city", "places"))


# ---------------------------------------------------------------------------
# اللغة
# ---------------------------------------------------------------------------


def test_language_detected_and_overridable():
    assert extract_context("suggest historical places in aleppo").language == "en"
    assert extract_context("اقترحلي اماكن بحلب").language == "ar"
    # تجاوز صريح
    assert extract_context("اقترحلي اماكن بحلب", language="en").language == "en"


# ---------------------------------------------------------------------------
# حالتا القبول: المثالان الحرفيان من مواصفة المالك
# ---------------------------------------------------------------------------


def test_owner_example_1_full_request():
    ctx = extract_context("أريد مكاناً أثرياً هادئاً ومجانياً في دمشق، مناسباً للعائلة والتصوير.")
    assert ctx.contract_version == "conversation_context_v1"
    assert ctx.intent == "recommend_places"
    assert ctx.filters.governorate == "Damascus"
    assert ctx.filters.budget_tier == "free"
    assert tag_weight(ctx.tags, "heritage", "تاريخي") == 1.0
    assert tag_weight(ctx.tags, "audience", "عائلي") == 0.8
    assert tag_weight(ctx.tags, "activity", "تصوير") == 0.9
    assert tag_weight(ctx.tags, "vibe", "هادئ") == 0.9
    assert ctx.trip_context.group_type == "family"
    assert ctx.requires_clarification is False


def test_owner_example_2_missing_info():
    ctx = extract_context("أريد مكاناً مناسباً غداً.")
    assert ctx.intent == "recommend_places"
    assert ctx.filters == ContextFilters()
    assert ctx.tags == []
    assert ctx.requires_clarification is True
    assert set(ctx.missing_information) == {"governorate_or_city", "trip_interests"}
    assert ctx.clarification_question is not None
