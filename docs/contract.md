# وثيقة العقد بين الطبقات (Interface Contract)
## مواصفات مدخلات ومخرجات محركي التوصية والتخطيط — الإصدار 1.0

**الغرض:** هذا العقد يحدد الشكل الدقيق للبيانات بين طبقة المحادثة والمحركين.
النسخ الوهمية (app/mocks/) **تلتزم به حرفيًا**: نفس أسماء الحقول، نفس الأنواع،
نفس البنية. عند بناء المحركات الحقيقية لاحقًا تُستبدل الـ mocks دون تغيير أي شيء
في طبقة المحادثة.

**قاعدة تغيير العقد:** أي حقل جديد أو تعديل يُحدَّث في هذه الوثيقة أولًا ثم يُنفَّذ.

---

## القسم 1: الكائنات المشتركة

### 1.1 ConversationState (يُمرَّر مع أغلب الاستدعاءات)

```json
{
  "destination": ["دمشق"],
  "dates": {"start": "2026-08-10", "end": "2026-08-13"},
  "duration_days": 4,
  "budget_level": "medium",
  "group_type": "family",
  "group_size": 4,
  "interests": ["tag:historical", "tag:family_fun"],
  "pace": "relaxed",
  "trip_purpose": "family_fun",
  "transport_mode": "car",
  "preferred_time": "morning",
  "excluded_place_ids": ["p03"],
  "saved_place_ids": ["p17", "p42"],
  "current_plan_id": null,
  "language": "ar"
}
```

| الحقل | النوع | إلزامي | القيم |
|---|---|---|---|
| destination | list[str] | لا | أسماء مدن بالعربية الموحدة — **واحدة أو أكثر** |
| dates | object/null | لا | start, end بصيغة ISO. اختياري تمامًا — لا يُسأل عنه |
| duration_days | int/null | لا | |
| budget_level | str/null | لا | low / medium / high |
| group_type | str/null | لا | solo / couple / family / friends / large_group |
| group_size | int/null | لا | |
| interests | list[str] | لا | من قاموس الوسوم الموحّد فقط — **واحد أو أكثر** |
| pace | str/null | لا | relaxed / moderate / intense |
| trip_purpose | str/null | **نعم** (حقل جمع إلزامي) | leisure / adventure / cultural / family_fun / romantic |
| transport_mode | str/null | لا | car / public_transport / walking / mixed |
| preferred_time | str/null | لا | morning / afternoon / evening |
| excluded_place_ids | list[str] | لا | ممنوع اقتراحها |
| saved_place_ids | list[str] | لا | |
| current_plan_id | str/null | لا | |
| language | str | **نعم** | ar / en |

**ملاحظة `trip_purpose`:** حقل مستقل عن `group_type`/`interests` — يصف **دافع** الرحلة
لا تركيبة المجموعة ولا نوع الأماكن (شخص وحيد ممكن يطلب `family_fun`، وعائلة
ممكن تطلب `romantic`). يُسأل عنه دائمًا ضمن ترتيب الجمع (أولوية قصوى) بخيارات
مغلقة — لا يُستنتَج تلقائيًا من group_type.

**قاموس الوسوم الموحّد (مغلق — إضافة وسم = تعديل هذه الوثيقة):**
`tag:historical, tag:religious, tag:nature, tag:sea, tag:market, tag:food,
tag:family_fun, tag:adventure, tag:quiet, tag:museum`

### 1.2 PlaceCard — المكان المختصر (لعرض البطاقات)

```json
{
  "place_id": "p42",
  "name_ar": "الجامع الأموي",
  "name_en": "Umayyad Mosque",
  "city": "دمشق",
  "category": "religious",
  "tags": ["tag:religious", "tag:historical"],
  "rating": 4.8,
  "reviews_count": 1240,
  "photo_url": "https://.../p42_thumb.jpg",
  "recommendation_reason": "مثالي لعائلتك: تاريخ حي وساحات واسعة للأطفال",
  "visit_duration_min": 90,
  "lat": 33.5119, "lng": 36.3067,
  "price_level": "free"
}
```

**قواعد صارمة:**
- `name_ar` و `name_en` **إلزاميان دائمًا** — محلّل الإشارات يحتاجهما معًا.
- `recommendation_reason` **جملة واحدة مخصصة لسياق هذا المستخدم بلغته** (ليس نصًا ثابتًا).
- `price_level`: free / cheap / medium / expensive.
- **كل قائمة تُرجَع مرتبةً والأفضل أولًا** — المستخدم سيشير إليها بـ«الأول» و«التاني».

### 1.3 شكل الخطأ الموحّد (لكل الأدوات)

```json
{
  "error": {
    "type": "no_results",
    "user_message": "ما لقيت أماكن مطابقة باللاذقية — بتحب وسّع البحث لطرطوس؟",
    "suggestion": {"action": "expand_search", "params": {"city": "طرطوس"}}
  }
}
```
`type`: no_results / missing_input / unsolvable / internal.
`user_message` بلغة الطلب وصالح للعرض المباشر. المحادثة لا تعرض أخطاء تقنية خامًا أبدًا.

---

## القسم 2: عقد محرك التوصية (5 أدوات)

### 2.1 search — بحث/اقتراح أماكن

**المدخلات:**
```json
{
  "query": "اماكن تاريخيه هاديه",
  "state": {/* ConversationState */},
  "top_k": 8,
  "scope": {"city": "دمشق", "category": null}
}
```
`scope` اختياري (لاستعلام «شو في بمعلولا؟»).

**المخرجات:**
```json
{
  "results": [/* PlaceCard مرتبة */],
  "ranking_note": "رتبنا حسب الملاءمة للعائلات والقرب من دمشق",
  "fallback": null
}
```
`fallback` يُملأ عند قلة النتائج: `{"reason": "...", "suggestion": {...}}`.

**التزامات:** احترام excluded_place_ids مطلقًا، تنويع (لا 5 أماكن متطابقة النوع
متتالية)، زمن ≤ 2ث.

### 2.2 details — تفاصيل مكان واحد

**المدخلات:** `{"place_id": "p42", "language": "ar"}`

**المخرجات:** PlaceCard كامل + إضافات:
```json
{
  "place": {/* PlaceCard */},
  "description": "وصف كامل بلغة الطلب...",
  "opening_hours": {
    "sat": {"open": "09:00", "close": "18:00"},
    "sun": {"open": "09:00", "close": "18:00"},
    "mon": null,
    "tue": {"open": "09:00", "close": "18:00"},
    "wed": {"open": "09:00", "close": "18:00"},
    "thu": {"open": "09:00", "close": "18:00"},
    "fri": {"open": "13:00", "close": "18:00"}
  },
  "open_now": true,
  "phone": "0930 517 426",
  "website": "http://...",
  "photos": ["url1", "url2"],
  "group_suitability": {"solo": 80, "couple": 70, "family": 90, "friends": 85, "large_group": 60},
  "best_season": "spring",
  "best_time_of_day": "morning",
  "practical_notes": ["يحتاج حجزًا مسبقًا للمجموعات"]
}
```
ساعات العمل **منظمة يوم-بيوم** (null = مغلق) — لأسئلة «مفتوح الجمعة؟».

### 2.3 compare — مقارنة أماكن

**المدخلات:** `{"place_ids": ["p17","p42"], "state": {...}, "language": "ar"}` (2–4 أماكن)

**المخرجات:**
```json
{
  "places": [/* PlaceCard لكل مكان */],
  "axes": [
    {"axis": "rating", "label_ar": "التقييم", "values": {"p17": 4.6, "p42": 4.8}},
    {"axis": "group_fit", "label_ar": "الملاءمة لعائلتك", "values": {"p17": 90, "p42": 85}},
    {"axis": "visit_duration_min", "label_ar": "مدة الزيارة", "values": {"p17": 120, "p42": 90}},
    {"axis": "distance_km", "label_ar": "البعد عنك", "values": {"p17": 2.1, "p42": 0.8}},
    {"axis": "price_level", "label_ar": "السعر", "values": {"p17": "cheap", "p42": "free"}}
  ],
  "verdict": {
    "winner_place_id": "p17",
    "reason": "لعائلة مع أطفال، القلعة أنسب: ساحات أوسع وملاءمة عائلية أعلى"
  }
}
```
`group_fit` يُحسب حسب group_type من الحالة. المحادثة تعرض الجدول وتصوغ التعليل —
لا تحسب شيئًا بنفسها.

### 2.4 log — تسجيل تفاعل (غير متزامن — المحادثة لا تنتظره)

**المدخلات:**
```json
{
  "user_id": "u123",
  "place_id": "p42",
  "event": "added_to_plan",
  "source": "chat",
  "ts": "2026-07-20T14:30:00Z"
}
```
`event` من قائمة مغلقة: `view_card / open_details / like / save / pass /
added_to_plan / removed_from_plan / visited`.
`source`: chat / search / plan.

**المخرجات:** `{"ok": true}`

### 2.5 profile — ملف تفضيلات المستخدم

**المدخلات:** `{"user_id": "u123"}`

**المخرجات:**
```json
{
  "top_tags": ["tag:historical", "tag:quiet"],
  "visited_cities": ["دمشق"],
  "usual_group_type": "family",
  "usual_pace": "relaxed",
  "last_activity": {"type": "plan_draft", "city": "اللاذقية", "days_ago": 7}
}
```
مستخدم جديد → كل الحقول null/فارغة.

---

## القسم 3: عقد محرك التخطيط (3 أدوات)

### 3.1 build — بناء خطة

**المدخلات:**
```json
{
  "state": {/* destination + duration_days + group_type إلزامية هنا */},
  "mandatory_place_ids": ["p17", "p42"],
  "candidate_place_ids": ["p08", "p11", "p23"],
  "start_location": {"lat": 33.51, "lng": 36.29}
}
```
- `mandatory` يجب أن تظهر جميعها في الخطة.
- `candidate` اختيارية — للمحرك حرية الاختيار منها للملء. إن غابت يطلبها من
  التوصية داخليًا.
- المحادثة **مسؤولة عن عدم الاستدعاء قبل اكتمال الإلزامي** في الحالة.

**المخرجات — PlanObject (أهم كائن في المشروع):**
```json
{
  "plan_id": "pl_889",
  "summary_ar": "4 أيام بدمشق وريفها بإيقاع مريح للعائلة",
  "summary_en": "4 relaxed family days in Damascus & countryside",
  "total_cost_estimate": {"activities": 40, "food": 60, "transport": 25, "currency": "USD"},
  "days": [
    {
      "day_number": 1,
      "date": "2026-08-10",
      "title_ar": "يوم المدينة القديمة",
      "weather_note": "حار ظهرًا — الجولات المفتوحة صباحًا",
      "day_cost_estimate": 30,
      "stops": [
        {
          "place_id": "p42",
          "name_ar": "الجامع الأموي", "name_en": "Umayyad Mosque",
          "stop_type": "visit",
          "arrival": "09:00", "departure": "10:30",
          "visit_duration_min": 90,
          "travel_from_prev_min": 0, "travel_mode": null,
          "cost_estimate": 0,
          "why_here": "صباحًا قبل اشتداد الحر وقبل الازدحام"
        },
        {
          "place_id": null,
          "name_ar": "غداء", "name_en": "Lunch",
          "stop_type": "meal",
          "arrival": "13:00", "departure": "14:00",
          "visit_duration_min": 60,
          "travel_from_prev_min": 10, "travel_mode": "walk",
          "cost_estimate": 15,
          "why_here": "استراحة منتصف اليوم قرب محطتك التالية"
        }
      ]
    }
  ],
  "tradeoffs": [
    {
      "issue_ar": "معلولا وقلعة الحصن معًا بيومين مرهق جدًا للتنقل",
      "options": [
        {"action": "extend_days", "label_ar": "تمديد ليوم ثالث"},
        {"action": "drop_place", "params": {"place_id": "p17"}, "label_ar": "تأجيل قلعة الحصن"}
      ]
    }
  ]
}
```
- `stop_type`: visit / meal / rest / transfer.
- `name_ar` و `name_en` بكل محطة — للإشارات لاحقًا («شيل السوق»).
- `why_here` جملة لكل محطة — تعرضها المحادثة لتعزيز الثقة.
- **tradeoffs إلزامي عند أي تنازل** — ممنوع منعًا باتًا إخفاء تعارض أو إرجاع
  خطة غير قابلة للتنفيذ بصمت.

**التزامات:** صفر زيارات لأماكن مغلقة (احترام ساعات العمل مطلق)، أيام متماسكة
جغرافيًا، زمن ≤ 5ث.

### 3.2 modify — تعديل خطة (العقد الأدق)

**المدخلات:**
```json
{
  "plan_id": "pl_889",
  "modification": {
    "type": "replace_with_kind",
    "target_place_id": "p11",
    "params": {"kind_tags": ["tag:family_fun"], "day_number": 2}
  }
}
```
`type` من قائمة مغلقة:
`remove / add / replace_with_place / replace_with_kind / move_to_day /
shift_time / change_day_pace / extend_days / shrink_days`.
`target_place_id` حلّته المحادثة بمحلّل الإشارات قبل الاستدعاء.

**المخرجات:**
```json
{
  "plan": {/* PlanObject كامل محدث */},
  "changes": [
    {"change_ar": "حذفنا المتحف الوطني من اليوم الثاني"},
    {"change_ar": "أضفنا حديقة تشرين بدلًا منه"},
    {"change_ar": "قدّمنا الغداء نصف ساعة ليضبط التوقيت"}
  ],
  "alternatives": [/* PlaceCard ×2 بدائل احتياطية عند replace_with_kind */]
}
```
- **التعديل جراحي:** يُعاد ترتيب اليوم المتأثر فقط؛ أي مساس بأيام أخرى يُذكر في changes.
- `changes` تشمل التغيير المطلوب + **كل** التغييرات الجانبية — المحادثة تذكرها بشفافية.

### 3.3 feasibility — فحص جدوى سريع

**المدخلات:**
```json
{"place_ids": ["p1","p2","p3","p4","p5","p6"], "duration_days": 2, "group_type": "family"}
```

**المخرجات:**
```json
{
  "verdict": "tight",
  "reason_ar": "6 أماكن متباعدة بيومين مع أطفال — ممكن لكن مرهق",
  "suggestion": {"action": "extend_days", "label_ar": "تمديد ليوم ثالث أريح بكثير"}
}
```
`verdict`: comfortable / tight / unrealistic. زمن ≤ 1ث.
تستدعيها المحادثة **قبل** build عندما يختار المستخدم أماكنه بنفسه.

---

## القسم 4: قواعد عامة ملزمة

1. كل النصوص المعروضة تأتي **بلغة الطلب** (حقل language إلزامي).
2. **معرّفات الأماكن موحّدة** = معرّفات قاعدة البيانات الأصلية. لا معرّفات مخترعة.
3. أزمنة قصوى: search 2ث، details/compare 1ث، build 5ث، modify 3ث، feasibility 1ث.
4. **الـ mocks أولًا:** تُبنى بهذا العقد حرفيًا، ببيانات 15–20 مكانًا سوريًا واقعيًا
   ثابتًا (قلعة دمشق، الجامع الأموي، سوق الحميدية، معلولا، قلعة الحصن، تدمر،
   المتحف الوطني، جزيرة أرواد، قلعة صلاح الدين، شاطئ الشاطئ الأزرق، ...) بحقول
   كاملة تشمل ساعات عمل متنوعة (منها مكان مغلق يوم الاثنين) وgroup_suitability مختلفة.
