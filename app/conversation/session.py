"""app/conversation/session.py

حالة المحادثة (ConversationState) وذاكرة العمل (WorkingMemory) مع تخزين/تحميل
من Redis بمفتاح الجلسة، حسب docs/plan.md (الجلسة 1) وdocs/spec.md §2.3-2.4.

- **الحالة (ConversationState):** تتراكم ولا تُنسى خلال الجلسة (قاعدة الوراثة).
- **ذاكرة العمل (WorkingMemory):** تُحدَّث إلزاميًا بعد كل رد (قاعدة صارمة في
  CLAUDE.md: "كل دالة في dialogue.py تحدّث ذاكرة العمل قبل إرجاع الرد").

كلاهما يُخزَّن معًا ككائن `SessionData` واحد بمفتاح `session:{session_id}`
بصلاحية 24 ساعة افتراضيًا.
"""
from __future__ import annotations

import json
from typing import Optional, Protocol

from app.config import settings
from app.shared.models import ConversationState, WorkingMemory


class SessionData:
    """كل ما يخص جلسة محادثة واحدة: الحالة المتراكمة + ذاكرة العمل."""

    def __init__(
        self,
        state: Optional[ConversationState] = None,
        memory: Optional[WorkingMemory] = None,
    ) -> None:
        self.state = state or ConversationState()
        self.memory = memory or WorkingMemory()

    def to_json(self) -> str:
        return json.dumps(
            {
                "state": self.state.model_dump(mode="json"),
                "memory": self.memory.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> "SessionData":
        data = json.loads(raw)
        return cls(
            state=ConversationState.model_validate(data.get("state", {})),
            memory=WorkingMemory.model_validate(data.get("memory", {})),
        )


class RedisLike(Protocol):
    """الحد الأدنى من واجهة عميل Redis المستخدَمة هنا — يسمح بحقن عميل بديل
    بالاختبارات (وبنسخة احتياطية بالذاكرة عند تعذّر الاتصال بخادم Redis فعلي)."""

    def get(self, name: str):
        ...

    def setex(self, name: str, time: int, value: str):
        ...

    def delete(self, *names: str) -> int:
        ...


class InMemoryRedis:
    """نسخة بديلة بالذاكرة تنفّذ نفس واجهة RedisLike الدنيا.

    تُستخدم فقط: (أ) بالاختبارات لتفادي الاعتماد على خادم Redis حقيقي، و
    (ب) كنسخة احتياطية تلقائية في main.py إذا تعذّر الاتصال بـ Redis أثناء
    التطوير المحلي. هذا ليس "قاعدة بيانات إضافية" (مخالفة لـ CLAUDE.md) بل
    شِيم بالذاكرة لعملية واحدة يطابق واجهة Redis الدنيا فقط.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, name: str):
        return self._store.get(name)

    def setex(self, name: str, time: int, value: str):
        self._store[name] = value
        return True

    def delete(self, *names: str) -> int:
        count = 0
        for n in names:
            if n in self._store:
                del self._store[n]
                count += 1
        return count


def _connect_real_redis() -> Optional[RedisLike]:
    """يحاول الاتصال بـ Redis الحقيقي حسب settings.redis_url. يرجع None عند الفشل
    كي يستخدم المستدعي نسخة احتياطية بدل الانهيار (مناسب للتطوير المحلي فقط)."""
    try:
        import redis  # مكتبة مسموحة بـ CLAUDE.md

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None


class SessionStore:
    """يحفظ/يحمّل SessionData من Redis بمفتاح `session:{session_id}` بصلاحية
    settings.session_ttl_seconds (افتراضيًا 24 ساعة)."""

    def __init__(self, client: Optional[RedisLike] = None) -> None:
        self._client = client if client is not None else (_connect_real_redis() or InMemoryRedis())

    @staticmethod
    def _key(session_id: str) -> str:
        return f"session:{session_id}"

    def load(self, session_id: str) -> SessionData:
        raw = self._client.get(self._key(session_id))
        if raw is None:
            return SessionData()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return SessionData.from_json(raw)

    def save(self, session_id: str, session: SessionData) -> None:
        self._client.setex(
            self._key(session_id),
            settings.session_ttl_seconds,
            session.to_json(),
        )

    def clear(self, session_id: str) -> None:
        self._client.delete(self._key(session_id))
