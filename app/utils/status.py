from datetime import date, datetime, timedelta


def calculateBusinessStatus(validTo: object, days: int = 60) -> str:
    if not validTo:
        return "no_date"
    parsed = parseDateValue(validTo)
    if not parsed:
        return "no_date"
    today = date.today()
    if parsed < today:
        return "expired"
    if parsed <= today + timedelta(days=days):
        return "expiring"
    return "active"


def parseDateValue(value: object) -> date | None:
    if value in {None, "", "-", "None", "null", "undefined"}:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for parser in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:19], parser).date()
        except ValueError:
            continue
    return None

