"""app/conversation/normalizer.py

[1] المطبِّع اللغوي — أول مكوّن بخط المعالجة (docs/spec.md §3-[1]).
يوحّد شكل النص العربي/الإنكليزي/المختلط قبل أي معالجة لاحقة، ويكشف لغة الرسالة.

كل ما بعده (حل الإشارات، تصنيف النية، استخراج الكيانات) يعمل على النص
المطبَّع الناتج من `normalize()` — بدون هذه الخطوة تُعامَل "قلعه دمشق" و
"قَلْعَة دمشق" ككلمتين مختلفتين وتنهار الدقة.
"""
from __future__ import annotations

import re

# توحيد الهمزات على الألف
_ALEF_VARIANTS = re.compile(r"[أإآٱ]")
# التشكيل (فتحة/ضمة/كسرة/شدة/سكون/تنوين) والتطويل (ـ)
_DIACRITICS_AND_TATWEEL = re.compile(r"[ً-ْٰـ]")
# تقليص أي حرف متكرر 3 مرات فأكثر إلى مرتين فقط
_REPEATED_CHARS = re.compile(r"(.)\1{2,}")
# علامات ترقيم عربية/إنكليزية شائعة — تُستبدل بمسافة كي لا تلتصق بآخر كلمة
# (وإلا تفشل مطابقة الكلمات الكاملة لاحقًا بمحلّل الإشارات، مثل "التاني؟").
_PUNCTUATION = re.compile(r"[؟?!.,:؛،;()\[\]{}\"'«»…]")
# مسافات متعددة
_MULTI_SPACE = re.compile(r"\s+")

_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_LATIN_DIGITS = "0123456789"
_DIGIT_TRANSLATION = str.maketrans(_ARABIC_DIGITS, _LATIN_DIGITS)

_ARABIC_RANGE = re.compile(r"[؀-ۿ]")
_LATIN_RANGE = re.compile(r"[a-zA-Z]")


def normalize(text: str) -> str:
    """يوحّد نص المستخدم إلى صيغة قانونية واحدة قابلة للمطابقة.

    الخطوات (بالترتيب الملزم):
    1. إزالة الفراغات الطرفية وتحويل لحروف صغيرة (يؤثر فقط على اللاتيني).
    2. توحيد الهمزات (أ/إ/آ/ٱ → ا).
    3. توحيد التاء المربوطة (ة → ه) والألف المقصورة (ى → ي).
    4. إزالة التشكيل والتطويل.
    5. استبدال علامات الترقيم الشائعة بمسافة (كي لا تلتصق بآخر كلمة).
    6. تقليص تكرار الأحرف (حلوووو → حلوو).
    7. توحيد الأرقام العربية إلى لاتينية.
    8. توحيد المسافات المتعددة إلى مسافة واحدة.
    """
    if not text:
        return ""

    t = text.strip().lower()
    t = _ALEF_VARIANTS.sub("ا", t)
    t = t.replace("ة", "ه")
    t = t.replace("ى", "ي")
    t = _DIACRITICS_AND_TATWEEL.sub("", t)
    t = _PUNCTUATION.sub(" ", t)
    t = _REPEATED_CHARS.sub(r"\1\1", t)
    t = t.translate(_DIGIT_TRANSLATION)
    t = _MULTI_SPACE.sub(" ", t)
    return t.strip()


def detect_language(text: str) -> str:
    """يكشف اللغة الغالبة بعدّ أحرف كل أبجدية في النص الأصلي (غير المطبَّع).

    القاعدة: عربي إن كان عدد الأحرف العربية >= عدد الأحرف اللاتينية (بما في ذلك
    حالة تساوي الصفرين، لأن المنصة عربية أساسًا). يُستخدم النص الأصلي وليس
    المطبَّع لأن التطبيع لا يغيّر مجموعة الأحرف المستخدمة في العدّ هنا.
    """
    if not text:
        return "ar"
    ar_count = len(_ARABIC_RANGE.findall(text))
    en_count = len(_LATIN_RANGE.findall(text))
    return "ar" if ar_count >= en_count else "en"
