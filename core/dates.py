import calendar
from datetime import date


def add_months(d: date, months: int) -> date:
    """Soma meses a uma data, ajustando ao último dia do mês quando necessário (31/01 + 1 = 28/02)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def parse_day_list(value: str) -> list[int]:
    """'7, 3,1' -> [7, 3, 1]. Ignora entradas inválidas e duplicadas."""
    days = []
    for part in (value or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) not in days:
            days.append(int(part))
    return days
