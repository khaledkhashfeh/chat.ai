"""app/conversation/intent.py

[3] مصنّف النية — نموذج تصنيف مدرَّب ذاتيًا (بدون أي LLM أو API خارجي)، وفق
docs/spec.md §3-[3]: TF-IDF بمقاطع حروف (char_wb, نطاق 2-5) + LogisticRegression،
محايد للغة (نموذج واحد يفهم العربي والإنكليزي والمختلط).

- 9 نوايا مغلقة (انظر CLAUDE.md) + نية داخلية "unclear" عندما تكون الثقة أقل
  من app.config.settings.intent_confidence_threshold (0.45 افتراضيًا).
- الربط مع محلّل الإشارات: إن وُجدت إشارة محلولة، يُلحق النص بعلامة
  "__مكان_مشار_اليه__" قبل التصنيف (docs/spec.md §3-[3])، وبيانات التدريب
  تحوي أمثلة بهذه العلامة.

أمر إعادة التدريب: `python -m app.conversation.intent --train`
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from app.config import settings
from app.conversation.normalizer import normalize

# النوايا التسع المغلقة — لا تُضاف نية بدون تعديل CLAUDE.md أولًا.
INTENTS: tuple[str, ...] = (
    "search",
    "details",
    "compare",
    "build_plan",
    "modify_plan",
    "add_to_plan",
    "reject",
    "greeting_thanks",
    "out_of_scope",
)

REFERENCE_TAG = "__مكان_مشار_اليه__"

_DEFAULT_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "seed_intents.txt"
_DELIMITER = "|||"


def load_training_data(path: Optional[Path] = None) -> list[tuple[str, str]]:
    """يقرأ أمثلة التدريب من ملف نصي بصيغة `النص ||| النية` بكل سطر."""
    data_path = path or _DEFAULT_DATA_PATH
    examples: list[tuple[str, str]] = []
    with open(data_path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n").strip()
            if not line or line.startswith("#"):
                continue
            if _DELIMITER not in line:
                continue
            text, _, label = line.partition(_DELIMITER)
            text = text.strip()
            label = label.strip()
            if label not in INTENTS:
                continue
            examples.append((text, label))
    return examples


def build_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True)),
            ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", C=40.0)),
        ]
    )


def train_model(data_path: Optional[Path] = None) -> Pipeline:
    examples = load_training_data(data_path)
    if not examples:
        raise ValueError(f"لا توجد بيانات تدريب صالحة في {data_path or _DEFAULT_DATA_PATH}")
    texts = [normalize(t) for t, _ in examples]
    labels = [label for _, label in examples]
    pipeline = build_pipeline()
    pipeline.fit(texts, labels)
    return pipeline


_MODEL_CACHE: Optional[Pipeline] = None


def _get_model() -> Pipeline:
    """يحمّل النموذج من القرص إن وُجد، وإلا يدرّبه بالذاكرة فورًا (بدون الحاجة
    لتشغيل --train يدويًا أولًا) ثم يحاول حفظه للاستخدام لاحقًا."""
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE

    model_path = Path(settings.intent_model_path)
    if model_path.exists():
        try:
            _MODEL_CACHE = joblib.load(model_path)
            return _MODEL_CACHE
        except Exception:
            pass  # نموذج تالف أو غير متوافق — نعيد التدريب أدناه

    _MODEL_CACHE = train_model()
    try:
        model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(_MODEL_CACHE, model_path)
    except Exception:
        pass  # فشل الحفظ لا يمنع الاستخدام — النموذج بالذاكرة كافٍ لهذه العملية
    return _MODEL_CACHE


def reset_model_cache() -> None:
    """يجبر إعادة تحميل/تدريب النموذج بالاستدعاء التالي (تُستخدم بالاختبارات)."""
    global _MODEL_CACHE
    _MODEL_CACHE = None


def classify_raw(text_norm: str) -> tuple[str, float]:
    """يرجع أعلى نية مرشّحة ودرجة ثقتها **دون** طيّها إلى "unclear" عند تدني
    الثقة. يُستخدم في مستخرج السياق (context_extractor) حيث نحتاج أفضل تخمين
    للنية دائمًا مع الثقة الخام لاتخاذ قرار requires_clarification بأنفسنا."""
    model = _get_model()
    proba = model.predict_proba([text_norm])[0]
    idx = max(range(len(proba)), key=lambda i: proba[i])
    return str(model.classes_[idx]), float(proba[idx])


def classify(text_norm: str) -> tuple[str, float]:
    """يصنّف نصًا **مطبَّعًا مسبقًا** (بعد normalize، وبعد إلحاق REFERENCE_TAG إن
    وُجدت إشارة محلولة). يرجع (النية, درجة الثقة) — "unclear" إن كانت الثقة
    أقل من العتبة المُعدّة."""
    label, confidence = classify_raw(text_norm)
    if confidence < settings.intent_confidence_threshold:
        return "unclear", confidence
    return label, confidence


def _main() -> None:
    parser = argparse.ArgumentParser(description="تدريب/إعادة تدريب مصنّف النية")
    parser.add_argument("--train", action="store_true", help="يعيد تدريب النموذج ويحفظه بالمسار المُعدّ")
    parser.add_argument("--data", type=str, default=None, help="مسار بديل لملف بيانات التدريب")
    args = parser.parse_args()

    if args.train:
        data_path = Path(args.data) if args.data else None
        pipeline = train_model(data_path)
        out_path = Path(settings.intent_model_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(pipeline, out_path)
        reset_model_cache()
        print(f"تم تدريب النموذج وحفظه في {out_path} ({len(load_training_data(data_path))} مثالًا)")
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
