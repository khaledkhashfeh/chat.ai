"""اختبارات مصنّف النية: بيانات التدريب، التصنيف الصحيح لجمل غير موجودة
بالتدريب (5 لكل نية كما يطلب docs/plan.md، الجلسة 3)، وقاعدة الثقة (unclear)."""
from collections import Counter

from app.conversation import intent
from app.conversation.normalizer import normalize


def test_training_data_covers_all_nine_intents_with_minimum_examples():
    examples = intent.load_training_data()
    counts = Counter(label for _, label in examples)
    assert set(counts) == set(intent.INTENTS)
    assert all(c >= 20 for c in counts.values()), counts


def test_confidence_threshold_returns_unclear_for_low_confidence():
    class FakeModel:
        classes_ = ["search", "details"]

        def predict_proba(self, X):
            return [[0.3, 0.2]]

    original = intent._MODEL_CACHE
    intent._MODEL_CACHE = FakeModel()
    try:
        label, confidence = intent.classify("اي نص")
        assert label == "unclear"
        assert confidence < 0.45
    finally:
        intent._MODEL_CACHE = original


def _predict(text: str) -> str:
    label, _ = intent.classify(normalize(text))
    return label


class TestSearchIntent:
    def test_1(self):
        assert _predict("اقترحلي اماكن حلوة بطرطوس") == "search"

    def test_2(self):
        assert _predict("شو في اماكن سياحية بحماة") == "search"

    def test_3(self):
        assert _predict("recommend nice places in latakia") == "search"

    def test_4(self):
        assert _predict("بدي زور اماكن جميلة بريف حلب") == "search"

    def test_5(self):
        assert _predict("any good spots to visit in tartus") == "search"


class TestDetailsIntent:
    def test_1(self):
        assert _predict("شو مواعيد دوام __مكان_مشار_اليه__") == "details"

    def test_2(self):
        assert _predict("احكيلي عنه بالتفصيل") == "details"

    def test_3(self):
        assert _predict("what are the visiting hours") == "details"

    def test_4(self):
        assert _predict("شو قصة الاول") == "details"

    def test_5(self):
        assert _predict("is it open today") == "details"


class TestCompareIntent:
    def test_1(self):
        assert _predict("ايهم افضل التاني ولا التالت") == "compare"

    def test_2(self):
        assert _predict("which place has a better rating") == "compare"

    def test_3(self):
        assert _predict("شو الفرق من ناحية التقييم بينهن") == "compare"

    def test_4(self):
        assert _predict("compare the citadel and the museum") == "compare"

    def test_5(self):
        assert _predict("ايهن انسب لرحلة العائلة") == "compare"


class TestBuildPlanIntent:
    def test_1(self):
        assert _predict("بدي تخطط لي رحلة اربع ايام") == "build_plan"

    def test_2(self):
        assert _predict("plan a five day trip to latakia") == "build_plan"

    def test_3(self):
        assert _predict("رتبلي برنامج زيارة لثلاث ايام بحمص") == "build_plan"

    def test_4(self):
        assert _predict("can you schedule a plan for our trip") == "build_plan"

    def test_5(self):
        assert _predict("اعملي جدول رحلة لعيلتي") == "build_plan"


class TestModifyPlanIntent:
    def test_1(self):
        assert _predict("شيل السوق من اليوم الاول") == "modify_plan"

    def test_2(self):
        assert _predict("can you move the museum to day three") == "modify_plan"

    def test_3(self):
        assert _predict("بدل مكان الغداء بمطعم تاني") == "modify_plan"

    def test_4(self):
        assert _predict("قدم موعد زيارة القلعة شوي") == "modify_plan"

    def test_5(self):
        assert _predict("extend my plan for one more day") == "modify_plan"


class TestAddToPlanIntent:
    def test_1(self):
        assert _predict("ضيف هالمكان عالخطة لو سمحت") == "add_to_plan"

    def test_2(self):
        assert _predict("please save this for my trip") == "add_to_plan"

    def test_3(self):
        assert _predict("احفظلي التالت") == "add_to_plan"

    def test_4(self):
        assert _predict("add the last one to my saved places") == "add_to_plan"

    def test_5(self):
        assert _predict("خليه ضمن المحفوظات") == "add_to_plan"


class TestRejectIntent:
    def test_1(self):
        assert _predict("ما ناسبوني هالخيارات") == "reject"

    def test_2(self):
        assert _predict("these suggestions don't work for me") == "reject"

    def test_3(self):
        assert _predict("لا هدول مش الي بدي ياهن") == "reject"

    def test_4(self):
        assert _predict("i don't want any of these") == "reject"

    def test_5(self):
        assert _predict("غيرلي كل الاقتراحات لو سمحت") == "reject"


class TestGreetingThanksIntent:
    def test_1(self):
        assert _predict("أهلا فيك") == "greeting_thanks"

    def test_2(self):
        assert _predict("thank you so much for your help") == "greeting_thanks"

    def test_3(self):
        assert _predict("مساكم الله بالخير") == "greeting_thanks"

    def test_4(self):
        assert _predict("thanks a ton") == "greeting_thanks"

    def test_5(self):
        assert _predict("شكرا الك كتير كتير") == "greeting_thanks"


class TestOutOfScopeIntent:
    def test_1(self):
        assert _predict("بدي احجز رحلة طيران لمصر") == "out_of_scope"

    def test_2(self):
        assert _predict("can you book a hotel room for me") == "out_of_scope"

    def test_3(self):
        assert _predict("بدي اعرف طقس بكرا") == "out_of_scope"

    def test_4(self):
        assert _predict("what's the currency exchange rate") == "out_of_scope"

    def test_5(self):
        assert _predict("help me translate this text to english") == "out_of_scope"
