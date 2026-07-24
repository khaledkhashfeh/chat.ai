# المشروع: طبقة المحادثة الذكية — منصة السياحة الذكية في سوريا

خدمة Python مستقلة تمثل "طبقة المحادثة" لمنصة سياحية. تفهم رسائل المستخدم
(عربي/إنكليزي/عامية سورية/مختلط)، تدير الحوار، وتستدعي محركي التوصية والتخطيط
(حاليًا نسخ وهمية mock ملتزمة بالعقد في docs/contract.md).

## قواعد صارمة — لا تُخالف أبدًا

1. **ممنوع استخدام أي نموذج لغوي (LLM) أو أي API خارجي للذكاء الاصطناعي.**
   لا OpenAI، لا Anthropic API، لا HuggingFace transformers، لا نماذج embeddings جاهزة.
   الفهم اللغوي يُبنى حصرًا من:
   - تطبيع نصي مكتوب يدويًا (normalizer)
   - مصنّف نية: scikit-learn (TfidfVectorizer بـ char_wb + LogisticRegression) مدرَّب على بياناتنا
   - قواميس وأنماط regex للكيانات
   - مطابقة غامضة: rapidfuzz
2. **المكتبات المسموحة فقط:** fastapi, uvicorn, pydantic, redis, scikit-learn,
   joblib, rapidfuzz, pytest, httpx (للاختبارات). أي مكتبة أخرى تحتاج موافقة صريحة مني أولًا.
3. **كل مكوّن جديد يُرفق باختباراته في نفس الجلسة. لا كود بدون اختبار.**
4. **محركا التوصية والتخطيط نسخ وهمية (app/mocks/) ملتزمة بـ docs/contract.md حرفيًا** —
   نفس أسماء الحقول والبنية بالضبط. لا تبنِ أي منطق توصية أو تخطيط حقيقي.
5. **الردود ثنائية اللغة:** الرد بلغة رسالة المستخدم الأخيرة (ar/en) دائمًا.
6. **اقرأ docs/spec.md قبل أي عمل على منطق الحوار، و docs/contract.md قبل أي عمل على
   الـ mocks أو تنسيقات البيانات، و docs/plan.md لترتيب البناء.**
7. **ممنوع اختراع معلومات في الردود:** كل اسم مكان/تقييم/وقت يأتي من استدعاء أداة (mock حاليًا).
8. **لا تعقيد استباقي:** لا microservices، لا قواعد بيانات غير Redis، لا Docker،
   لا async queues. أبسط حل يحقق المواصفات.

## البنية الملزمة للمشروع

```
app/
├── main.py                    # FastAPI: نقطة /chat + /health + رأس السر الداخلي
├── config.py                  # الإعدادات من متغيرات البيئة
├── conversation/
│   ├── normalizer.py          # تطبيع العربية + كشف اللغة
│   ├── session.py             # حالة المحادثة + ذاكرة العمل (Redis)
│   ├── resolver.py            # محلّل الإشارات (ترتيبي/اسمي/ضمائر)
│   ├── intent.py              # مصنّف النية + سكربت التدريب
│   ├── entities.py            # مستخرج الكيانات (مدن/مدة/مجموعة/ميزانية/اهتمامات)
│   ├── dialogue.py            # مدير الحوار (القلب — كل القرارات)
│   └── responses.py           # قوالب الردود ثنائية اللغة + الرد المنظم
├── mocks/
│   ├── recommender.py         # search / details / compare / log / profile
│   └── planner.py             # build / modify / feasibility
├── shared/
│   └── models.py              # Pydantic: كائنات العقد (contract.md)
data/
├── seed_intents.txt           # بذرة أمثلة النوايا (يكتبها الفريق)
├── train_intents.txt          # بيانات التدريب الموسعة
└── intent_model.pkl           # النموذج المدرَّب (لا يُرفع لـ git)
tests/
├── test_normalizer.py
├── test_resolver.py
├── test_intent.py
├── test_entities.py
├── test_dialogue.py
└── test_flows.py              # حوارات كاملة متسلسلة عبر /chat (الأهم)
```

## أوامر التشغيل

```bash
# تثبيت
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# تدريب/إعادة تدريب المصنّف (ثوانٍ)
python -m app.conversation.intent --train

# الاختبارات (تشغَّل بعد كل تعديل — إلزامي)
pytest -x -q

# تشغيل الخدمة
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

## النوايا التسع المغلقة (لا تضف نية بدون موافقة)

search / details / compare / build_plan / modify_plan / add_to_plan /
reject / greeting_thanks / out_of_scope
(+ نية داخلية unclear عندما تكون الثقة < 0.45)

## قاموس الوسوم الموحّد (يُستخدم في الكيانات والـ mocks)

tag:historical, tag:religious, tag:nature, tag:sea, tag:market,
tag:food, tag:family_fun, tag:adventure, tag:quiet, tag:museum

## اتفاقيات الكود

- Python 3.12، type hints في كل التواقيع، Pydantic v2 للكائنات.
- أسماء الحقول بالإنكليزية snake_case مطابقة لـ contract.md حرفيًا.
- النصوص المعروضة للمستخدم في responses.py فقط — لا نصوص مبعثرة بالمنطق.
- التعليقات بالعربية أو الإنكليزية، المهم الوضوح.
- كل دالة في dialogue.py تحدّث ذاكرة العمل قبل إرجاع الرد (قاعدة إلزامية).

## تعريف "الإنجاز المكتمل" لأي جلسة عمل

1. الكود مكتوب حسب المواصفات في docs/
2. الاختبارات مكتوبة وتمر كلها (pytest -x أخضر)
3. اختبارات المكونات السابقة ما زالت تمر (لا كسر رجعي)
4. لا تحذيرات lint جوهرية
