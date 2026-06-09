from fastapi import APIRouter

from app.schemas.api import AskRequest, AskResponse
from app.services.ragService import RagService
from app.services.sessionService import SessionService


router = APIRouter(prefix="/rag", tags=["rag"])
ragService = RagService()
sessionService = SessionService()


@router.post("/ask", response_model=AskResponse)
def askQuestion(request: AskRequest) -> dict:
    return ragService.askQuestion(request.question, request.sessionId, request.documentId)


@router.get("/chat/sessions")
def getChatSessions() -> list[dict]:
    return sessionService.getSessions()


@router.get("/chat/sessions/{sessionId}/messages")
def getChatMessages(sessionId: str) -> dict:
    sessionData = sessionService.getSession(sessionId)
    if not sessionData:
        return {"sessionId": sessionId, "messages": []}
    return {"sessionId": sessionId, "messages": sessionData.get("messages") or []}
