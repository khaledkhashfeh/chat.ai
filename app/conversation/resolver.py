"""app/conversation/resolver.py

[2] محلّل الإشارات — يعمل **قبل** تصنيف النية (قرار معماري مقصود، docs/spec.md
§3-[2]، ومخطط 1 في docs/plan.md). يبحث عن إشارة لمكان واحد ضمن (آخر توصيات
معروضة + أماكن الخطة الحالية) بالترتيب الملزم:

1. **إشارة ترتيبية** (الأول/التاني/الأخير/first/second/last/1/2/3) — تُطابَق
   فقط مع last_recommendations لأنها القائمة المرقمة المعروضة على المستخدم.
2. **اسم صريح بمطابقة غامضة** (rapidfuzz، عتبة settings.resolver_fuzzy_threshold
   = 82 افتراضيًا) على الاسمين العربي والإنكليزي معًا، تتحمل الأخطاء الإملائية
   والأسماء الجزئية.
3. **ضمير** (هاد/هذا/ياه/it/this/...) → last_mentioned_place.

يرجع dict فارغ `{}` إن لم توجد إشارة، أو `{"place_id": ..., "source": ...}`.
"""
from __future__ import annotations

from typing import TypedDict

from rapidfuzz import fuzz, process

from app.config import settings
from app.conversation.normalizer import normalize
from app.mocks.data import PLACES
from app.shared.models import WorkingMemory

ORDINALS: dict[str, int] = {
    "الاول": 1, "الاولى": 1, "الاولي": 1, "اول": 1,
    "التاني": 2, "الثاني": 2, "ثاني": 2,
    "التالت": 3, "الثالث": 3, "ثالث": 3,
    "الرابع": 4, "رابع": 4,
    "الخامس": 5, "خامس": 5,
    "قبل الاخير": -2,
    "الاخير": -1, "اخير": -1, "الاخيره": -1,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "last one": -1, "last": -1,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
}
# نرتّب المفاتيح من الأطول فالأقصر كي تُطابَق العبارات المركّبة ("قبل الاخير")
# قبل العبارات الأقصر التي قد تكون جزءًا منها ("الاخير").
_ORDINAL_KEYS_BY_LENGTH = sorted(ORDINALS.keys(), key=len, reverse=True)

PRONOUNS_AR = ["هاد", "هذا", "هيدا", "هالمكان", "ياه", "عنه", "فيه", "منه", "هاي", "هيدي"]
PRONOUNS_EN = ["this one", "that one", "it", "this", "that", "there"]
_PRONOUNS_BY_LENGTH = sorted(PRONOUNS_AR + PRONOUNS_EN, key=len, reverse=True)


class ResolvedReference(TypedDict, total=False):
    place_id: str
    source: str  # ordinal / name / pronoun


def _contains_token(text: str, phrase: str) -> bool:
    """يتحقق من وجود phrase كوحدة كلمات كاملة (لا كجزء من كلمة أخرى)."""
    return f" {phrase} " in f" {text} "


def _plan_place_pool(memory: WorkingMemory) -> list[dict]:
    """أماكن الخطة الحالية كمراجع مبسّطة {place_id, name_ar, name_en} بلا تكرار."""
    pool: list[dict] = []
    seen: set[str] = set()
    if memory.current_plan:
        for day in memory.current_plan.days:
            for stop in day.stops:
                if stop.place_id and stop.place_id not in seen:
                    pool.append({"place_id": stop.place_id, "name_ar": stop.name_ar, "name_en": stop.name_en})
                    seen.add(stop.place_id)
    return pool


def resolve_references(text_norm: str, memory: WorkingMemory) -> ResolvedReference:
    """يبحث عن إشارة لمكان واحد بالنص **المطبَّع مسبقًا** ضمن (المقترحات +
    أماكن الخطة) معًا. بالترتيب: ترتيبية ← اسمية ← ضمير."""
    recs = memory.last_recommendations
    plan_pool = _plan_place_pool(memory)
    full_pool = [{"place_id": r.place_id, "name_ar": r.name_ar, "name_en": r.name_en} for r in recs] + plan_pool

    if not full_pool:
        return {}

    # 1) الإشارة الترتيبية — تنطبق فقط على last_recommendations (القائمة المرقمة)
    if recs:
        for word in _ORDINAL_KEYS_BY_LENGTH:
            if _contains_token(text_norm, word):
                pos = ORDINALS[word]
                idx = pos - 1 if pos > 0 else pos
                if -len(recs) <= idx < len(recs):
                    return {"place_id": recs[idx].place_id, "source": "ordinal"}

    # 2) اسم صريح بمطابقة غامضة على الاسمين العربي والإنكليزي معًا (مطبَّعين).
    # حارس طول أدنى (4 أحرف) يمنع تطابقات زائفة: partial_ratio يمنح تشابهًا
    # عاليًا زوريًا لضمائر قصيرة جدًا (مثل "it") كونها تظهر كجزء حرفي داخل
    # أسماء أطول (citadel) — النصوص الأقصر من ذلك تُترك لمرحلة الضمائر أدناه.
    names: dict[str, str] = {}
    for p in full_pool:
        names[normalize(p["name_ar"])] = p["place_id"]
        names[normalize(p["name_en"])] = p["place_id"]
    if names and len(text_norm.strip()) >= 4:
        match = process.extractOne(text_norm, names.keys(), scorer=fuzz.partial_ratio)
        if match and match[1] >= settings.resolver_fuzzy_threshold:
            return {"place_id": names[match[0]], "source": "name"}

    # 3) ضمير → آخر مكان مفرد دار عنه الحديث
    for word in _PRONOUNS_BY_LENGTH:
        if _contains_token(text_norm, word) and memory.last_mentioned_place:
            return {"place_id": memory.last_mentioned_place, "source": "pronoun"}

    return {}


# كتالوج الأسماء المطبَّعة (اسم → place_id) — يُبنى مرة واحدة عند الاستيراد.
_CATALOG_NAMES: dict[str, str] = {}
for _pid, _place in PLACES.items():
    _CATALOG_NAMES[normalize(_place["name_ar"])] = _pid
    _CATALOG_NAMES[normalize(_place["name_en"])] = _pid


def resolve_place_by_name(text_norm: str, threshold: Optional[int] = None) -> Optional[str]:
    """يبحث عن مكان بالاسم في **الكتالوج الكامل** (لا الأماكن المعروضة فقط) —
    للاستعلامات المباشرة بلا سياق سابق مثل «معلومات عن قلعة حلب» بأول رسالة.

    يعتمد **تغطية الكلمات**: كل كلمة من اسم المكان يجب أن تجد مقابلًا قويًا بين
    كلمات النص (متوسط التطابق ≥ العتبة). هذا يمنع المطابقات الكاذبة التي يسببها
    partial_ratio مع الأسماء القصيرة (مثلًا «معلومات» تشبه «معلولا» جزئيًا)."""
    thr = threshold if threshold is not None else settings.resolver_fuzzy_threshold
    text_tokens = [t for t in text_norm.split() if len(t) >= 2]
    if not text_tokens:
        return None

    best_pid: Optional[str] = None
    best_score = 0.0
    for name, pid in _CATALOG_NAMES.items():
        name_tokens = [t for t in name.split() if len(t) >= 2]
        if not name_tokens:
            continue
        per_token = [max(fuzz.ratio(nt, tt) for tt in text_tokens) for nt in name_tokens]
        avg = sum(per_token) / len(per_token)
        if avg > best_score:
            best_score, best_pid = avg, pid

    return best_pid if best_score >= thr else None


def extract_ordinal_place_ids(text_norm: str, recs: list) -> list[str]:
    """يستخرج **كل** الإشارات الترتيبية بالنص (وليس أوّلها فقط) بترتيب ورودها —
    تُستخدم خصيصًا لنية المقارنة حيث قد يُشار لمكانين معًا ("الأول ولا التالت")."""
    if not recs:
        return []
    padded = f" {text_norm} "
    matches: list[tuple[int, str]] = []
    for word in _ORDINAL_KEYS_BY_LENGTH:
        idx = padded.find(f" {word} ")
        if idx == -1:
            continue
        pos = ORDINALS[word]
        list_idx = pos - 1 if pos > 0 else pos
        if -len(recs) <= list_idx < len(recs):
            matches.append((idx, recs[list_idx].place_id))
    matches.sort(key=lambda x: x[0])
    result: list[str] = []
    for _, pid in matches:
        if pid not in result:
            result.append(pid)
    return result
