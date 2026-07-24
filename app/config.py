"""app/config.py

الإعدادات من متغيرات البيئة. لا قيم سرية مكتوبة بالكود — كلها بمتغيرات بيئة
مع افتراضات آمنة للتطوير المحلي (لا تعقيد استباقي: بدون Docker، بدون قواعد
بيانات إضافية غير Redis).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    session_ttl_seconds: int = int(os.getenv("SESSION_TTL_SECONDS", str(24 * 60 * 60)))
    internal_secret: str = os.getenv("INTERNAL_SECRET", "dev-secret-change-me")
    intent_confidence_threshold: float = float(
        os.getenv("INTENT_CONFIDENCE_THRESHOLD", "0.45")
    )
    resolver_fuzzy_threshold: int = int(os.getenv("RESOLVER_FUZZY_THRESHOLD", "82"))
    intent_model_path: str = os.getenv("INTENT_MODEL_PATH", "data/intent_model.pkl")
    # مسموح بأي أصل افتراضيًا لتسهيل الاختبار المحلي عبر web/test-client.html
    # (صفحة تُفتح من file:// أو من أي منفذ تطوير). قيّدها بقيمة حقيقية بالإنتاج.
    cors_allow_origins: list[str] = field(default_factory=lambda: _split_csv(os.getenv("CORS_ALLOW_ORIGINS", "*")))


settings = Settings()
