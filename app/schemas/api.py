from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    sessionId: str | None = None
    documentId: str | None = None


class AskResponse(BaseModel):
    answer: str
    status: str = "answered"
    intent: str
    sourceType: str
    sessionId: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    usedLLM: bool = False
    usedVectorSearch: bool = False
    documentsOverview: list[dict[str, Any]] = Field(default_factory=list)
    totalAmount: float | None = None
    currency: str | None = None
    includedDocuments: list[dict[str, Any]] = Field(default_factory=list)
    excludedDocuments: list[dict[str, Any]] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    service: str
