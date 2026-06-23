from fastapi import APIRouter

from app.services.chromaStoreService import ChromaStoreService
from app.services.llmService import LlmService
from app.services.ocrService import OcrService


router = APIRouter(tags=["health"])


@router.get("/health")
def getHealth() -> dict:
    chromaHealth = ChromaStoreService().getHealth()
    llmHealth = LlmService().getHealth()
    ocrHealth = OcrService().getHealth()
    return {
        "status": "ok",
        "service": "RAG Module",
        "chroma": chromaHealth,
        "llm": llmHealth,
        "ocr": ocrHealth,
    }


@router.get("/health/ocr")
def getOcrHealth() -> dict:
    return OcrService().getHealth()


@router.get("/health/llm")
def getLlmHealth() -> dict:
    return LlmService().getHealth()


@router.get("/health/chroma")
def getChromaHealth() -> dict:
    return ChromaStoreService().getHealth()
