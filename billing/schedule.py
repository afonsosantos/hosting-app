"""Previsão das próximas cobranças de uma subscrição (o “plano de pagamentos”).

Segue as mesmas regras da geração automática: cada documento junta um período de cada produto
que vence dentro da janela de antecedência a contar do vencimento mais próximo.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.utils.formats import number_format

from core.dates import add_months
from core.models import SiteSettings

from .models import Subscription, SubscriptionItem


@dataclass
class PlannedLine:
    item: SubscriptionItem
    description: str
    start: date | None
    end: date | None
    amount: Decimal
    is_setup_fee: bool = False

    @property
    def label(self) -> str:
        return self.item.label

    @property
    def cycle(self) -> str:
        return "" if self.is_setup_fee else self.item.get_cycle_display()


@dataclass
class PlannedCharge:
    due_date: date
    lines: list[PlannedLine] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0"))


@dataclass
class PlanSummary:
    charges: list[PlannedCharge]

    @property
    def next(self) -> PlannedCharge | None:
        return self.charges[0] if self.charges else None

    @property
    def total(self) -> Decimal:
        return sum((c.total for c in self.charges), Decimal("0"))

    @property
    def monthly_average(self) -> Decimal:
        return (self.total / 12).quantize(Decimal("0.01"))


def payment_schedule(subscription: Subscription, months: int = 12) -> list[PlannedCharge]:
    items = [i for i in subscription.items.all() if i.cycle and i.next_due_date]
    if not items or subscription.status != Subscription.Status.ACTIVE:
        return []
    window = timedelta(days=SiteSettings.load().days_before_due)
    next_due = {item.pk: item.next_due_date for item in items}
    setup_pending = {item.pk: item.pending_setup_fee for item in items}
    limit = add_months(min(next_due.values()), months)

    charges = []
    while True:
        billable = [
            item
            for item in items
            if next_due[item.pk] < limit and subscription.is_billable(next_due[item.pk])
        ]
        if not billable:
            break
        due = min(next_due[item.pk] for item in billable)
        charge = PlannedCharge(due_date=due)
        for item in billable:
            if next_due[item.pk] > due + window:
                continue
            start, end = item.period_for(next_due[item.pk])
            charge.lines.append(
                PlannedLine(
                    item,
                    f"{item.label} ({item.get_cycle_display().lower()})",
                    start,
                    end,
                    item.total,
                )
            )
            if setup_pending[item.pk]:
                charge.lines.append(
                    PlannedLine(
                        item,
                        f"Taxa de ativação — {item.label}",
                        None,
                        None,
                        setup_pending[item.pk],
                        is_setup_fee=True,
                    )
                )
                setup_pending[item.pk] = Decimal("0")
            next_due[item.pk] = end + timedelta(days=1)
        charges.append(charge)
    return charges


def schedule_as_text(charges: list[PlannedCharge]) -> str:
    """Versão em texto simples, para os emails."""
    out = []
    for charge in charges:
        out.append(f"{charge.due_date:%d/%m/%Y} — {euros(charge.total)}")
        for line in charge.lines:
            period = f" {line.start:%d/%m/%Y} a {line.end:%d/%m/%Y}" if line.start else ""
            out.append(f"    {line.description}{period}: {euros(line.amount)}")
    return "\n".join(out)


def euros(value) -> str:
    """Valor formatado no idioma ativo (pt: 1234,50 €)."""
    return f"{number_format(value, 2)} €"
