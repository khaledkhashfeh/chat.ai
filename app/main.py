"""app/main.py

نقطة الدخول: FastAPI بمسارين فقط — POST /chat و GET /health، مع تحقق رأس
X-Internal-Secret على /chat (البنية الملزمة بـ CLAUDE.md).
"""
from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware  # جزء من fastapi نفسها، ليست مكتبة إضافية

from app.config import settings
from app.conversation.context_extractor import extract_context
from app.conversation.dialogue import handle_turn
from app.conversation.session import SessionStore
from app.shared.models import ChatRequest, ChatResponse, ContextRequest, ConversationContextV1

app = FastAPI(title="طبقة المحادثة الذكية — منصة السياحة الذكية في سوريا")

# يسمح بالاتصال من واجهة الاختبار web/test-client.html (قد تُفتح من file:// أو
# من أي منفذ محلي). settings.cors_allow_origins قابل للتضييق عبر CORS_ALLOW_ORIGINS
# بالإنتاج (راجع app/config.py).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# مثيل وحيد للعملية. يحاول Redis حقيقيًا أولًا (settings.redis_url) ويسقط تلقائيًا
# لنسخة بالذاكرة إن تعذّر الاتصال (مناسب للتطوير المحلي — راجع session.py).
_session_store = SessionStore()


def get_session_store() -> SessionStore:
    return _session_store


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    x_internal_secret: str = Header(..., alias="X-Internal-Secret"),
) -> ChatResponse:
    if x_internal_secret != settings.internal_secret:
        raise HTTPException(status_code=401, detail="unauthorized")

    store = get_session_store()
    session = store.load(request.session_id)
    response = await handle_turn(session, request.message, request.user_id)
    store.save(request.session_id, session)
    return response


@app.post("/context", response_model=ConversationContextV1)
async def context(
    request: ContextRequest,
    x_internal_secret: str = Header(..., alias="X-Internal-Secret"),
) -> ConversationContextV1:
    """يعيد فهمًا منظمًا لرسالة المستخدم (عقد conversation_context_v1) لتستهلكه
    طبقة Laravel: نية + قيود + تفضيلات + جودة فهم — بلا حالة وبلا استدعاء محركات.
    """
    if x_internal_secret != settings.internal_secret:
        raise HTTPException(status_code=401, detail="unauthorized")
    return extract_context(request.message, request.language)
