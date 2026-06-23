import re
from difflib import SequenceMatcher

from app.services.documentService import DocumentService
from app.services.intentService import (
    DOCUMENT_AMOUNT_TOTAL,
    DOCUMENT_COLLECTION_SUMMARY,
    DOCUMENT_CONTRACT_DATES,
    DOCUMENT_COUNT,
    DOCUMENT_FULL_TEXT,
    DOCUMENT_METADATA,
    DOCUMENT_PARTIES,
    DOCUMENT_STATUS_ACTIVE,
    DOCUMENT_STATUS_EXPIRED,
    DOCUMENT_STATUS_EXPIRING,
    DOCUMENT_STATUS_NO_DATE,
    DOCUMENT_SUMMARY,
    GENERAL_RAG,
    IntentService,
)


class ClarificationService:
    ordinalMap = {
        "1": 0,
        "первый": 0,
        "первая": 0,
        "первом": 0,
        "2": 1,
        "второй": 1,
        "вторая": 1,
        "втором": 1,
        "3": 2,
        "третий": 2,
        "третья": 2,
        "третьем": 2,
        "4": 3,
        "четвертый": 3,
        "четвертая": 3,
        "5": 4,
        "пятый": 4,
        "пятая": 4,
        "последний": -1,
        "последняя": -1,
    }

    deterministicIntents = {
        DOCUMENT_COLLECTION_SUMMARY,
        DOCUMENT_AMOUNT_TOTAL,
        DOCUMENT_COUNT,
        DOCUMENT_STATUS_ACTIVE,
        DOCUMENT_STATUS_EXPIRING,
        DOCUMENT_STATUS_EXPIRED,
        DOCUMENT_STATUS_NO_DATE,
    }

    def __init__(self) -> None:
        self.documentService = DocumentService()
        self.intentService = IntentService()

    def applyClarificationAnswer(self, question: str, sessionData: dict) -> dict | None:
        pendingClarification = sessionData.get("pendingClarification")
        if not pendingClarification:
            return None

        selectedOption = self.selectClarificationOption(question, pendingClarification.get("options") or [])
        if selectedOption:
            return {
                "resolved": True,
                "question": pendingClarification.get("originalQuestion") or question,
                "documentId": selectedOption.get("value") if selectedOption.get("type") == "document" else None,
                "selectedOption": selectedOption,
            }

        currentIntent = self.intentService.detectIntent(question)["intent"]
        if currentIntent in self.deterministicIntents or len(question.strip().split()) > 2:
            return {"resolved": False, "clearPending": True}
        return None

    def buildClarification(self, question: str, intentData: dict, sessionData: dict, documentId: str | None = None) -> dict | None:
        if documentId or intentData["intent"] in self.deterministicIntents:
            return None

        normalizedQuestion = self.intentService.normalizeText(question)
        if sessionData.get("lastDocumentId") and (intentData.get("isFollowup") or self.isContextQuestion(normalizedQuestion)):
            return None

        if self.hasOrdinalReference(normalizedQuestion):
            collectionDocumentIds = sessionData.get("lastCollectionDocumentIds") or []
            if collectionDocumentIds:
                return None
            return self.createClarificationPayload(
                sessionData,
                question,
                "missing_collection_context",
                "Не понял, к какому списку относится ссылка на документ. Уточните название документа.",
                [],
            )

        if int(sessionData.get("clarificationCount") or 0) >= 5:
            return self.createClarificationPayload(
                sessionData,
                question,
                "clarification_limit_reached",
                "Не могу ответить без уточнения. Укажите название или номер документа.",
                [],
            )

        candidateDocuments = self.findMatchingDocuments(question)
        if len(candidateDocuments) == 1:
            return None
        if len(candidateDocuments) > 1 and self.shouldClarifyCandidates(candidateDocuments):
            return self.createClarificationPayload(
                sessionData,
                question,
                "multiple_matching_documents",
                "Я нашел несколько подходящих документов. Укажите номер или название документа.",
                self.buildDocumentOptions(candidateDocuments),
            )

        if self.needsDocumentClarification(normalizedQuestion, intentData):
            relevantDocuments = self.getRelevantDocuments(normalizedQuestion)
            if len(relevantDocuments) <= 1 or not self.isGenericDocumentQuestion(normalizedQuestion):
                return None
            clarificationQuestion = self.buildClarificationQuestion(normalizedQuestion)
            return self.createClarificationPayload(
                sessionData,
                question,
                "document_selection_required",
                clarificationQuestion,
                self.buildDocumentOptions(relevantDocuments),
            )

        if self.isTooGeneralQuestion(normalizedQuestion):
            return self.createClarificationPayload(
                sessionData,
                question,
                "too_general",
                "Уточните, что именно нужно проверить: документы, договоры, лицензии, суммы, сроки или реквизиты?",
                [
                    {"label": "Документы", "value": "документы", "type": "topic"},
                    {"label": "Договоры", "value": "договоры", "type": "topic"},
                    {"label": "Лицензии", "value": "лицензии", "type": "topic"},
                    {"label": "Реквизиты", "value": "реквизиты", "type": "topic"},
                ],
            )
        return None

    def buildClarificationQuestion(self, normalizedQuestion: str) -> str:
        if any(marker in normalizedQuestion for marker in ("сумма", "стоимость", "цена")):
            return "Уточните, по какому документу нужно найти сумму?"
        if any(marker in normalizedQuestion for marker in ("срок", "дата", "истека", "заканч", "актуален")):
            return "Уточните, для какого договора или лицензии проверить срок действия?"
        if self.intentService.isRequisitesQuestion(normalizedQuestion):
            return "Уточните, по какому документу показать реквизиты сторон?"
        if self.intentService.isPartyQuestion(normalizedQuestion) or "вендор" in normalizedQuestion:
            return "Уточните, по какому документу нужно определить сторону договора?"
        if any(marker in normalizedQuestion for marker in ("статус", "что с договором")):
            return "Уточните, по какому документу нужно проверить статус?"
        if any(marker in normalizedQuestion for marker in ("расскажи", "подробнее", "про него")):
            return "Уточните, о каком документе нужно рассказать подробнее?"
        return "Уточните, по какому документу нужен ответ."

    def createClarificationPayload(
        self,
        sessionData: dict,
        originalQuestion: str,
        reason: str,
        questionText: str,
        options: list[dict],
    ) -> dict:
        return {
            "answer": questionText,
            "status": "clarification_required",
            "intent": "clarification",
            "sourceType": "dialog_manager",
            "sessionId": sessionData["id"],
            "citations": [],
            "clarification": {
                "reason": reason,
                "question": questionText,
                "options": options[:5],
                "maxQuestions": 5,
            },
            "usedLLM": False,
            "usedVectorSearch": False,
            "documentsOverview": [],
            "includedDocuments": [],
            "excludedDocuments": [],
            "debug": {
                "pendingClarification": True,
                "clarificationCount": int(sessionData.get("clarificationCount") or 0) + 1,
            },
            "clarificationState": {
                "reason": reason,
                "question": questionText,
                "originalQuestion": originalQuestion,
                "options": options[:5],
            },
        }

    def selectClarificationOption(self, question: str, options: list[dict]) -> dict | None:
        normalizedQuestion = self.intentService.normalizeText(question)
        if normalizedQuestion in self.ordinalMap:
            optionIndex = self.ordinalMap[normalizedQuestion]
            if optionIndex == -1:
                return options[-1] if options else None
            if 0 <= optionIndex < len(options):
                return options[optionIndex]

        for option in options:
            label = self.intentService.normalizeText(option.get("label") or "")
            value = self.intentService.normalizeText(option.get("value") or "")
            if normalizedQuestion == label or normalizedQuestion == value:
                return option
            if normalizedQuestion and (normalizedQuestion in label or normalizedQuestion in value):
                return option
        return None

    def needsDocumentClarification(self, normalizedQuestion: str, intentData: dict) -> bool:
        if intentData["intent"] in {DOCUMENT_METADATA, DOCUMENT_PARTIES, DOCUMENT_SUMMARY, DOCUMENT_FULL_TEXT, DOCUMENT_CONTRACT_DATES}:
            return True
        markers = (
            "сумма",
            "стоимость",
            "срок",
            "дата",
            "истека",
            "заканч",
            "поставщик",
            "вендор",
            "заказчик",
            "исполнитель",
            "реквизит",
            "инн",
            "кпп",
            "огрн",
            "бик",
            "адрес",
            "статус",
            "договор",
            "лиценз",
            "документ",
            "файл",
            "подробнее",
            "про него",
            "расскажи",
        )
        return any(marker in normalizedQuestion for marker in markers)

    def getRelevantDocuments(self, normalizedQuestion: str) -> list[dict]:
        documents = self.documentService.getDocuments()
        if any(marker in normalizedQuestion for marker in ("лиценз",)):
            return [document for document in documents if (document.get("documentType") or "").lower() == "license"] or documents
        if any(marker in normalizedQuestion for marker in ("договор", "контракт")) or self.intentService.isPartyQuestion(normalizedQuestion) or self.intentService.isRequisitesQuestion(normalizedQuestion):
            return [document for document in documents if (document.get("documentType") or "").lower() in {"contract", "agreement", "license"}] or documents
        return documents

    def findMatchingDocuments(self, question: str) -> list[dict]:
        normalizedQuestion = self.intentService.normalizeText(question)
        matches: list[tuple[float, dict]] = []
        for document in self.documentService.getDocuments():
            score = SequenceMatcher(None, normalizedQuestion, self.intentService.normalizeText(document["title"])).ratio()
            if document.get("contractNumber") and str(document["contractNumber"]).lower() in normalizedQuestion:
                score = max(score, 0.95)
            if document.get("softwareName") and self.intentService.normalizeText(str(document["softwareName"])) in normalizedQuestion:
                score = max(score, 0.88)
            if document.get("vendor") and self.intentService.normalizeText(str(document["vendor"])) in normalizedQuestion:
                score = max(score, 0.72)
            if score > 0.45:
                matches.append((score, document))
        matches.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in matches[:5]]

    def shouldClarifyCandidates(self, candidateDocuments: list[dict]) -> bool:
        if len(candidateDocuments) < 2:
            return False
        normalizedTitles = {self.intentService.normalizeText(document["title"]) for document in candidateDocuments}
        return len(normalizedTitles) > 1

    def buildDocumentOptions(self, documents: list[dict]) -> list[dict]:
        options = []
        sortedDocuments = sorted(documents, key=lambda document: self.intentService.normalizeText(document["title"]))
        for document in sortedDocuments[:5]:
            details = []
            if document.get("contractNumber"):
                details.append(f"№ {document['contractNumber']}")
            if document.get("validTo"):
                details.append(str(document["validTo"]))
            if document.get("documentType"):
                details.append(str(document["documentType"]))
            label = document["title"]
            if details:
                label = f"{label} ({', '.join(details)})"
            options.append({
                "label": label,
                "value": document["id"],
                "type": "document",
            })
        return options

    def hasOrdinalReference(self, normalizedQuestion: str) -> bool:
        return any(marker in normalizedQuestion.split() for marker in self.ordinalMap)

    def isContextQuestion(self, normalizedQuestion: str) -> bool:
        return any(marker in normalizedQuestion for marker in ("его", "него", "этот", "данный", "текущий", "по нему"))

    def isTooGeneralQuestion(self, normalizedQuestion: str) -> bool:
        tooGeneralMarkers = {
            "расскажи",
            "что есть",
            "покажи",
            "найди",
            "проверь",
            "статус",
            "актуально",
        }
        return normalizedQuestion in tooGeneralMarkers

    def isGenericDocumentQuestion(self, normalizedQuestion: str) -> bool:
        genericMarkers = (
            "какая сумма",
            "когда истекает",
            "когда заканчивается",
            "какой срок",
            "какой срок действия",
            "покажи реквизиты",
            "кто поставщик",
            "кто заказчик",
            "кто исполнитель",
            "какой статус",
            "что с договором",
            "расскажи про него",
            "а второй подробнее",
        )
        if any(marker in normalizedQuestion for marker in genericMarkers):
            return True
        if self.intentService.isPartyQuestion(normalizedQuestion) or self.intentService.isRequisitesQuestion(normalizedQuestion):
            return True
        return len(normalizedQuestion.split()) <= 4 and any(
            marker in normalizedQuestion
            for marker in ("сумма", "срок", "реквизит", "статус", "договор", "лиценз", "документ")
        )
