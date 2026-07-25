"""اختبارات مستخرج الكيانات — قواميس ومدة، وفق docs/spec.md §3-[4]."""
from app.conversation.entities import extract_entities
from app.conversation.normalizer import normalize


def norm_extract(text: str) -> dict:
    return extract_entities(normalize(text))


def test_city_by_official_name():
    assert norm_extract("بدي روح ع حلب")["destination"] == ["حلب"]


def test_city_by_colloquial_alias_alsham_for_damascus():
    assert norm_extract("بدي روح عالشام")["destination"] == ["دمشق"]


def test_city_english():
    assert norm_extract("i want to visit latakia") == {"destination": ["اللاذقية"]}


def test_duration_numeric_arabic_digits():
    assert norm_extract("بدي رحلة ٣ ايام")["duration_days"] == 3


def test_duration_word_two_days():
    assert norm_extract("بدي رحلة يومين") == {"duration_days": 2}


def test_duration_word_one_day():
    assert norm_extract("رحلة يوم واحد") == {"duration_days": 1}


def test_duration_word_week():
    assert norm_extract("رحلة اسبوع كامل") == {"duration_days": 7}


def test_duration_english_days():
    assert norm_extract("plan for 5 days") == {"duration_days": 5}


def test_group_family_from_kids_mention():
    # "اطفالي" يفعّل نوع المجموعة (عائلة) كما يفعّل وسم الاهتمام family_fun معًا — سلوك متوقع
    result = norm_extract("رايح مع اطفالي")
    assert result["group_type"] == "family"


def test_group_couple_from_wife_mention():
    assert norm_extract("رايح مع مرتي") == {"group_type": "couple"}


def test_group_solo():
    assert norm_extract("رايح لحالي") == {"group_type": "solo"}


def test_group_friends_english():
    assert norm_extract("traveling with friends") == {"group_type": "friends"}


def test_budget_low():
    assert norm_extract("بدي رحله اقتصاديه رخيصه") == {"budget_level": "low"}


def test_budget_high():
    assert norm_extract("بدي فندق فخم وفاخر") == {"budget_level": "high"}


def test_interests_multiple_tags():
    result = norm_extract("بدي اماكن تاريخيه وبحريه")
    assert set(result["interests"]) == {"tag:historical", "tag:sea"}


def test_interests_food_and_market():
    result = norm_extract("بدي زور سوق واكل مطاعم حلوه")
    assert "tag:market" in result["interests"]
    assert "tag:food" in result["interests"]


def test_no_entities_found_returns_empty_dict():
    assert norm_extract("مرحبا كيفك اليوم") == {}


def test_acceptance_sentence_from_claude_md():
    # المثال المرجعي بـ CLAUDE.md: destination=دمشق، duration=3، group=family، budget=low
    result = norm_extract("رايحين ٣ أيام عالشام مع الولاد وميزانيتنا عقد الحال")
    assert result["destination"] == ["دمشق"]
    assert result["duration_days"] == 3
    assert result["group_type"] == "family"
    assert result["budget_level"] == "low"


def test_mixed_language_message():
    result = norm_extract("بدي اروح ع aleppo مع family لمدة 4 days")
    assert result["destination"] == ["حلب"]
    assert result["group_type"] == "family"
    assert result["duration_days"] == 4


def test_occasion_honeymoon_maps_to_couple_group_no_new_field():
    # المناسبات الخاصة تُطوى على group_type الموجود (docs/spec.md §3-[4]) — لا حقل occasion منفصل
    assert norm_extract("بدنا نعمل شهر عسل") == {"group_type": "couple"}


def test_occasion_anniversary_english_maps_to_couple_group():
    assert norm_extract("we want a trip for our anniversary") == {"group_type": "couple"}


def test_occasion_graduation_trip_maps_to_friends_group():
    assert norm_extract("بدنا رحلة تخرج") == {"group_type": "friends"}


def test_climate_escape_heat_maps_to_nature_tag_no_new_tag():
    # الهروب من الطقس يُطوى على tag:nature/tag:sea الموجودين (docs/spec.md §3-[4]) — لا وسم جديد
    result = norm_extract("هربان من الحر بدي جو بارد")
    assert result["interests"] == ["tag:nature"]


def test_climate_warm_sun_maps_to_sea_tag():
    result = norm_extract("بدي دفا وشمس")
    assert result["interests"] == ["tag:sea"]
