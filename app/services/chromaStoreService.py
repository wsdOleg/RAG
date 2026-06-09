import json
from typing import Any

import chromadb

from app.config import Settings, getSettings
from app.services.embeddingService import EmbeddingService


class ChromaStoreService:
    def __init__(self, settings: Settings | None = None, embeddingService: EmbeddingService | None = None) -> None:
        self.settings = settings or getSettings()
        self.embeddingService = embeddingService or EmbeddingService()
        self.client = chromadb.PersistentClient(path=str(self.settings.chromaDir))
        self.documentsCollection = self.client.get_or_create_collection(self.settings.chromaCollectionDocuments)
        self.chunksCollection = self.client.get_or_create_collection(self.settings.chromaCollectionChunks)

    def getHealth(self) -> dict:
        try:
            return {
                "available": True,
                "documentsCollection": self.settings.chromaCollectionDocuments,
                "chunksCollection": self.settings.chromaCollectionChunks,
                "path": str(self.settings.chromaDir),
                "documentsCount": self.documentsCollection.count(),
                "chunksCount": self.chunksCollection.count(),
            }
        except Exception as error:
            return {"available": False, "error": str(error)}

    def saveDocumentMetadata(self, documentRecord: dict) -> None:
        metadata = self.buildDocumentMetadata(documentRecord)
        self.documentsCollection.upsert(
            ids=[documentRecord["id"]],
            documents=[documentRecord.get("shortSummary") or documentRecord["title"]],
            metadatas=[metadata],
        )

    def saveDocumentChunks(self, documentRecord: dict, chunks: list[dict]) -> None:
        if not chunks:
            return
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        embeddings: list[list[float]] = []
        for chunk in chunks:
            chunkId = chunk["id"]
            ids.append(chunkId)
            documents.append(chunk["text"])
            embeddings.append(self.embeddingService.getEmbedding(chunk["text"]))
            metadatas.append({
                "documentId": documentRecord["id"],
                "documentTitle": documentRecord["title"],
                "documentType": documentRecord.get("documentType") or "document",
                "vendor": documentRecord.get("vendor") or "",
                "softwareName": documentRecord.get("softwareName") or "",
                "pageNumber": chunk.get("pageNumber") or 0,
                "sourceType": chunk.get("sourceType") or "text",
                "extractionMethod": chunk.get("extractionMethod") or "unknown",
                "chunkIndex": chunk.get("chunkIndex") or 0,
                "warningsJson": json.dumps(chunk.get("warnings") or [], ensure_ascii=False),
            })
        self.chunksCollection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)

    def searchChunks(self, query: str, topK: int = 5, documentId: str | None = None) -> list[dict]:
        where = {"documentId": documentId} if documentId else None
        results = self.chunksCollection.query(
            query_embeddings=[self.embeddingService.getEmbedding(query)],
            n_results=topK,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        found: list[dict] = []
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        for rowIndex, chunkId in enumerate(ids):
            distance = float(distances[rowIndex]) if rowIndex < len(distances) else 1.0
            score = max(0.0, min(1.0, 1.0 - distance))
            found.append({
                "chunkId": chunkId,
                "text": documents[rowIndex] if rowIndex < len(documents) else "",
                "metadata": metadatas[rowIndex] if rowIndex < len(metadatas) else {},
                "score": round(score, 4),
            })
        return found

    def deleteDocument(self, documentId: str) -> None:
        self.documentsCollection.delete(ids=[documentId])
        self.chunksCollection.delete(where={"documentId": documentId})

    def buildDocumentMetadata(self, documentRecord: dict) -> dict:
        return {
            "title": documentRecord["title"],
            "originalFileName": documentRecord.get("originalFileName") or "",
            "fileName": documentRecord.get("fileName") or "",
            "mimeType": documentRecord.get("mimeType") or "",
            "fileSize": int(documentRecord.get("fileSize") or 0),
            "documentType": documentRecord.get("documentType") or "document",
            "vendor": documentRecord.get("vendor") or "",
            "contractNumber": documentRecord.get("contractNumber") or "",
            "validFrom": documentRecord.get("validFrom") or "",
            "validTo": documentRecord.get("validTo") or "",
            "amount": float(documentRecord.get("amount") or 0),
            "currency": documentRecord.get("currency") or "RUB",
            "softwareName": documentRecord.get("softwareName") or "",
            "licenseCount": int(documentRecord.get("licenseCount") or 0),
            "comment": documentRecord.get("comment") or "",
            "processingStatus": documentRecord.get("processingStatus") or "indexed",
            "businessStatus": documentRecord.get("businessStatus") or "no_date",
            "createdAt": documentRecord.get("createdAt") or "",
            "updatedAt": documentRecord.get("updatedAt") or "",
        }

