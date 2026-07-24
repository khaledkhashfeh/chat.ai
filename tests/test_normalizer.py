"""اختبارات المطبِّع اللغوي وكاشف اللغة."""
from app.conversation.normalizer import detect_language, normalize


def test_alef_variants_unify():
    assert normalize("أحمد") == normalize("إحمد") == normalize("آحمد") == normalize("ٱحمد")


def test_taa_marbuta_and_diacritics_and_tatweel_unify():
    # قَلْعَة (بتشكيل) / قلعه (بدون) / قلعــة (بتطويل) → نفس الناتج
    a = normalize("قَلْعَة")
    b = normalize("قلعه")
    c = normalize("قلعــة")
    assert a == b == c == "قلعه"


def test_alef_maqsura_unifies_to_yaa():
    assert normalize("علي") == normalize("على")


def test_repeated_chars_collapse():
    assert normalize("حلوووو") == "حلوو"
    assert normalize("رهيبببببب") == "رهيبب"


def test_repeated_chars_two_occurrences_untouched():
    # تكرار حرفين فقط (مثل اللام المشددة بـ"الله") لا يُقلَّص (العتبة 3 فأكثر)
    assert normalize("الله") == "الله"


def test_arabic_digits_to_latin():
    assert normalize("٣ ايام") == "3 ايام"
    assert normalize("٠١٢٣٤٥٦٧٨٩") == "0123456789"


def test_multiple_spaces_collapse():
    assert normalize("بدي    اماكن   حلوه") == "بدي اماكن حلوه"


def test_strip_and_lowercase_latin():
    assert normalize("  Damascus CITADEL  ") == "damascus citadel"


def test_empty_string():
    assert normalize("") == ""


def test_trailing_punctuation_does_not_stick_to_last_word():
    # مهم لمحلّل الإشارات: "التاني؟" يجب أن تُطابَق ككلمة "التاني" كاملة
    assert normalize("شو قصة التاني؟") == "شو قصه التاني"
    assert normalize("is it open now?") == "is it open now"


def test_punctuation_marks_replaced_with_space():
    assert normalize("مرحبا، كيفك!") == "مرحبا كيفك"


def test_detect_language_arabic():
    assert detect_language("اقترحلي مطاعم بحلب") == "ar"


def test_detect_language_english():
    assert detect_language("suggest restaurants in Aleppo") == "en"


def test_detect_language_mixed_more_arabic():
    assert detect_language("بدي اروح ع Aleppo") == "ar"


def test_detect_language_mixed_more_english():
    assert detect_language("is قلعة worth visiting today definitely") == "en"


def test_detect_language_empty_defaults_arabic():
    assert detect_language("") == "ar"
