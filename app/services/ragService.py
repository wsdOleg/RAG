import calendar
import re
from difflib import SequenceMatcher
from datetime import date, timedelta

from app.services.chromaStoreService import ChromaStoreService
from app.services.clarificationService import ClarificationService
from app.services.documentService import DocumentService
from app.services.intentService import (
    DOCUMENT_AMOUNT_TOTAL,
    DOCUMENT_COLLECTION_SUMMARY,
    DOCUMENT_CONTRACT_DATES,
    DOCUMENT_COUNT,
    DOCUMENT_COUNT_INDEXED,
    DOCUMENT_FULL_TEXT,
    DOCUMENT_METADATA,
    DOCUMENT_PARTIES,
    DOCUMENT_STATUS_ACTIVE,
    DOCUMENT_STATUS_EXPIRED,
    DOCUMENT_STATUS_FAILED,
    DOCUMENT_STATUS_EXPIRING,
    DOCUMENT_STATUS_NO_DATE,
    DOCUMENT_STATUS_PROCESSING,
    DOCUMENT_SUMMARY,
    DOCUMENT_VENDOR_FILTER,
    GENERAL_RAG,
    IntentService,
)
from app.services.llmService import LlmService
from app.services.sessionService import SessionService
from app.utils.formatting import formatAmount, formatDate, formatDocumentType, formatStatus, formatVendor
from app.utils.status import parseDateValue


class RagService:
    corpusLevelIntents = {
        DOCUMENT_COLLECTION_SUMMARY,
        DOCUMENT_AMOUNT_TOTAL,
        DOCUMENT_COUNT,
        DOCUMENT_COUNT_INDEXED,
        DOCUMENT_STATUS_ACTIVE,
        DOCUMENT_STATUS_EXPIRING,
        DOCUMENT_STATUS_EXPIRED,
        DOCUMENT_STATUS_NO_DATE,
        DOCUMENT_STATUS_PROCESSING,
        DOCUMENT_STATUS_FAILED,
        DOCUMENT_VENDOR_FILTER,
    }

    documentScopedIntents = {
        DOCUMENT_METADATA,
        DOCUMENT_PARTIES,
        DOCUMENT_CONTRACT_DATES,
        DOCUMENT_SUMMARY,
        DOCUMENT_FULL_TEXT,
    }

    def __init__(self) -> None:
        self.documentService = DocumentService()
        self.chromaStoreService = self.documentService.chromaStoreService
        self.clarificationService = ClarificationService()
        self.intentService = IntentService()
        self.llmService = LlmService()
        self.sessionService = SessionService()

    def askQuestion(self, question: str, sessionId: str | None = None, documentId: str | None = None) -> dict:
        sessionData = self.sessionService.getOrCreateSession(sessionId)
        self.sessionService.saveMessage(sessionData, "user", question)
        clarificationResolution = self.clarificationService.applyClarificationAnswer(question, sessionData)
        effectiveQuestion = question
        effectiveDocumentId = documentId
        debugData = self.createDebugData(question, sessionData, documentId)
        if clarificationResolution:
            if clarificationResolution.get("clearPending"):
                self.sessionService.clearPendingClarification(sessionData)
            if clarificationResolution.get("resolved"):
                effectiveQuestion = clarificationResolution.get("question") or question
                effectiveDocumentId = clarificationResolution.get("documentId") or documentId
                self.sessionService.clearPendingClarification(sessionData, resetCount=True)
        debugData["effectiveQuestion"] = effectiveQuestion
        debugData["passedDocumentId"] = effectiveDocumentId

        intentData = self.intentService.detectIntent(effectiveQuestion)
        debugData["detectedIntent"] = intentData["intent"]
        if effectiveDocumentId and not self.documentService.getDocument(effectiveDocumentId):
            response = self.buildResponse(
                "Документ не найден. Проверьте documentId или выберите документ заново.",
                sessionData,
                "DOCUMENT_NOT_FOUND",
                "document_metadata",
                [],
                False,
                False,
            )
            response["status"] = "no_context"
            self.attachDebugData(response, debugData, handlerName="missing_document", usedRequisitesExtractor=False, usedVectorSearch=False)
            self.sessionService.saveMessage(sessionData, "assistant", response["answer"], {"intent": response["intent"], "response": response})
            return response

        if intentData["intent"] == DOCUMENT_COLLECTION_SUMMARY:
            response = self.handleDocumentCollectionSummary(effectiveQuestion, sessionData)
            self.attachDebugData(response, debugData, handlerName="handleDocumentCollectionSummary", usedRequisitesExtractor=False, usedVectorSearch=False)
            self.sessionService.updateLastCollection(sessionData, [item["documentId"] for item in response.get("documentsOverview") or []])
            self.sessionService.clearPendingClarification(sessionData, resetCount=True)
            self.sessionService.saveMessage(sessionData, "assistant", response["answer"], {"intent": response["intent"], "response": response})
            return response
        if intentData["intent"] == DOCUMENT_AMOUNT_TOTAL:
            response = self.handleDocumentAmountTotal(effectiveQuestion, sessionData)
            self.attachDebugData(response, debugData, handlerName="handleDocumentAmountTotal", usedRequisitesExtractor=False, usedVectorSearch=False)
            self.sessionService.clearPendingClarification(sessionData, resetCount=True)
            self.sessionService.saveMessage(sessionData, "assistant", response["answer"], {"intent": response["intent"], "response": response})
            return response
        if intentData["intent"] in {
            DOCUMENT_COUNT,
            DOCUMENT_COUNT_INDEXED,
            DOCUMENT_STATUS_ACTIVE,
            DOCUMENT_STATUS_EXPIRING,
            DOCUMENT_STATUS_EXPIRED,
            DOCUMENT_STATUS_NO_DATE,
            DOCUMENT_STATUS_PROCESSING,
            DOCUMENT_STATUS_FAILED,
            DOCUMENT_VENDOR_FILTER,
        }:
            response = self.handleDocumentStatus(effectiveQuestion, sessionData, intentData["intent"])
            self.attachDebugData(response, debugData, handlerName="handleDocumentStatus", usedRequisitesExtractor=False, usedVectorSearch=False)
            self.sessionService.updateLastCollection(sessionData, [citation["documentId"] for citation in response.get("citations") or [] if citation.get("documentId")])
            self.sessionService.clearPendingClarification(sessionData, resetCount=True)
            self.sessionService.saveMessage(sessionData, "assistant", response["answer"], {"intent": response["intent"], "response": response})
            return response

        clarificationResponse = self.clarificationService.buildClarification(effectiveQuestion, intentData, sessionData, effectiveDocumentId)
        if clarificationResponse:
            self.sessionService.setPendingClarification(sessionData, clarificationResponse.pop("clarificationState"))
            clarificationResponse["sessionId"] = sessionData["id"]
            self.attachDebugData(clarificationResponse, debugData, handlerName="buildClarification", usedRequisitesExtractor=False, usedVectorSearch=False)
            self.sessionService.saveMessage(sessionData, "assistant", clarificationResponse["answer"], {"intent": clarificationResponse["intent"], "response": clarificationResponse})
            return clarificationResponse

        scopeResolution = self.resolveQuestionScope(effectiveQuestion, sessionData, effectiveDocumentId, intentData)
        debugData["selectionMode"] = scopeResolution["mode"]
        targetDocument = scopeResolution.get("document")
        if scopeResolution["mode"] == "clarification_required":
            clarificationResponse = self.clarificationService.buildClarification(effectiveQuestion, intentData, sessionData, effectiveDocumentId)
            if not clarificationResponse:
                clarificationResponse = self.clarificationService.createClarificationPayload(
                    sessionData,
                    effectiveQuestion,
                    "document_selection_required",
                    self.clarificationService.buildClarificationQuestion(self.intentService.normalizeText(effectiveQuestion)),
                    self.clarificationService.buildDocumentOptions(self.clarificationService.getRelevantDocuments(self.intentService.normalizeText(effectiveQuestion))),
                )
            self.sessionService.setPendingClarification(sessionData, clarificationResponse.pop("clarificationState"))
            clarificationResponse["sessionId"] = sessionData["id"]
            self.attachDebugData(clarificationResponse, debugData, handlerName="resolveQuestionScope", usedRequisitesExtractor=False, usedVectorSearch=False)
            self.sessionService.saveMessage(sessionData, "assistant", clarificationResponse["answer"], {"intent": clarificationResponse["intent"], "response": clarificationResponse})
            return clarificationResponse
        debugData["selectedDocumentId"] = targetDocument["id"] if targetDocument else None
        debugData["selectedDocumentTitle"] = targetDocument["title"] if targetDocument else None
        if targetDocument:
            self.sessionService.updateLastDocument(sessionData, targetDocument["id"], targetDocument["title"])
            self.sessionService.clearPendingClarification(sessionData, resetCount=True)

        if intentData["intent"] == DOCUMENT_FULL_TEXT and targetDocument:
            answer = self.buildFullTextAnswer(targetDocument)
            response = self.buildResponse(answer, sessionData, DOCUMENT_FULL_TEXT, "document_text", [], False, False)
            self.attachDebugData(response, debugData, handlerName="buildFullTextAnswer", usedRequisitesExtractor=False, usedVectorSearch=False)
            self.sessionService.saveMessage(sessionData, "assistant", answer, {"documentId": targetDocument["id"], "response": response})
            return response

        if targetDocument:
            response = self.tryHandleStructuredDocumentAnswer(effectiveQuestion, targetDocument, sessionData, intentData["intent"])
            if response:
                self.attachDebugData(
                    response,
                    debugData,
                    handlerName=response.get("debug", {}).get("handlerName") or "tryHandleStructuredDocumentAnswer",
                    usedRequisitesExtractor=response.get("sourceType") == "document_requisites",
                    usedVectorSearch=response.get("usedVectorSearch", False),
                )
                self.sessionService.saveMessage(sessionData, "assistant", response["answer"], {"documentId": targetDocument["id"], "response": response})
                return response

        if intentData["intent"] in {DOCUMENT_SUMMARY, DOCUMENT_METADATA, DOCUMENT_PARTIES, DOCUMENT_CONTRACT_DATES} and targetDocument:
            response = self.handleTargetDocumentAnswer(effectiveQuestion, targetDocument, sessionData, intentData["intent"])
            self.attachDebugData(response, debugData, handlerName="handleTargetDocumentAnswer", usedRequisitesExtractor=False, usedVectorSearch=False)
            self.sessionService.saveMessage(sessionData, "assistant", response["answer"], {"documentId": targetDocument["id"], "response": response})
            return response

        response = self.handleRagAnswer(effectiveQuestion, sessionData, targetDocument)
        self.attachDebugData(response, debugData, handlerName="handleRagAnswer", usedRequisitesExtractor=False, usedVectorSearch=True)
        if targetDocument:
            self.sessionService.updateLastDocument(sessionData, targetDocument["id"], targetDocument["title"])
        self.sessionService.clearPendingClarification(sessionData, resetCount=True)
        self.sessionService.saveMessage(sessionData, "assistant", response["answer"], {"documentId": targetDocument["id"] if targetDocument else None, "response": response})
        return response

    def handleDocumentCollectionSummary(self, question: str, sessionData: dict) -> dict:
        documents = self.filterCollectionDocuments(question, self.documentService.getDocuments())
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

    def filterCollectionDocuments(self, question: str, documents: list[dict]) -> list[dict]:
        normalizedQuestion = self.intentService.normalizeText(question)
        if "договор" in normalizedQuestion and "лиценз" not in normalizedQuestion:
            filteredDocuments = [
                document for document in documents if (document.get("documentType") or "").lower() in {"contract", "agreement"}
            ]
            return filteredDocuments or documents
        if "лиценз" in normalizedQuestion and "договор" not in normalizedQuestion:
            filteredDocuments = [
                document for document in documents if (document.get("documentType") or "").lower() == "license"
            ]
            return filteredDocuments or documents
        return documents

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
        normalizedQuestion = self.intentService.normalizeText(question)
        if intentName == DOCUMENT_COUNT:
            answer = f"В базе RAG загружено {len(documents)} документов."
            return self.buildResponse(answer, sessionData, DOCUMENT_COUNT, "document_metadata", [], False, False)
        if intentName == DOCUMENT_COUNT_INDEXED:
            indexedCount = sum(1 for document in documents if document.get("processingStatus") == "indexed")
            answer = f"Индексировано документов: {indexedCount}."
            return self.buildResponse(answer, sessionData, DOCUMENT_COUNT_INDEXED, "document_metadata", [], False, False)
        if intentName == DOCUMENT_VENDOR_FILTER:
            vendorQuery = self.extractVendorQuery(normalizedQuestion)
            filteredDocuments = [
                document
                for document in documents
                if vendorQuery and vendorQuery in self.intentService.normalizeText(str(document.get("vendor") or ""))
            ]
            lines = [f"Документы по поставщику: найдено {len(filteredDocuments)}."]
            for index, document in enumerate(filteredDocuments, start=1):
                lines.append(
                    f"{index}. {document['title']} — тип: {formatDocumentType(document.get('documentType'))}, "
                    f"срок: {formatDate(document.get('validTo'))}, сумма: {formatAmount(document.get('amount'), document.get('currency'), emptyText='сумма не указана')}"
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
            return self.buildResponse("\n".join(lines), sessionData, DOCUMENT_VENDOR_FILTER, "document_metadata", citations, False, False)

        statusMap = {
            DOCUMENT_STATUS_ACTIVE: ("active", "Активные документы"),
            DOCUMENT_STATUS_EXPIRING: ("expiring", "Документы, истекающие скоро"),
            DOCUMENT_STATUS_EXPIRED: ("expired", "Просроченные документы"),
            DOCUMENT_STATUS_NO_DATE: ("no_date", "Документы без срока"),
            DOCUMENT_STATUS_PROCESSING: ("processing", "Документы в обработке"),
            DOCUMENT_STATUS_FAILED: ("failed", "Документы с ошибкой"),
        }
        statusKey, title = statusMap[intentName]
        if statusKey in {"processing", "failed"}:
            filteredDocuments = [document for document in documents if document.get("processingStatus") == statusKey]
        else:
            filteredDocuments = [document for document in documents if document.get("businessStatus") == statusKey]
        lines = [f"{title}: найдено {len(filteredDocuments)}."]
        for index, document in enumerate(filteredDocuments, start=1):
            if statusKey in {"processing", "failed"}:
                lines.append(
                    f"{index}. {document['title']} — тип: {formatDocumentType(document.get('documentType'))}, "
                    f"статус индексации: {document.get('processingStatus')}, поставщик: {formatVendor(document.get('vendor'))}"
                )
            else:
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

    def handleTargetDocumentAnswer(self, question: str, document: dict, sessionData: dict, intentName: str = DOCUMENT_METADATA) -> dict:
        normalizedQuestion = self.intentService.normalizeText(question)
        if intentName == DOCUMENT_CONTRACT_DATES or self.intentService.isContractDatesQuestion(normalizedQuestion):
            return self.buildContractDatesAnswer(document, sessionData)
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
        return self.buildResponse(answer, sessionData, intentName, "document_metadata", citations, False, False)

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

    def resolveQuestionScope(self, question: str, sessionData: dict, documentId: str | None, intentData: dict) -> dict:
        if intentData.get("isCollection") or intentData["intent"] in self.corpusLevelIntents:
            return {"mode": "all_documents", "document": None}
        targetDocument = self.resolveTargetDocument(question, sessionData, documentId, intentData)
        if targetDocument:
            return {"mode": "document", "document": targetDocument}
        if self.shouldRequireExplicitDocument(question, sessionData, intentData):
            return {"mode": "clarification_required", "document": None}
        return {"mode": "search", "document": None}

    def resolveTargetDocument(self, question: str, sessionData: dict, documentId: str | None, intentData: dict) -> dict | None:
        if documentId:
            return self.documentService.getDocument(documentId)
        if intentData.get("isCollection") or intentData["intent"] in self.corpusLevelIntents:
            return None
        ordinalDocument = self.resolveOrdinalDocument(question, sessionData)
        if ordinalDocument:
            return ordinalDocument
        documents = self.documentService.getDocuments()
        normalizedQuestion = self.intentService.normalizeText(question)
        indexedDocuments = [document for document in documents if document.get("processingStatus") == "indexed"]
        if sessionData.get("lastDocumentId") and (intentData.get("isFollowup") or self.isDocumentScopedIntent(intentData["intent"])):
            sessionDocument = self.documentService.getDocument(sessionData["lastDocumentId"])
            if sessionDocument:
                return sessionDocument
        if len(indexedDocuments) == 1:
            return self.documentService.getDocument(indexedDocuments[0]["id"])
        if len(documents) == 1:
            return self.documentService.getDocument(documents[0]["id"])
        if self.shouldRequireExplicitDocument(question, sessionData, intentData):
            return None
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
        return None

    def shouldRequireExplicitDocument(self, question: str, sessionData: dict, intentData: dict) -> bool:
        if not self.isDocumentScopedIntent(intentData["intent"]):
            return False
        if sessionData.get("lastDocumentId"):
            return False
        if intentData.get("isFollowup") and sessionData.get("lastCollectionDocumentIds"):
            return False
        documents = self.documentService.getDocuments()
        indexedDocuments = [document for document in documents if document.get("processingStatus") == "indexed"]
        if len(indexedDocuments) <= 1 and len(documents) <= 1:
            return False
        if self.hasStrongDocumentReference(question, documents):
            return False
        return True

    def isDocumentScopedIntent(self, intentName: str) -> bool:
        return intentName in self.documentScopedIntents

    def hasStrongDocumentReference(self, question: str, documents: list[dict]) -> bool:
        normalizedQuestion = self.intentService.normalizeText(question)
        matchedDocuments = self.clarificationService.findMatchingDocuments(question)
        if len(matchedDocuments) == 1:
            return True
        for document in documents:
            contractNumber = str(document.get("contractNumber") or "").strip().lower()
            if contractNumber and contractNumber in normalizedQuestion:
                return True
            softwareName = self.intentService.normalizeText(str(document.get("softwareName") or ""))
            if softwareName and len(softwareName) > 3 and softwareName in normalizedQuestion:
                return True
        return False

    def resolveOrdinalDocument(self, question: str, sessionData: dict) -> dict | None:
        normalizedQuestion = self.intentService.normalizeText(question)
        collectionDocumentIds = sessionData.get("lastCollectionDocumentIds") or []
        if not collectionDocumentIds:
            return None
        ordinalMap = self.clarificationService.ordinalMap
        for token in normalizedQuestion.split():
            if token not in ordinalMap:
                continue
            index = ordinalMap[token]
            if index == -1 and collectionDocumentIds:
                return self.documentService.getDocument(collectionDocumentIds[-1])
            if 0 <= index < len(collectionDocumentIds):
                return self.documentService.getDocument(collectionDocumentIds[index])
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

    def extractVendorQuery(self, normalizedQuestion: str) -> str:
        vendorMatch = re.search(r"по поставщику\s+(.+)$", normalizedQuestion)
        if not vendorMatch:
            return ""
        return vendorMatch.group(1).strip()

    def tryHandleStructuredDocumentAnswer(
        self,
        question: str,
        document: dict,
        sessionData: dict,
        intentName: str,
    ) -> dict | None:
        normalizedQuestion = self.intentService.normalizeText(question)
        if intentName == DOCUMENT_CONTRACT_DATES or self.intentService.isContractDatesQuestion(normalizedQuestion):
            return self.buildContractDatesAnswer(document, sessionData)
        if self.intentService.isPartyQuestion(normalizedQuestion):
            return self.buildPartiesAnswer(document, sessionData, normalizedQuestion)
        if self.intentService.isRequisitesQuestion(normalizedQuestion):
            return self.buildRequisitesAnswer(document, sessionData)
        if intentName in {DOCUMENT_METADATA, DOCUMENT_PARTIES, DOCUMENT_CONTRACT_DATES}:
            return self.handleTargetDocumentAnswer(question, document, sessionData, intentName)
        return None

    def buildContractDatesAnswer(self, document: dict, sessionData: dict) -> dict:
        contractDatesInfo = self.extractContractDatesInfo(document)
        citations = contractDatesInfo["citations"]
        explicitEndDate = contractDatesInfo.get("explicitEndDate")
        if explicitEndDate:
            answer = (
                f"Срок действия по документу «{document['title']}»: до {formatDate(explicitEndDate)}."
            )
            return self.buildResponse(
                answer,
                sessionData,
                DOCUMENT_CONTRACT_DATES,
                "document_term_extractor",
                citations,
                False,
                False,
                extra={"debug": {"handlerName": "buildContractDatesAnswer"}},
            )

        serviceTermText = contractDatesInfo.get("serviceTermText")
        documentDate = contractDatesInfo.get("documentDate")
        accessWindowDays = contractDatesInfo.get("accessWindowDays")
        if serviceTermText and documentDate and accessWindowDays:
            startDate = parseDateValue(documentDate)
            monthsCount = contractDatesInfo.get("serviceTermMonths")
            if startDate and monthsCount:
                earliestEndDate = self.addMonths(startDate, monthsCount)
                latestAccessDate = self.addWorkingDays(startDate, accessWindowDays)
                latestEndDate = self.addMonths(latestAccessDate, monthsCount)
                answer = (
                    "Точная дата окончания зависит от даты предоставления доступа. "
                    f"По договору срок действия установлен как {serviceTermText}. "
                    f"Доступ должен быть предоставлен в течение {accessWindowDays} рабочих дней с даты подписания договора {formatDate(documentDate)}. "
                    f"Если доступ предоставлен в день подписания, ориентировочная дата окончания — {formatDate(earliestEndDate)}; "
                    f"если в последний допустимый срок — около {formatDate(latestEndDate)}."
                )
                return self.buildResponse(
                    answer,
                    sessionData,
                    DOCUMENT_CONTRACT_DATES,
                    "document_term_extractor",
                    citations,
                    False,
                    False,
                    extra={"debug": {"handlerName": "buildContractDatesAnswer"}},
                )

        if serviceTermText:
            baseAnswer = (
                f"По договору срок действия установлен как {serviceTermText}. "
                "Точная дата окончания зависит от фактической даты предоставления доступа."
            )
            if documentDate and accessWindowDays:
                baseAnswer += (
                    f" Доступ должен быть предоставлен в течение {accessWindowDays} рабочих дней "
                    f"с даты подписания договора {formatDate(documentDate)}."
                )
            return self.buildResponse(
                baseAnswer,
                sessionData,
                DOCUMENT_CONTRACT_DATES,
                "document_term_extractor",
                citations,
                False,
                False,
                extra={"debug": {"handlerName": "buildContractDatesAnswer"}},
            )

        if document.get("validTo"):
            answer = f"Документ «{document['title']}» актуален до {formatDate(document.get('validTo'))}."
            metadataCitation = [{
                "documentId": document["id"],
                "documentTitle": document["title"],
                "previewUrl": f"/api/documents/{document['id']}/preview",
                "sourceType": "metadata",
            }]
            return self.buildResponse(
                answer,
                sessionData,
                DOCUMENT_CONTRACT_DATES,
                "document_metadata",
                metadataCitation,
                False,
                False,
                extra={"debug": {"handlerName": "buildContractDatesAnswer"}},
            )

        return self.buildResponse(
            f"В тексте и metadata документа «{document['title']}» срок действия не удалось определить достаточно точно.",
            sessionData,
            DOCUMENT_CONTRACT_DATES,
            "document_term_extractor",
            citations,
            False,
            False,
            extra={"debug": {"handlerName": "buildContractDatesAnswer"}},
        )

    def extractContractDatesInfo(self, document: dict) -> dict:
        blocks = document.get("extractedBlocks") or []
        serviceTermBlock = self.findFirstBlock(
            blocks,
            (
                "действует в течение",
                "календарных месяцев с момента предоставления доступа",
                "срок оказания услуг",
            ),
        )
        accessBlock = self.findAccessProvisionBlock(blocks)
        explicitEndDateBlock = self.findFirstBlock(
            blocks,
            (
                "действует до",
                "срок действия до",
                "дата окончания",
            ),
        )
        documentDate = document.get("documentDate") or document.get("validFrom")
        serviceTermText = document.get("serviceTerm")
        if not serviceTermText and serviceTermBlock:
            match = re.search(
                r"(?:в течение\s+\d+\s*\([^)]+\)\s*календарных\s+месяцев\s+с\s+момента\s+предоставления\s+доступа|"
                r"\d+\s*\([^)]+\)\s*месяц[а-я\s]+с\s+момента\s+предоставления\s+доступа)",
                re.sub(r"\s+", " ", serviceTermBlock.get("text") or ""),
                re.IGNORECASE,
            )
            if match:
                serviceTermText = match.group(0).strip()

        accessWindowDays = None
        if accessBlock:
            normalizedAccessText = re.sub(r"\s+", " ", accessBlock.get("text") or "").strip()
            accessMatch = re.search(
                r"доступ\s+к\s+программ[еы].{0,240}?предоставля[а-я\s]{0,120}?в\s+течение\s+(\d+)\s*\([^)]+\)\s*рабочих\s+дн",
                normalizedAccessText,
                re.IGNORECASE,
            )
            if not accessMatch:
                accessMatch = re.search(
                    r"предоставля[а-я\s]{0,120}?в\s+течение\s+(\d+)\s*\([^)]+\)\s*рабочих\s+дн[а-я\s]{0,120}?даты\s+подписания",
                    normalizedAccessText,
                    re.IGNORECASE,
                )
            if not accessMatch:
                accessMatch = re.search(
                    r"5\.1\.[^\n]{0,260}?в\s+течение\s+(\d+)\s*\([^)]+\)\s*рабочих\s+дн",
                    normalizedAccessText,
                re.IGNORECASE,
            )
            if accessMatch:
                accessWindowDays = int(accessMatch.group(1))

        explicitEndDate = None
        if explicitEndDateBlock:
            explicitDateMatch = re.search(
                r"(\d{2}[.\-/]\d{2}[.\-/]\d{4})",
                explicitEndDateBlock.get("text") or "",
            )
            if explicitDateMatch:
                explicitEndDate = self.normalizeDateValue(explicitDateMatch.group(1))

        citations: list[dict] = []
        for block in (serviceTermBlock, accessBlock, explicitEndDateBlock):
            if not block:
                continue
            candidateCitation = self.buildBlockCitation(document, block, self.getQuote("срок договора", block.get("text") or ""))
            if candidateCitation not in citations:
                citations.append(candidateCitation)

        return {
            "documentDate": documentDate,
            "serviceTermText": serviceTermText,
            "serviceTermMonths": self.extractMonthsCount(serviceTermText),
            "accessWindowDays": accessWindowDays,
            "explicitEndDate": explicitEndDate,
            "citations": citations,
        }

    def findFirstBlock(self, blocks: list[dict], markers: tuple[str, ...]) -> dict | None:
        for block in blocks:
            normalizedText = self.intentService.normalizeText(block.get("text") or "")
            if all(marker in normalizedText for marker in markers):
                return block
        for block in blocks:
            normalizedText = self.intentService.normalizeText(block.get("text") or "")
            if any(marker in normalizedText for marker in markers):
                return block
        return None

    def findAccessProvisionBlock(self, blocks: list[dict]) -> dict | None:
        strictPattern = re.compile(
            r"доступ\s+к\s+программ[еы][^\n]{0,240}?предоставля[а-я\s]{0,120}?в\s+течение\s+\d+\s*\([^)]+\)\s*рабочих\s+дн[а-я\s]{0,120}?даты\s+подписания",
            re.IGNORECASE,
        )
        fallbackPattern = re.compile(
            r"предоставля[а-я\s]{0,120}?в\s+течение\s+\d+\s*\([^)]+\)\s*рабочих\s+дн[а-я\s]{0,120}?даты\s+подписания",
            re.IGNORECASE,
        )
        for block in blocks:
            normalizedText = re.sub(r"\s+", " ", block.get("text") or "").strip()
            if strictPattern.search(normalizedText):
                return block
        for block in blocks:
            normalizedText = re.sub(r"\s+", " ", block.get("text") or "").strip()
            if fallbackPattern.search(normalizedText):
                return block
        return None

    def extractMonthsCount(self, serviceTermText: str | None) -> int | None:
        if not serviceTermText:
            return None
        match = re.search(r"(\d+)", serviceTermText)
        if not match:
            return None
        return int(match.group(1))

    def normalizeDateValue(self, value: str | date | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
        parsedDate = parseDateValue(str(value))
        if not parsedDate:
            return None
        return parsedDate.strftime("%Y-%m-%d")

    def addMonths(self, startDate: date, monthsCount: int) -> date:
        monthIndex = startDate.month - 1 + monthsCount
        targetYear = startDate.year + monthIndex // 12
        targetMonth = monthIndex % 12 + 1
        targetDay = min(startDate.day, calendar.monthrange(targetYear, targetMonth)[1])
        return date(targetYear, targetMonth, targetDay)

    def addWorkingDays(self, startDate: date, workingDays: int) -> date:
        currentDate = startDate
        addedDays = 0
        while addedDays < workingDays:
            currentDate += timedelta(days=1)
            if currentDate.weekday() < 5:
                addedDays += 1
        return currentDate

    def buildPartiesAnswer(self, document: dict, sessionData: dict, normalizedQuestion: str) -> dict:
        partyInfo = self.extractPartyInfo(document)
        citations: list[dict] = []
        asksCustomer = self.intentService.isCustomerQuestion(normalizedQuestion)
        asksExecutor = self.intentService.isExecutorQuestion(normalizedQuestion)
        asksParties = self.intentService.isPartiesQuestion(normalizedQuestion)

        if asksCustomer and partyInfo.get("customer"):
            customer = partyInfo["customer"]
            citations.append(self.buildBlockCitation(document, customer["block"], customer["name"]))
            answer = f"Заказчик по документу «{document['title']}»: {customer['name']}."
            return self.buildResponse(
                answer,
                sessionData,
                DOCUMENT_PARTIES,
                "document_requisites",
                citations,
                False,
                False,
                extra={"debug": {"handlerName": "buildPartiesAnswer"}},
            )

        if asksExecutor and partyInfo.get("executor"):
            executor = partyInfo["executor"]
            citations.append(self.buildBlockCitation(document, executor["block"], executor["name"]))
            roleLabel = self.getExecutorRoleLabel(normalizedQuestion, executor.get("role"))
            answer = f"{roleLabel} по документу «{document['title']}»: {executor['name']}."
            return self.buildResponse(
                answer,
                sessionData,
                DOCUMENT_PARTIES,
                "document_requisites",
                citations,
                False,
                False,
                extra={"debug": {"handlerName": "buildPartiesAnswer"}},
            )

        if asksParties and (partyInfo.get("customer") or partyInfo.get("executor")):
            lines = [f"Стороны по документу «{document['title']}»:"] 
            if partyInfo.get("executor"):
                executor = partyInfo["executor"]
                lines.append(f"{self.getExecutorRoleLabel(normalizedQuestion, executor.get('role'))}: {executor['name']}.")
                citations.append(self.buildBlockCitation(document, executor["block"], executor["name"]))
            if partyInfo.get("customer"):
                customer = partyInfo["customer"]
                lines.append(f"Заказчик: {customer['name']}.")
                citations.append(self.buildBlockCitation(document, customer["block"], customer["name"]))
            return self.buildResponse(
                "\n".join(lines),
                sessionData,
                DOCUMENT_PARTIES,
                "document_requisites",
                citations,
                False,
                False,
                extra={"debug": {"handlerName": "buildPartiesAnswer"}},
            )

        return self.buildResponse(
            f"В документе «{document['title']}» не удалось уверенно определить стороны договора.",
            sessionData,
            DOCUMENT_PARTIES,
            "document_requisites",
            [],
            False,
            False,
            extra={"debug": {"handlerName": "buildPartiesAnswer"}},
        )

    def buildRequisitesAnswer(self, document: dict, sessionData: dict) -> dict:
        requisitesBlock = self.findRequisitesBlock(document)
        if not requisitesBlock:
            return self.buildResponse(
                f"В документе «{document['title']}» реквизиты сторон не найдены.",
                sessionData,
                DOCUMENT_METADATA,
                "document_requisites",
                [],
                False,
                False,
            )

        previewText = re.sub(r"\s+", " ", requisitesBlock.get("text") or "").strip()
        previewText = previewText[:700].rstrip() + ("..." if len(previewText) > 700 else "")
        citation = self.buildBlockCitation(document, requisitesBlock, previewText)
        answer = f"Реквизиты сторон найдены в документе «{document['title']}». Откройте подробности или документ, чтобы посмотреть полный блок."
        return self.buildResponse(answer, sessionData, DOCUMENT_METADATA, "document_requisites", [citation], False, False)

    def extractPartyInfo(self, document: dict) -> dict:
        customerInfo = None
        executorInfo = None
        for block in self.getPartyPriorityBlocks(document):
            blockText = block.get("text") or ""
            if not customerInfo:
                customerName = self.extractNamedParty(blockText, "Заказчик")
                if customerName and self.isLikelyOrganizationName(customerName):
                    customerInfo = {"name": customerName, "block": block}
            if not executorInfo:
                for roleName in ("Исполнитель", "Поставщик", "Продавец", "Лицензиар"):
                    executorName = self.extractNamedParty(blockText, roleName)
                    if executorName and self.isLikelyOrganizationName(executorName):
                        executorInfo = {"name": executorName, "block": block, "role": roleName}
                        break
            if customerInfo and executorInfo:
                break
        return {"customer": customerInfo, "executor": executorInfo}

    def getPartyPriorityBlocks(self, document: dict) -> list[dict]:
        blocks = document.get("extractedBlocks") or []
        return sorted(blocks, key=self.getPartyBlockPriority, reverse=True)

    def getPartyBlockPriority(self, block: dict) -> int:
        normalizedText = self.intentService.normalizeText(block.get("text") or "")
        score = 0
        if "реквизиты и подписи сторон" in normalizedText:
            score += 100
        if (
            "именуемое в дальнейшем заказчик" in normalizedText
            or "именуемый в дальнейшем заказчик" in normalizedText
            or "именуемое в дальнейшем исполнитель" in normalizedText
            or "именуемый в дальнейшем исполнитель" in normalizedText
            or "именуемое в дальнейшем поставщик" in normalizedText
            or "именуемый в дальнейшем поставщик" in normalizedText
        ):
            score += 80
        if "заказчик" in normalizedText and "исполнитель" in normalizedText:
            score += 40
        if "инн" in normalizedText or "бик" in normalizedText or "юр адрес" in normalizedText:
            score += 20
        pageNumber = block.get("pageNumber") or 0
        if pageNumber == 1:
            score += 10
        return score

    def findRequisitesBlock(self, document: dict) -> dict | None:
        for block in self.getPartyPriorityBlocks(document):
            normalizedText = self.intentService.normalizeText(block.get("text") or "")
            if "реквизиты и подписи сторон" in normalizedText:
                return block
            if ("инн" in normalizedText or "бик" in normalizedText or "юр адрес" in normalizedText) and "заказчик" in normalizedText:
                return block
        return None

    def extractNamedParty(self, text: str, partyRole: str) -> str | None:
        normalizedRole = partyRole.lower()
        markerMatch = re.search(rf"именуем[а-я\s]+[«\"]?{normalizedRole}[»\"]?", text, re.IGNORECASE)
        if not markerMatch:
            markerMatch = re.search(rf"[«\"]?{normalizedRole}[»\"]?", text, re.IGNORECASE)
            if not markerMatch:
                return None
        beforeMarker = text[:markerMatch.start()]
        searchWindow = beforeMarker[-500:]
        loweredWindow = searchWindow.lower()
        if normalizedRole == "заказчик":
            customerSplitMatch = re.search(r"(?:с одной стороны,\s*и|,\s*и)\s*(.+)$", searchWindow, re.IGNORECASE | re.DOTALL)
            if customerSplitMatch:
                searchWindow = customerSplitMatch.group(1)
        elif normalizedRole == "исполнитель":
            splitIndex = loweredWindow.find("с одной стороны")
            if splitIndex > 0:
                searchWindow = searchWindow[:splitIndex]
        organizationPatterns = [
            r"Федеральное государственное бюджетное образовательное учреждение[^\n]{10,260}?«[^»]+»(?:\s*\([^)]{1,40}\))?",
            r"Федеральное государственное бюджетное образовательное учреждение[^\n]{10,260}?(?=(?:\s*\(|\s*,|\s+именуем))",
            r"Общество с ограниченной ответственностью[^\n]{0,80}?«[^»]+»(?:\s*\([^)]{1,40}\))?",
            r"Общество с ограниченной ответственностью[^\n]{0,140}?(?=(?:\s*\(|\s*,|\s+именуем))",
            r'ООО\s+["«][^"»]+["»](?:\s*\([^)]{1,40}\))?',
            r'АО\s+["«][^"»]+["»](?:\s*\([^)]{1,40}\))?',
            r'ПАО\s+["«][^"»]+["»](?:\s*\([^)]{1,40}\))?',
            r"Тюменский индустриальный университет(?:\s*\([^)]{1,40}\))?",
        ]
        for pattern in organizationPatterns:
            matches = list(re.finditer(pattern, searchWindow, re.IGNORECASE | re.DOTALL))
            if matches:
                return self.cleanPartyName(matches[-1].group(0))
        fragments = [fragment.strip() for fragment in re.split(r"[\n,;]", searchWindow) if fragment.strip()]
        for fragment in reversed(fragments):
            if len(fragment) >= 12 and not re.fullmatch(r"[A-Za-zА-Яа-яЁё.\- ]{1,8}", fragment):
                cleanedFragment = self.cleanPartyName(fragment)
                if len(cleanedFragment) >= 12 and self.isLikelyOrganizationName(cleanedFragment):
                    return cleanedFragment
        return None

    def cleanPartyName(self, value: str) -> str:
        cleanedValue = re.sub(r"\s+", " ", value).strip(" .,:;`'\"|-")
        cleanedValue = cleanedValue.replace("(THY)", "(ТИУ)")
        cleanedValue = cleanedValue.replace("(TNY)", "(ТИУ)")
        cleanedValue = cleanedValue.replace("‘", " ")
        cleanedValue = cleanedValue.replace("`", " ")
        cleanedValue = re.sub(r"\s+", " ", cleanedValue)
        return cleanedValue.strip()

    def isLikelyOrganizationName(self, value: str) -> bool:
        normalizedValue = self.intentService.normalizeText(value)
        organizationMarkers = (
            "ооо",
            "ао",
            "пао",
            "общество с ограниченной ответственностью",
            "федеральное государственное",
            "университет",
            "институт",
            "компания",
            "учреждение",
            "инвенторус",
            "тюменский индустриальный университет",
            "тиу",
        )
        return any(marker in normalizedValue for marker in organizationMarkers)

    def getExecutorRoleLabel(self, normalizedQuestion: str, extractedRole: str | None) -> str:
        if "лицензиар" in normalizedQuestion:
            return "Лицензиар"
        if "продавец" in normalizedQuestion:
            return "Продавец"
        if "поставщик" in normalizedQuestion:
            return "Поставщик"
        if extractedRole in {"Поставщик", "Продавец", "Лицензиар"}:
            return extractedRole
        return "Исполнитель"

    def buildBlockCitation(self, document: dict, block: dict, quote: str | None = None) -> dict:
        return {
            "documentId": document["id"],
            "documentTitle": document["title"],
            "pageNumber": block.get("pageNumber") or None,
            "sourceType": block.get("sourceType") or "text",
            "quote": quote or self.getQuote(document["title"], block.get("text") or ""),
            "previewUrl": f"/api/documents/{document['id']}/preview",
            "fileUrl": f"/api/documents/{document['id']}/file",
        }

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
            "clarification": None,
            "usedLLM": usedLlm,
            "usedVectorSearch": usedVectorSearch,
            "documentsOverview": [],
            "includedDocuments": [],
            "excludedDocuments": [],
            "debug": {
                "pendingClarification": False,
                "clarificationCount": int(sessionData.get("clarificationCount") or 0),
            },
        }
        if extra:
            response.update(extra)
        return response

    def createDebugData(self, question: str, sessionData: dict, documentId: str | None) -> dict:
        documents = self.documentService.getDocuments()
        indexedDocuments = [document for document in documents if document.get("processingStatus") == "indexed"]
        return {
            "question": question,
            "passedDocumentId": documentId,
            "sessionLastDocumentId": sessionData.get("lastDocumentId"),
            "sessionLastDocumentTitle": sessionData.get("lastDocumentTitle"),
            "documentsCount": len(documents),
            "indexedDocumentsCount": len(indexedDocuments),
        }

    def attachDebugData(
        self,
        response: dict,
        debugData: dict,
        handlerName: str,
        usedRequisitesExtractor: bool,
        usedVectorSearch: bool,
    ) -> None:
        existingDebug = response.get("debug") or {}
        mergedDebug = {
            **existingDebug,
            **debugData,
            "handlerName": handlerName,
            "usedRequisitesExtractor": usedRequisitesExtractor,
            "usedVectorSearch": usedVectorSearch,
        }
        response["debug"] = mergedDebug
