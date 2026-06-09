import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.config import Settings, getSettings


class SessionService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or getSettings()

    def getNowIso(self) -> str:
        return datetime.now(UTC).isoformat()

    def getOrCreateSession(self, sessionId: str | None = None) -> dict:
        if sessionId:
            existingSession = self.getSession(sessionId)
            if existingSession:
                return existingSession
        newSessionId = sessionId or uuid4().hex
        sessionData = {
            "id": newSessionId,
            "createdAt": self.getNowIso(),
            "updatedAt": self.getNowIso(),
            "lastDocumentId": None,
            "lastDocumentTitle": None,
            "messages": [],
        }
        self.saveSession(sessionData)
        return sessionData

    def getSession(self, sessionId: str) -> dict | None:
        path = self.getSessionPath(sessionId)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def getSessions(self) -> list[dict]:
        sessions: list[dict] = []
        for path in sorted(self.settings.sessionsDir.glob("*.json")):
            sessions.append(json.loads(path.read_text(encoding="utf-8")))
        sessions.sort(key=lambda session: session.get("updatedAt") or "", reverse=True)
        return sessions

    def saveSession(self, sessionData: dict) -> None:
        sessionData["updatedAt"] = self.getNowIso()
        self.getSessionPath(sessionData["id"]).write_text(json.dumps(sessionData, ensure_ascii=False, indent=2), encoding="utf-8")

    def saveMessage(self, sessionData: dict, role: str, content: str, metadata: dict | None = None) -> None:
        sessionData["messages"].append({
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "createdAt": self.getNowIso(),
        })
        self.saveSession(sessionData)

    def updateLastDocument(self, sessionData: dict, documentId: str | None, documentTitle: str | None) -> None:
        sessionData["lastDocumentId"] = documentId
        sessionData["lastDocumentTitle"] = documentTitle
        self.saveSession(sessionData)

    def getSessionPath(self, sessionId: str) -> Path:
        return self.settings.sessionsDir / f"{sessionId}.json"
