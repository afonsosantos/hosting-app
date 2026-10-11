import logging
from datetime import date

from django.utils import timezone

from core.email import build_email
from core.models import SiteSettings

from .emails import render_email
from .models import Invoice, Notification, Subscription

logger = logging.getLogger(__name__)

Kind = Notification.Kind


def _send(log: Notification, kind: str, context: dict, attachments=()) -> Notification:
    """Envia e regista o resultado. Nunca lança exceção."""
    try:
        email = render_email(kind, **context)
        log.subject = email.subject[:300]
        build_email(
            email.subject, email.text, [log.recipient], html=email.html, attachments=attachments
        ).send()
        log.success = True
    except Exception as exc:  # noqa: BLE001 - qualquer falha fica registada no log
        logger.exception(
            "Falha ao enviar notificação %s (%s)", kind, log.invoice or log.subscription
        )
        log.error = str(exc)
    log.save()
    return log


def send_notification(invoice: Invoice, kind: str, days: int | None = None) -> Notification:
    """Email ao cliente sobre uma cobrança (emissão, lembrete, atraso, pagamento)."""
    log = Notification(
        invoice=invoice, kind=kind, offset_days=days, recipient=invoice.customer.notification_email
    )
    attachments = []
    if kind in (Kind.ISSUE, Kind.REMINDER, Kind.OVERDUE):
        from .pdf import render_invoice_pdf

        try:
            attachments.append(
                (invoice.pdf_filename, render_invoice_pdf(invoice), "application/pdf")
            )
        except Exception as exc:  # noqa: BLE001
            log.error = f"Falha a gerar o PDF: {exc}"
            log.save()
            return log
    context = {"customer": invoice.customer, "document": invoice, "days": days}
    _send(log, kind, context, attachments)
    if log.success and kind == Kind.ISSUE and invoice.email_pending:
        invoice.email_pending = False
        invoice.save(update_fields=["email_pending"])
    return log


def _send_subscription_email(subscription: Subscription, kind: str) -> Notification:
    from . import pdf

    customer = subscription.customer
    log = Notification(subscription=subscription, kind=kind, recipient=customer.notification_email)
    try:
        attachments = []
        if kind == Kind.CONTRACT:
            attachments.append(
                (
                    pdf.contract_filename(subscription),
                    pdf.render_contract_pdf(subscription),
                    "application/pdf",
                )
            )
        attachments.append(
            (pdf.plan_filename(subscription), pdf.render_plan_pdf(subscription), "application/pdf")
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Falha a gerar o PDF da subscrição %s", subscription.pk)
        log.error = f"Falha a gerar o PDF: {exc}"
        log.save()
        return log
    return _send(log, kind, {"customer": customer, "subscription": subscription}, attachments)


def send_subscription_plan(subscription: Subscription) -> Notification:
    """Email ao cliente com os produtos da subscrição e o plano de pagamentos (em PDF)."""
    return _send_subscription_email(subscription, Kind.SUBSCRIPTION)


def send_contract(subscription: Subscription) -> Notification:
    """Email ao cliente com o contrato e o plano de pagamentos (em PDF)."""
    return _send_subscription_email(subscription, Kind.CONTRACT)


def _already_sent(invoice: Invoice, kind: str, days: int) -> bool:
    return invoice.notifications.filter(kind=kind, offset_days=days, success=True).exists()


def due_reminder(invoice: Invoice, today: date, cfg: SiteSettings) -> int | None:
    """Limiar de lembrete a enviar hoje (ou None).

    Usa o limiar mais recente já atingido, para não perder lembretes se o agendador
    falhar um dia, e ignora limiares que já tinham passado quando o documento foi emitido.
    """
    days_left = (invoice.due_date - today).days
    if days_left < 0:
        return None
    lead_at_issue = (invoice.due_date - invoice.issue_date).days
    crossed = [d for d in cfg.reminder_day_list if days_left <= d < lead_at_issue]
    if not crossed:
        return None
    threshold = min(crossed)
    return None if _already_sent(invoice, Kind.REMINDER, threshold) else threshold


def due_overdue_notice(invoice: Invoice, today: date, cfg: SiteSettings) -> int | None:
    days_late = (today - invoice.due_date).days
    if days_late <= 0:
        return None
    crossed = [d for d in cfg.overdue_day_list if 0 < d <= days_late]
    if not crossed:
        return None
    threshold = max(crossed)
    return None if _already_sent(invoice, Kind.OVERDUE, threshold) else threshold


def run_notifications(today: date | None = None) -> list[Notification]:
    today = today or timezone.localdate()
    cfg = SiteSettings.load()
    if not cfg.notifications_enabled:
        return []
    sent = []
    pending = Invoice.objects.filter(
        status=Invoice.Status.PENDING, customer__notify=True
    ).select_related("customer")
    for invoice in pending:
        if invoice.email_pending:
            sent.append(send_notification(invoice, Kind.ISSUE))
            continue
        if (threshold := due_reminder(invoice, today, cfg)) is not None:
            days_left = (invoice.due_date - today).days
            log = send_notification(invoice, Kind.REMINDER, days_left)
            # o registo guarda o limiar (para não repetir), a mensagem usa os dias reais
            log.offset_days = threshold
            log.save(update_fields=["offset_days"])
            sent.append(log)
        elif (threshold := due_overdue_notice(invoice, today, cfg)) is not None:
            days_late = (today - invoice.due_date).days
            log = send_notification(invoice, Kind.OVERDUE, days_late)
            log.offset_days = threshold
            log.save(update_fields=["offset_days"])
            sent.append(log)
    return sent
