from datetime import date, datetime
from decimal import Decimal, InvalidOperation


DOCUMENT_TYPE_LABELS = {
    "contract": "Договор",
    "license": "Лицензия",
    "agreement": "Соглашение",
    "act": "Акт",
    "appendix": "Приложение",
    "invoice": "Счет",
    "scan": "Скан",
    "other": "Документ",
    "document": "Документ",
}

STATUS_LABELS = {
    "active": "Активен",
    "expiring": "Истекает скоро",
    "expired": "Просрочено",
    "no_date": "Срок не указан",
    "processing": "Обработка",
    "failed": "Ошибка",
    "indexed": "Индексирован",
}


def formatAmount(value: object, currency: str | None = "RUB", emptyText: str = "сумма не указана") -> str:
    if value in {None, "", "-", "None", "null", "undefined"}:
        return emptyText
    try:
        amount = Decimal(str(value).replace(" ", "").replace("₽", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return emptyText
    if amount.is_nan() or amount <= 0:
        return emptyText

    normalized = amount.quantize(Decimal("0.01")) if amount != amount.to_integral() else amount.quantize(Decimal("1"))
    if normalized == normalized.to_integral():
        amountText = f"{int(normalized):,}".replace(",", " ")
    else:
        amountText = f"{normalized:,.2f}".replace(",", " ").replace(".", ",")

    currencyMap = {
        "RUB": "₽",
        "RUR": "₽",
        "₽": "₽",
        "USD": "$",
        "EUR": "€",
    }
    symbol = currencyMap.get(str(currency or "RUB").upper(), str(currency or "RUB"))
    return f"{amountText} {symbol}"


def formatDate(value: object, emptyText: str = "срок не указан") -> str:
    if value in {None, "", "-", "None", "null", "undefined"}:
        return emptyText
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    text = str(value).strip()
    if not text:
        return emptyText
    for parser in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(text[:19], parser)
            return parsed.strftime("%d.%m.%Y")
        except ValueError:
            continue
    return emptyText


def formatDocumentType(value: str | None) -> str:
    if not value:
        return "Документ"
    return DOCUMENT_TYPE_LABELS.get(str(value).lower(), "Документ")


def formatVendor(value: object, emptyText: str = "не указан") -> str:
    if value in {None, "", "-", "None", "null", "undefined"}:
        return emptyText
    return str(value).strip() or emptyText


def formatStatus(value: str | None) -> str:
    if not value:
        return STATUS_LABELS["no_date"]
    return STATUS_LABELS.get(value, value)

