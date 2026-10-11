import logging
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.models import SiteSettings

from .models import Invoice, InvoiceLine, Subscription

logger = logging.getLogger(__name__)

# Proteção contra subscrições com datas muito antigas: no máximo N períodos por execução.
MAX_PERIODS_PER_RUN = 12


def assign_number(invoice: Invoice) -> None:
    """Atribui série/ano/número sequencial. Tem de correr dentro de uma transação."""
    cfg = SiteSettings.objects.select_for_update().get(pk=SiteSettings.load().pk)
    invoice.series = invoice.series or cfg.document_prefix
    invoice.year = invoice.issue_date.year
    last = Invoice.objects.filter(series=invoice.series, year=invoice.year).aggregate(
        n=Max("sequence")
    )["n"]
    invoice.sequence = (last or 0) + 1


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


@transaction.atomic
def create_invoice_for_subscription(
    subscription: Subscription,
    *,
    horizon: date | None = None,
    send_email: bool | None = None,
) -> Invoice | None:
    """Gera uma cobrança com um período de cada produto que vence até `horizon`.

    Produtos com periodicidades diferentes (ex. alojamento mensal + domínio anual) que vençam
    dentro da mesma janela ficam no mesmo documento; o vencimento do documento é o mais próximo.
    Sem `horizon` (geração manual), usa a janela de antecedência a contar do próximo vencimento.
    Avança o próximo vencimento de cada produto incluído.
    """
    sub = (
        Subscription.objects.select_for_update().select_related("customer").get(pk=subscription.pk)
    )
    items = [
        item
        for item in sub.items.select_related("product").prefetch_related("product__price_plans")
        if sub.is_billable(item.next_due_date)
    ]
    if not items:
        return None

    cfg = SiteSettings.load()
    if horizon is None:
        earliest = min(item.next_due_date for item in items)
        horizon = max(earliest, timezone.localdate()) + timedelta(days=cfg.days_before_due)
    items = [item for item in items if item.next_due_date <= horizon]
    if not items:
        return None
    if send_email is None:
        send_email = cfg.notifications_enabled and cfg.notify_on_issue and sub.customer.notify

    periods = {item.pk: item.period_for(item.next_due_date) for item in items}
    distinct_periods = set(periods.values())
    single_period = distinct_periods.pop() if len(distinct_periods) == 1 else (None, None)

    invoice = Invoice.objects.create(
        customer=sub.customer,
        subscription=sub,
        issue_date=timezone.localdate(),
        due_date=min(item.next_due_date for item in items),
        period_start=single_period[0],
        period_end=single_period[1],
        email_pending=send_email,
    )
    for item in items:
        start, end = periods[item.pk]
        InvoiceLine.objects.create(
            invoice=invoice,
            subscription_item=item,
            description=f"{item.label} ({item.get_cycle_display().lower()}, "
            f"{_fmt(start)} a {_fmt(end)})",
            quantity=item.quantity,
            unit_price=item.unit_price or 0,
        )
        setup_fee = item.pending_setup_fee
        if setup_fee:
            InvoiceLine.objects.create(
                invoice=invoice,
                subscription_item=item,
                description=f"Taxa de ativação — {item.label}",
                quantity=1,
                unit_price=setup_fee,
            )
        item.setup_fee_charged = True
        item.next_due_date = end + timedelta(days=1)
        item.save(update_fields=["setup_fee_charged", "next_due_date"])

    invoice.recalculate_total()
    logger.info("Gerada %s para subscrição %s (%s €).", invoice.number, sub.pk, invoice.total)
    return invoice


def generate_due_invoices(today: date | None = None) -> list[Invoice]:
    """Gera cobranças para todos os produtos de subscrições ativas que vencem dentro da antecedência."""
    today = today or timezone.localdate()
    cfg = SiteSettings.load()
    horizon = today + timedelta(days=cfg.days_before_due)
    created = []
    subs = Subscription.objects.filter(
        status=Subscription.Status.ACTIVE, items__next_due_date__lte=horizon
    ).distinct()
    for sub in subs:
        # vários documentos só quando há períodos em atraso por recuperar
        for _ in range(MAX_PERIODS_PER_RUN):
            invoice = create_invoice_for_subscription(sub, horizon=horizon)
            if invoice is None:
                break
            created.append(invoice)
    return created


def mark_paid(invoice: Invoice, *, paid_on: date | None = None, reference: str = "") -> bool:
    """Marca como paga e envia confirmação (se configurado). Devolve False se já estava paga."""
    if invoice.status == Invoice.Status.PAID:
        return False
    invoice.status = Invoice.Status.PAID
    invoice.paid_on = paid_on or timezone.localdate()
    if reference:
        invoice.payment_reference = reference
    invoice.email_pending = False
    invoice.save(update_fields=["status", "paid_on", "payment_reference", "email_pending"])
    notify_paid(invoice)
    return True


def notify_paid(invoice: Invoice) -> None:
    """Envia a confirmação de pagamento ao cliente, se configurado."""
    cfg = SiteSettings.load()
    if cfg.notifications_enabled and cfg.notify_on_payment and invoice.customer.notify:
        from .notifications import send_notification

        send_notification(invoice, "paid")


def subscription_created(subscription: Subscription) -> bool:
    """Envia o plano de pagamentos ao cliente, se configurado. Devolve True se enviou."""
    cfg = SiteSettings.load()
    if not (
        cfg.notifications_enabled
        and cfg.notify_on_subscription
        and subscription.customer.notify
        and subscription.items.exists()
    ):
        return False
    from .notifications import send_subscription_plan

    return send_subscription_plan(subscription).success
