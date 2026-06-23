import re


DOCUMENT_COLLECTION_SUMMARY = "DOCUMENT_COLLECTION_SUMMARY"
DOCUMENT_AMOUNT_TOTAL = "DOCUMENT_AMOUNT_TOTAL"
DOCUMENT_COUNT = "DOCUMENT_COUNT"
DOCUMENT_COUNT_INDEXED = "DOCUMENT_COUNT_INDEXED"
DOCUMENT_STATUS_ACTIVE = "DOCUMENT_STATUS_ACTIVE"
DOCUMENT_STATUS_EXPIRING = "DOCUMENT_STATUS_EXPIRING"
DOCUMENT_STATUS_EXPIRED = "DOCUMENT_STATUS_EXPIRED"
DOCUMENT_STATUS_NO_DATE = "DOCUMENT_STATUS_NO_DATE"
DOCUMENT_STATUS_PROCESSING = "DOCUMENT_STATUS_PROCESSING"
DOCUMENT_STATUS_FAILED = "DOCUMENT_STATUS_FAILED"
DOCUMENT_VENDOR_FILTER = "DOCUMENT_VENDOR_FILTER"
DOCUMENT_SUMMARY = "DOCUMENT_SUMMARY"
DOCUMENT_FULL_TEXT = "DOCUMENT_FULL_TEXT"
DOCUMENT_METADATA = "DOCUMENT_METADATA"
DOCUMENT_PARTIES = "DOCUMENT_PARTIES"
DOCUMENT_CONTRACT_DATES = "DOCUMENT_CONTRACT_DATES"
GENERAL_RAG = "GENERAL_RAG"


class IntentService:
    collectionMarkers = (
        "все документы", "эти документы", "все файлы", "документы в базе", "загруженные документы",
        "каждый файл", "каждый документ", "каждого файла", "каждого документа", "по отдельности",
        "каждый фаил", "в каждом файле", "в каждом документе", "про каждый загруженный файл",
        "покажи все договоры", "покажи все лицензии", "покажи все документы", "покажи все файлы",
        "все договоры", "все лицензии", "все загруженные договоры", "все загруженные лицензии",
    )

    amountMarkers = (
        "общая сумма", "сумма всех договоров", "сумма всех лицензий", "общая стоимость документов",
        "общую сумму", "сумму всех договоров", "сумму всех лицензий", "общую стоимость документов",
        "посчитай сумму", "посчитай общую сумму", "сумма договоров и лицензий", "общий бюджет по документам",
    )

    briefMarkers = ("кратко", "в двух словах", "коротко", "одним предложением", "суть", "summary")
    fullTextMarkers = ("весь текст", "полный текст", "напиши весь текст", "покажи весь текст")
    followupMarkers = ("этот", "данный", "текущий", "он", "его", "по нему", "у него", "этого")
    customerMarkers = (
        "заказчик",
        "кто является заказчиком",
        "назови заказчика",
        "заказчик по договору",
        "сторона заказчика",
        "какая организация является заказчиком",
        "кто клиент",
        "кто покупатель",
        "клиент",
        "покупател",
    )
    executorMarkers = (
        "исполнитель",
        "кто является исполнителем",
        "поставщик",
        "кто является поставщиком",
        "продавец",
        "лицензиар",
    )
    partiesMarkers = (
        "стороны договора",
        "кто стороны",
        "кто заключил договор",
        "покажи стороны",
        "реквизиты сторон",
        "заказчик и исполнитель",
        "заказчик и поставщик",
        "контрагент",
    )
    requisitesMarkers = (
        "реквизит",
        "инн",
        "кпп",
        "огрн",
        "бик",
        "адрес",
        "почтов",
        "расчетн",
        "корреспондентск",
        "банк",
        "эл почта",
        "email",
        "телефон",
    )
    contractDateMarkers = (
        "когда истекает этот договор",
        "когда заканчивается договор",
        "срок действия договора",
        "до какой даты действует договор",
        "когда окончание договора",
        "когда истекает",
        "какой срок",
    )

    def detectIntent(self, question: str) -> dict:
        normalizedQuestion = self.normalizeText(question)
        isCollection = any(marker in normalizedQuestion for marker in self.collectionMarkers)
        isAmountTotal = any(marker in normalizedQuestion for marker in self.amountMarkers)
        if isAmountTotal:
            return {
                "intent": DOCUMENT_AMOUNT_TOTAL,
                "isCollection": False,
                "isFollowup": False,
                "disableLlm": True,
                "disableVectorSearch": True,
                "language": "ru",
            }
        if isCollection:
            return {
                "intent": DOCUMENT_COLLECTION_SUMMARY,
                "isCollection": True,
                "perDocument": True,
                "isFollowup": False,
                "disableLlm": True,
                "disableVectorSearch": True,
                "language": "ru",
            }
        if "сколько документов индекс" in normalizedQuestion:
            return {"intent": DOCUMENT_COUNT_INDEXED, "isFollowup": False}
        if "сколько документов" in normalizedQuestion or "сколько файлов" in normalizedQuestion:
            return {"intent": DOCUMENT_COUNT, "isFollowup": False}
        if "по поставщику" in normalizedQuestion:
            return {"intent": DOCUMENT_VENDOR_FILTER, "isFollowup": False}
        if any(marker in normalizedQuestion for marker in ("активные документы", "действующие документы", "что действует сейчас")):
            return {"intent": DOCUMENT_STATUS_ACTIVE, "isFollowup": False}
        if any(marker in normalizedQuestion for marker in ("истекает скоро", "заканчивается скоро", "истекающие документы", "в ближайшие 60 дней", "в течение месяца", "скоро истекают")):
            return {"intent": DOCUMENT_STATUS_EXPIRING, "isFollowup": False}
        if any(marker in normalizedQuestion for marker in ("просроченные документы", "что уже истекло", "документы с истекшим сроком", "просрочено")):
            return {"intent": DOCUMENT_STATUS_EXPIRED, "isFollowup": False}
        if any(marker in normalizedQuestion for marker in ("без срока", "не указан срок", "без даты окончания")):
            return {"intent": DOCUMENT_STATUS_NO_DATE, "isFollowup": False}
        if any(marker in normalizedQuestion for marker in ("в обработке", "обрабатывается", "processing")):
            return {"intent": DOCUMENT_STATUS_PROCESSING, "isFollowup": False}
        if any(marker in normalizedQuestion for marker in ("с ошибкой", "ошибка обработки", "failed")):
            return {"intent": DOCUMENT_STATUS_FAILED, "isFollowup": False}
        if any(marker in normalizedQuestion for marker in self.fullTextMarkers):
            return {"intent": DOCUMENT_FULL_TEXT, "isFollowup": self.isFollowupReference(normalizedQuestion)}
        if self.isContractDatesQuestion(normalizedQuestion):
            return {"intent": DOCUMENT_CONTRACT_DATES, "isFollowup": self.isFollowupReference(normalizedQuestion)}
        if self.isPartyQuestion(normalizedQuestion):
            return {"intent": DOCUMENT_PARTIES, "isFollowup": self.isFollowupReference(normalizedQuestion)}
        if self.isMetadataQuestion(normalizedQuestion):
            return {"intent": DOCUMENT_METADATA, "isFollowup": self.isFollowupReference(normalizedQuestion)}
        if self.isSummaryQuestion(normalizedQuestion):
            return {"intent": DOCUMENT_SUMMARY, "isFollowup": self.isFollowupReference(normalizedQuestion)}
        return {"intent": GENERAL_RAG, "isFollowup": self.isFollowupReference(normalizedQuestion)}

    def normalizeText(self, text: str) -> str:
        normalizedText = text.lower().replace("ё", "е")
        normalizedText = re.sub(r"(?<!\d)[^\w\s.]+|[^\w\s.]+(?!\d)", " ", normalizedText)
        normalizedText = re.sub(r"\s+", " ", normalizedText)
        return normalizedText.strip()

    def isBriefRequest(self, question: str) -> bool:
        normalizedQuestion = self.normalizeText(question)
        return any(marker in normalizedQuestion for marker in self.briefMarkers)

    def isFullTextRequest(self, question: str) -> bool:
        normalizedQuestion = self.normalizeText(question)
        return any(marker in normalizedQuestion for marker in self.fullTextMarkers)

    def isFollowupReference(self, normalizedQuestion: str) -> bool:
        if any(marker in normalizedQuestion for marker in self.collectionMarkers):
            return False
        return any(marker in normalizedQuestion for marker in self.followupMarkers) or len(normalizedQuestion.split()) <= 4

    def isSummaryQuestion(self, normalizedQuestion: str) -> bool:
        markers = ("о чем документ", "расскажи про документ", "расскажи про файл", "что в документе", "что в файле")
        return any(marker in normalizedQuestion for marker in markers) or self.isBriefRequest(normalizedQuestion)

    def isMetadataQuestion(self, normalizedQuestion: str) -> bool:
        markers = (
            "сумма",
            "стоимость",
            "срок",
            "дата",
            "поставщик",
            "вендор",
            "лиценз",
            "программный продукт",
            *self.requisitesMarkers,
        )
        return any(marker in normalizedQuestion for marker in markers)

    def isPartyQuestion(self, normalizedQuestion: str) -> bool:
        return (
            self.isCustomerQuestion(normalizedQuestion)
            or self.isExecutorQuestion(normalizedQuestion)
            or self.isPartiesQuestion(normalizedQuestion)
        )

    def isCustomerQuestion(self, normalizedQuestion: str) -> bool:
        return any(marker in normalizedQuestion for marker in self.customerMarkers)

    def isExecutorQuestion(self, normalizedQuestion: str) -> bool:
        return any(marker in normalizedQuestion for marker in self.executorMarkers)

    def isPartiesQuestion(self, normalizedQuestion: str) -> bool:
        return any(marker in normalizedQuestion for marker in self.partiesMarkers)

    def isRequisitesQuestion(self, normalizedQuestion: str) -> bool:
        return any(marker in normalizedQuestion for marker in self.requisitesMarkers)

    def isContractDatesQuestion(self, normalizedQuestion: str) -> bool:
        return any(marker in normalizedQuestion for marker in self.contractDateMarkers)
