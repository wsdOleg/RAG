import re
from difflib import SequenceMatcher

from app.services.chromaStoreService import ChromaStoreService
from app.services.documentService import DocumentService
from app.services.intentService import (
    DOCUMENT_AMOUNT_TOTAL,
    DOCUMENT_COLLECTION_SUMMARY,
    DOCUMENT_COUNT,
    DOCUMENT_FULL_TEXT,
    DOCUMENT_METADATA,
    DOCUMENT_STATUS_ACTIVE,
    DOCUMENT_STATUS_EXPIRED,
    DOCUMENT_STATUS_EXPIRING,
    DOCUMENT_STATUS_NO_DATE,
    DOCUMENT_SUMMARY,
    GENERAL_RAG,
    IntentService,
)
from app.services.llmService import LlmService
from app.services.sessionService import SessionService
from app.utils.formatting import formatAmount, formatDate, formatDocumentType, formatStatus, formatVendor


class RagService:
    def __init__(self) -> None:
        self.documentService = DocumentService()
        self.chromaStoreService = self.documentService.chromaStoreService
        self.intentService = IntentService()
        self.llmService = LlmService()
        self.sessionService = SessionService()

    def askQuestion(self, question: str, sessionId: str | None = None, documentId: str | None = None) -> dict:
        sessionData = self.sessionService.getOrCreateSession(sessionId)
        self.sessionService.saveMessage(sessionData, "user", question)
        intentData = self.intentService.detectIntent(question)

        if intentData["intent"] == DOCUMENT_COLLECTION_SUMMARY:
            response = self.handleDocumentCollectionSummary(question, sessionData)
            self.sessionService.saveMessage(sessionData, "assistant", response["answer"], {"intent": response["intent"]})
            return response
        if intentData["intent"] == DOCUMENT_AMOUNT_TOTAL:
            response = self.handleDocumentAmountTotal(question, sessionData)
            self.sessionService.saveMessage(sessionData, "assistant", response["answer"], {"intent": response["intent"]})
            return response
        if intentData["intent"] in {DOCUMENT_COUNT, DOCUMENT_STATUS_ACTIVE, DOCUMENT_STATUS_EXPIRING, DOCUMENT_STATUS_EXPIRED, DOCUMENT_STATUS_NO_DATE}:
            response = self.handleDocumentStatus(question, sessionData, intentData["intent"])
            self.sessionService.saveMessage(sessionData, "assistant", response["answer"], {"intent": response["intent"]})
            return response

        targetDocument = self.resolveTargetDocument(question, sessionData, documentId, intentData)
        if targetDocument:
            self.sessionService.updateLastDocument(sessionData, targetDocument["id"], targetDocument["title"])

        if intentData["intent"] == DOCUMENT_FULL_TEXT and targetDocument:
            answer = self.buildFullTextAnswer(targetDocument)
            response = self.buildResponse(answer, sessionData, DOCUMENT_FULL_TEXT, "document_text", [], False, False)
            self.sessionService.saveMessage(sessionData, "assistant", answer, {"documentId": targetDocument["id"]})
            return response

        if intentData["intent"] in {DOCUMENT_SUMMARY, DOCUMENT_METADATA} and targetDocument:
            response = self.handleTargetDocumentAnswer(question, targetDocument, sessionData)
            self.sessionService.saveMessage(sessionData, "assistant", response["answer"], {"documentId": targetDocument["id"]})
            return response

        response = self.handleRagAnswer(question, sessionData, targetDocument)
        if targetDocument:
            self.sessionService.updateLastDocument(sessionData, targetDocument["id"], targetDocument["title"])
        self.sessionService.saveMessage(sessionData, "assistant", response["answer"], {"documentId": targetDocument["id"] if targetDocument else None})
        return response

    def handleDocumentCollectionSummary(self, question: str, sessionData: dict) -> dict:
        documents = self.documentService.getDocuments()
        overviewItems: list[dict] = []
        lines = [f"В базе RAG загружено {len(documents)} документов:"]
        for index, document in enumerate(documents, start=1):
            fullDocument = self.documentService.getDocument(document["id"]) or document
            shortSummary = fullDocument.get("shortSummary") or "Краткое описание не сформировано."
            overviewItem = {
                "documentId": document["id"],
                "title": document["title"],
                "documentType": document.get("documentType"),
                "vendor": document.get("vendor"),
                "validTo": document.get("validTo"),
                "amount": document.get("amount"),
                "currency": document.get("currency") or "RUB",
                "businessStatus": document.get("businessStatus"),
                "shortSummary": shortSummary,
                "previewUrl": document.get("previewUrl"),
                "fileUrl": document.get("fileUrl"),
                "firstChunks": (fullDocument.get("chunks") or [])[:1],
            }
            overviewItems.append(overviewItem)
            lines.extend([
                "",
                f"{index}. {document['title']}",
                f"Тип: {formatDocumentType(document.get('documentType'))}.",
                f"Поставщик: {formatVendor(document.get('vendor'))}.",
                f"Срок: {'до ' + formatDate(document.get('validTo')) if document.get('validTo') else 'срок не указан'}.",
                f"Статус: {formatStatus(document.get('businessStatus'))}.",
                f"Сумма: {formatAmount(document.get('amount'), document.get('currency'), emptyText='сумма не указана')}.",
                f"Содержание: {shortSummary}",
            ])
        return self.buildResponse(
            "\n".join(lines).strip(),
            sessionData,
            DOCUMENT_COLLECTION_SUMMARY,
            "document_collection_metadata",
            [],
            usedLlm=False,
            usedVectorSearch=False,
            extra={"documentsOverview": overviewItems},
        )

    def handleDocumentAmountTotal(self, question: str, sessionData: dict) -> dict:
        normalizedQuestion = self.intentService.normalizeText(question)
        documents = self.documentService.getDocuments()
        includedDocuments: list[dict] = []
        excludedDocuments: list[dict] = []
        totalAmount = 0.0
        for document in documents:
            if not self.shouldIncludeDocumentInAmount(normalizedQuestion, document):
                continue
            if document.get("amount"):
                totalAmount += float(document["amount"])
                includedDocuments.append({
                    "documentId": document["id"],
                    "title": document["title"],
                    "documentType": document.get("documentType"),
                    "amount": document.get("amount"),
                    "businessStatus": document.get("businessStatus"),
                    "previewUrl": document.get("previewUrl"),
                })
            else:
                excludedDocuments.append({
                    "documentId": document["id"],
                    "title": document["title"],
                    "reason": "amount_missing",
                    "previewUrl": document.get("previewUrl"),
                })
        totalText = formatAmount(totalAmount, "RUB", emptyText="0 ₽")
        lines = [f"Общая сумма документов с указанной стоимостью: {totalText}."]
        if includedDocuments:
            lines.append("\nВ расчет включены:")
            for index, item in enumerate(includedDocuments, start=1):
                lines.append(
                    f"{index}. {item['title']} — {formatDocumentType(item.get('documentType'))}, "
                    f"сумма: {formatAmount(item.get('amount'), 'RUB')}, статус: {formatStatus(item.get('businessStatus'))}."
                )
        if excludedDocuments:
            lines.append("\nНе включены в расчет, потому что сумма не указана:")
            for index, item in enumerate(excludedDocuments, start=1):
                lines.append(f"{index}. {item['title']}")
        lines.append("\nИсточник: metadata документов RAG.")
        return self.buildResponse(
            "\n".join(lines).strip(),
            sessionData,
            DOCUMENT_AMOUNT_TOTAL,
            "document_metadata",
            [],
            usedLlm=False,
            usedVectorSearch=False,
            extra={
                "totalAmount": round(totalAmount, 2),
                "currency": "RUB",
                "includedDocuments": includedDocuments,
                "excludedDocuments": excludedDocuments,
            },
        )

    def handleDocumentStatus(self, question: str, sessionData: dict, intentName: str) -> dict:
        documents = self.documentService.getDocuments()
        if intentName == DOCUMENT_COUNT:
            answer = f"В базе RAG загружено {len(documents)} документов."
            return self.buildResponse(answer, sessionData, DOCUMENT_COUNT, "document_metadata", [], False, False)

        statusMap = {
            DOCUMENT_STATUS_ACTIVE: ("active", "Активные документы"),
            DOCUMENT_STATUS_EXPIRING: ("expiring", "Документы, истекающие скоро"),
            DOCUMENT_STATUS_EXPIRED: ("expired", "Просроченные документы"),
            DOCUMENT_STATUS_NO_DATE: ("no_date", "Документы без срока"),
        }
        statusKey, title = statusMap[intentName]
        filteredDocuments = [document for document in documents if document.get("businessStatus") == statusKey]
        lines = [f"{title}: найдено {len(filteredDocuments)}."]
        for index, document in enumerate(filteredDocuments, start=1):
            lines.append(
                f"{index}. {document['title']} — тип: {formatDocumentType(document.get('documentType'))}, "
                f"срок: {formatDate(document.get('validTo'))}, поставщик: {formatVendor(document.get('vendor'))}, "
                f"сумма: {formatAmount(document.get('amount'), document.get('currency'), emptyText='сумма не указана')}"
            )
        citations = [
            {
                "documentId": document["id"],
                "documentTitle": document["title"],
                "previewUrl": document.get("previewUrl"),
                "sourceType": "metadata",
            }
            for document in filteredDocuments
        ]
        return self.buildResponse("\n".join(lines), sessionData, intentName, "document_metadata", citations, False, False)

    def handleTargetDocumentAnswer(self, question: str, document: dict, sessionData: dict) -> dict:
        normalizedQuestion = self.intentService.normalizeText(question)
        if any(marker in normalizedQuestion for marker in ("сумма", "стоимость", "цена")):
            answer = f"Сумма документа «{document['title']}»: {formatAmount(document.get('amount'), document.get('currency'))}."
        elif any(marker in normalizedQuestion for marker in ("срок", "дата", "истека", "заканч", "актуален")):
            if document.get("validTo"):
                answer = f"Документ «{document['title']}» актуален до {formatDate(document.get('validTo'))}."
            else:
                answer = f"В metadata документа «{document['title']}» срок действия не указан."
        elif any(marker in normalizedQuestion for marker in ("поставщик", "вендор")):
            answer = f"Поставщик по документу «{document['title']}»: {formatVendor(document.get('vendor'))}."
        else:
            answer = self.buildDocumentSummary(document)
        citations = [{
            "documentId": document["id"],
            "documentTitle": document["title"],
            "previewUrl": f"/api/documents/{document['id']}/preview",
            "sourceType": "metadata",
        }]
        return self.buildResponse(answer, sessionData, DOCUMENT_METADATA, "document_metadata", citations, False, False)

    def handleRagAnswer(self, question: str, sessionData: dict, targetDocument: dict | None = None) -> dict:
        documentId = targetDocument["id"] if targetDocument else None
        searchResults = self.chromaStoreService.searchChunks(question, topK=5 if targetDocument else 7, documentId=documentId)
        if not searchResults:
            answer = "Данные не найдены в загруженных документах."
            return self.buildResponse(answer, sessionData, GENERAL_RAG, "document_rag", [], False, True)

        citations = []
        contextBlocks: list[str] = []
        for result in searchResults[:3]:
            metadata = result["metadata"] or {}
            citations.append({
                "documentId": metadata.get("documentId"),
                "documentTitle": metadata.get("documentTitle"),
                "pageNumber": metadata.get("pageNumber") or None,
                "sourceType": metadata.get("sourceType") or "text",
                "score": result["score"],
                "quote": self.getQuote(question, result["text"]),
                "previewUrl": f"/api/documents/{metadata.get('documentId')}/preview",
                "fileUrl": f"/api/documents/{metadata.get('documentId')}/file",
            })
            pageText = f"стр. {metadata.get('pageNumber')}" if metadata.get("pageNumber") else "без номера страницы"
            contextBlocks.append(
                f"Документ: {metadata.get('documentTitle')}\n"
                f"Источник: {metadata.get('sourceType')}, {pageText}\n"
                f"Фрагмент: {result['text']}"
            )
        answerMode = "brief" if self.intentService.isBriefRequest(question) else "default"
        try:
            answer = self.llmService.generateAnswer(question, "\n\n".join(contextBlocks), answerMode=answerMode)
            usedLlm = True
        except Exception:
            answer = self.getFallbackAnswer(question, targetDocument, citations)
            usedLlm = False
        return self.buildResponse(answer, sessionData, GENERAL_RAG, "document_rag", citations, usedLlm, True)

    def resolveTargetDocument(self, question: str, sessionData: dict, documentId: str | None, intentData: dict) -> dict | None:
        if documentId:
            return self.documentService.getDocument(documentId)
        if intentData.get("isCollection") or intentData["intent"] == DOCUMENT_AMOUNT_TOTAL:
            return None
        documents = self.documentService.getDocuments()
        normalizedQuestion = self.intentService.normalizeText(question)
        bestDocument = None
        bestScore = 0.0
        for document in documents:
            titleScore = SequenceMatcher(None, normalizedQuestion, self.intentService.normalizeText(document["title"])).ratio()
            if document.get("contractNumber") and str(document["contractNumber"]).lower() in normalizedQuestion:
                titleScore = max(titleScore, 0.95)
            if document.get("softwareName") and self.intentService.normalizeText(str(document["softwareName"])) in normalizedQuestion:
                titleScore = max(titleScore, 0.88)
            if titleScore > bestScore and titleScore > 0.45:
                bestScore = titleScore
                bestDocument = self.documentService.getDocument(document["id"])
        if bestDocument:
            return bestDocument
        if intentData.get("isFollowup") and sessionData.get("lastDocumentId"):
            return self.documentService.getDocument(sessionData["lastDocumentId"])
        return None

    def buildDocumentSummary(self, document: dict) -> str:
        shortSummary = document.get("shortSummary") or "Краткое описание не найдено."
        return (
            f"{document['title']} — тип: {formatDocumentType(document.get('documentType'))}, "
            f"поставщик: {formatVendor(document.get('vendor'))}, "
            f"срок: {formatDate(document.get('validTo'))}, "
            f"сумма: {formatAmount(document.get('amount'), document.get('currency'))}. "
            f"{shortSummary}"
        )

    def buildFullTextAnswer(self, document: dict) -> str:
        blocks = document.get("extractedBlocks") or []
        text = "\n\n".join(block.get("text") or "" for block in blocks if block.get("text"))
        if not text.strip():
            return f"В документе «{document['title']}» не найден извлеченный текст."
        return text

    def shouldIncludeDocumentInAmount(self, normalizedQuestion: str, document: dict) -> bool:
        documentType = (document.get("documentType") or "").lower()
        businessStatus = document.get("businessStatus")
        asksContracts = "договор" in normalizedQuestion or "контракт" in normalizedQuestion
        asksLicenses = "лиценз" in normalizedQuestion
        if asksContracts and not asksLicenses and documentType not in {"contract", "agreement"}:
            return False
        if asksLicenses and not asksContracts and documentType != "license":
            return False
        if "просроч" in normalizedQuestion and businessStatus != "expired":
            return False
        if "истека" in normalizedQuestion and businessStatus != "expiring":
            return False
        if "актив" in normalizedQuestion and businessStatus != "active":
            return False
        return True

    def getQuote(self, question: str, text: str) -> str:
        normalizedQuestion = self.intentService.normalizeText(question)
        keywords = [token for token in normalizedQuestion.split() if len(token) > 3]
        normalizedText = re.sub(r"\s+", " ", text)
        for keyword in keywords:
            keywordIndex = normalizedText.lower().find(keyword)
            if keywordIndex >= 0:
                startIndex = max(0, keywordIndex - 180)
                endIndex = min(len(normalizedText), keywordIndex + 420)
                snippet = normalizedText[startIndex:endIndex].strip()
                return snippet + ("..." if endIndex < len(normalizedText) else "")
        return normalizedText[:600] + ("..." if len(normalizedText) > 600 else "")

    def getFallbackAnswer(self, question: str, targetDocument: dict | None, citations: list[dict]) -> str:
        if targetDocument and citations:
            return f"По документу «{targetDocument['title']}» найдено {len(citations)} релевантных фрагмента(ов). Откройте подробности, чтобы посмотреть источники."
        if citations:
            return f"По запросу найдено {len(citations)} релевантных фрагмента(ов) в документах."
        return "Данные не найдены в загруженных документах."

    def buildResponse(
        self,
        answer: str,
        sessionData: dict,
        intent: str,
        sourceType: str,
        citations: list[dict],
        usedLlm: bool,
        usedVectorSearch: bool,
        extra: dict | None = None,
    ) -> dict:
        response = {
            "answer": answer,
            "status": "answered",
            "intent": intent,
            "sourceType": sourceType,
            "sessionId": sessionData["id"],
            "citations": citations,
            "usedLLM": usedLlm,
            "usedVectorSearch": usedVectorSearch,
            "documentsOverview": [],
            "includedDocuments": [],
            "excludedDocuments": [],
            "debug": {
                "pendingClarification": False,
            },
        }
        if extra:
            response.update(extra)
        return response
