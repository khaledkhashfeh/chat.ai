"""اختبارات مستخرج الكيانات — قواميس ومدة، وفق docs/spec.md §3-[4]."""
from datetime import date

from app.conversation.entities import extract_entities, find_start_date
from app.conversation.normalizer import normalize


def norm_extract(text: str) -> dict:
    return extract_entities(normalize(text))


def test_city_by_official_name():
    assert norm_extract("بدي روح ع حلب")["destination"] == ["حلب"]


def test_city_by_colloquial_alias_alsham_for_damascus():
    assert norm_extract("بدي روح عالشام")["destination"] == ["دمشق"]


def test_city_english():
    assert norm_extract("i want to visit latakia") == {"destination": ["اللاذقية"]}


def test_multiple_destinations_in_one_message():
    """طلب المالك: يستطيع استخراج أكتر من وجهة بنفس الرسالة («دمشق وحلب»)."""
    assert norm_extract("بدي روح دمشق وحلب")["destination"] == ["دمشق", "حلب"]


def test_multiple_destinations_preserve_mention_order():
    assert norm_extract("بدي زور حلب واللاذقية")["destination"] == ["حلب", "اللاذقية"]


def test_friend_group_alternate_spelling_asdiqai():
    """«اصدقائي» (بدل «اصحابي») يجب أن يُستخرج كـ friends أيضًا."""
    assert norm_extract("رايح مع اصدقائي")["group_type"] == "friends"


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


def test_owner_example_destination_and_group_in_one_message():
    """طلب المالك حرفيًا: "اريد الذهاب لدمشق مع اصدقائي" فيها معلومتان يجب
    استخلاصهما معًا بنداء واحد — الوجهة والمجموعة."""
    result = norm_extract("اريد الذهاب لدمشق مع اصدقائي")
    assert result["destination"] == ["دمشق"]
    assert result["group_type"] == "friends"


def test_occasion_honeymoon_maps_to_couple_group_no_new_field():
    # المناسبات الخاصة تُطوى على group_type الموجود (docs/spec.md §3-[4]) — لا حقل occasion منفصل.
    # «شهر عسل» تُستخرج أيضًا كـ trip_purpose=romantic (حقل مستقل، منطقي بنفس الوقت).
    result = norm_extract("بدنا نعمل شهر عسل")
    assert result["group_type"] == "couple"
    assert result["trip_purpose"] == "romantic"


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


# ---------------------------------------------------------------------------
# تاريخ بدء الرحلة — اختياري تمامًا، نسبي أو صريح (طلب المالك)
# ---------------------------------------------------------------------------

_ANCHOR = date(2026, 7, 26)  # اليوم الحالي بالمشروع (currentDate) — مرجع ثابت للاختبارات


def find_date(text: str) -> date | None:
    return find_start_date(normalize(text), today=_ANCHOR)


def test_start_date_tomorrow():
    assert find_date("بكرا") == date(2026, 7, 27)


def test_start_date_next_week():
    assert find_date("الاسبوع القادم") == date(2026, 8, 2)


def test_start_date_next_month_handles_day_overflow():
    """31 كانون الثاني + شهر → 28 شباط (لا 31 شباط غير الصالح) — تحقّق يدوي بلا مكتبات تواريخ."""
    assert find_start_date(normalize("الشهر القادم"), today=date(2026, 1, 31)) == date(2026, 2, 28)


def test_start_date_after_n_days_digit_and_written():
    assert find_date("بعد 5 ايام") == date(2026, 7, 31)
    assert find_date("بعد خمس ايام") == date(2026, 7, 31)  # رقم مكتوب لا رقمي فقط


def test_start_date_after_one_week_implicit_singular():
    """«بعد اسبوع» بلا رقم = اسبوع واحد ضمنيًا — يجب ألّا يسبقه فحص «اسبوعين»
    الأكثر تحديدًا خطأً (لأن «اسبوعين» تحوي «اسبوع» كجزء نصي)."""
    assert find_date("بعد اسبوع") == date(2026, 8, 2)
    assert find_date("3 ايام بعد اسبوع") == date(2026, 8, 2)


def test_start_date_after_two_weeks_dual_form():
    assert find_date("بعد اسبوعين") == date(2026, 8, 9)


def test_relative_start_date_does_not_leak_into_duration_days():
    """بق مكتشَف: «بعد 5 ايام» / «بعد اسبوعين» تاريخ بدء نسبي، لا مدة رحلة —
    كانتا تُقرآن خطأً كـ duration_days=5 / 14 بسبب تداخل الأنماط."""
    assert norm_extract("بدي ابلش بعد 5 ايام") == {}
    assert norm_extract("بدي ابلش بعد اسبوعين") == {}
    assert norm_extract("بدي ابلش بعد اسبوع") == {}  # المفرد الضمني أيضًا
    # مدة صريحة مرافقة لتاريخ بدء نسبي: «3 ايام» مدة حقيقية، و«اسبوع» بعد «بعد»
    # تاريخ لا مدة — يجب أن تبقى duration_days=3 فقط (لا 7 ولا تراكم خاطئ)
    assert norm_extract("3 ايام بعد اسبوع")["duration_days"] == 3
    # يبقى استخراج المدة الطبيعي (بلا «بعد») سليمًا
    assert norm_extract("بدي رحلة 5 ايام")["duration_days"] == 5
    assert norm_extract("بدي رحلة اسبوعين")["duration_days"] == 14


def test_start_date_explicit_gregorian_month_name():
    assert find_date("بدي روح 20 اكتوبر") == date(2026, 10, 20)


def test_start_date_explicit_levantine_month_name():
    assert find_date("بدي روح 5 تشرين الاول") == date(2026, 10, 5)


def test_start_date_explicit_past_date_rolls_to_next_year():
    """تاريخ صريح مضى هالسنة (1 كانون الثاني وإحنا بتموز) → يُفترض السنة الجاية."""
    assert find_date("بدي روح 1 يناير") == date(2027, 1, 1)


def test_start_date_none_when_not_mentioned():
    """اختياري تمامًا — بلا أي إشارة تاريخ، يرجع None (لا يُسأل عنه إطلاقًا)."""
    assert find_date("بدي اماكن تاريخيه بدمشق") is None


# ---------------------------------------------------------------------------
# trip_purpose / transport_mode / preferred_time — حقول docs/contract.md §1.1
# ---------------------------------------------------------------------------


def test_trip_purpose_leisure():
    assert norm_extract("بدي استجمام واسترخاء")["trip_purpose"] == "leisure"


def test_trip_purpose_adventure():
    assert norm_extract("بدي مغامرة ونشاط")["trip_purpose"] == "adventure"


def test_trip_purpose_independent_of_group_type():
    """trip_purpose حقل مستقل عن group_type — شخص لحاله ممكن يطلب family_fun."""
    result = norm_extract("لحالي بس بدي نشاط عائلي")
    assert result["group_type"] == "solo"
    assert result["trip_purpose"] == "family_fun"


def test_transport_mode_car():
    assert norm_extract("رايح بسيارتي")["transport_mode"] == "car"


def test_transport_mode_public_transport():
    assert norm_extract("رح اخد الباص")["transport_mode"] == "public_transport"


def test_transport_mode_walking():
    assert norm_extract("بفضل امشي عالماشي")["transport_mode"] == "walking"


def test_preferred_time_morning():
    assert norm_extract("بحب الروتين الصباحي")["preferred_time"] == "morning"


def test_preferred_time_afternoon():
    assert norm_extract("بفضل بعد الظهر")["preferred_time"] == "afternoon"


def test_preferred_time_evening():
    assert norm_extract("بحب البرنامج المسائي")["preferred_time"] == "evening"
