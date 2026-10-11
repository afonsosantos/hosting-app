"""Composição dos emails aos clientes: mensagem editável + layout (HTML e texto simples)."""

from dataclasses import dataclass

from django.conf import settings
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils import translation

from core.models import SiteSettings

from .models import Invoice, Notification, Subscription
from .schedule import euros, payment_schedule, schedule_as_text

Kind = Notification.Kind


@dataclass
class RenderedEmail:
    subject: str
    text: str
    html: str


def _headline(kind: str, document: Invoice | None, days: int | None) -> tuple[str, str]:
    """(texto, tom) do destaque no resumo do documento."""
    if document is None:
        return "", "neutral"
    due = document.due_date.strftime("%d/%m/%Y")
    if kind == Kind.PAID:
        paid = document.paid_on.strftime("%d/%m/%Y") if document.paid_on else ""
        return (f"Pago em {paid}" if paid else "Pago"), "success"
    if kind == Kind.OVERDUE:
        return f"Em atraso há {days} dia{'s' if days != 1 else ''} (venceu a {due})", "danger"
    if kind == Kind.REMINDER:
        when = "hoje" if days == 0 else f"daqui a {days} dia{'s' if days != 1 else ''}"
        return f"Vence {when} ({due})", "warning"
    return f"Vence a {due}", "neutral"


def _text_version(ctx: dict) -> str:
    cfg: SiteSettings = ctx["company"]
    document: Invoice | None = ctx["document"]
    parts = [ctx["message"].strip()]

    if document is not None:
        block = [f"{cfg.document_title} {document.number}", f"Valor: {euros(document.total)}"]
        if ctx["headline"]:
            block.append(ctx["headline"])
        if document.period_start:
            block.append(
                f"Período: {document.period_start:%d/%m/%Y} a {document.period_end:%d/%m/%Y}"
            )
        parts.append("\n".join(block))
        if ctx["show_lines"]:
            parts.append(
                "\n".join(f"  - {line.description}: {euros(line.total)}" for line in ctx["lines"])
            )

    if ctx["subscription"] is not None:
        parts.append(
            "Serviços:\n"
            + "\n".join(
                f"  - {item.label}: {euros(item.total)} ({item.get_cycle_display().lower()})"
                for item in ctx["items"]
            )
        )
        if ctx["plan"]:
            parts.append("Plano de pagamentos:\n" + schedule_as_text(ctx["plan"]))

    if ctx["show_payment"]:
        block = ["Pagamento por transferência bancária:"]
        block += [f"  {label}: {value}" for label, value in cfg.bank_details]
        if document is not None:
            block += [f"  Montante: {euros(document.total)}", f"  Descritivo: {document.number}"]
        if cfg.payment_instructions:
            block += ["", cfg.payment_instructions]
        parts.append("\n".join(block))

    signature = [cfg.company_name]
    signature += [v for v in (cfg.company_email, cfg.company_phone, cfg.company_website) if v]
    parts.append("—\n" + "\n".join(signature))
    return "\n\n".join(parts) + "\n"


def render_email(
    kind: str,
    *,
    customer,
    document: Invoice | None = None,
    subscription: Subscription | None = None,
    days: int | None = None,
) -> RenderedEmail:
    cfg = SiteSettings.load()
    # emails sempre em português (datas e valores), mesmo quando enviados a partir do admin
    with translation.override(settings.LANGUAGE_CODE):
        plan = payment_schedule(subscription) if subscription is not None else []
        user_ctx = Context(
            {
                "company": cfg,
                "customer": customer,
                "document": document,
                "subscription": subscription,
                "days": days,
                # compatibilidade com modelos personalizados antigos
                "payment_instructions": cfg.payment_instructions_text(),
                "payment_plan": schedule_as_text(plan),
            },
            autoescape=False,
        )
        subject = " ".join(Template(getattr(cfg, f"{kind}_subject")).render(user_ctx).split())
        message = Template(getattr(cfg, f"{kind}_body")).render(user_ctx)

        headline, tone = _headline(kind, document, days)
        ctx = {
            "kind": kind,
            "subject": subject,
            "message": message,
            "company": cfg,
            "customer": customer,
            "document": document,
            "lines": list(document.lines.all()) if document is not None else [],
            "show_lines": document is not None and kind == Kind.ISSUE,
            "headline": headline,
            "tone": tone,
            "subscription": subscription,
            "items": list(subscription.items.all()) if subscription is not None else [],
            "plan": plan,
            "show_payment": kind != Kind.PAID
            and bool(cfg.bank_details or cfg.payment_instructions),
            "has_attachment": kind in (Kind.ISSUE, Kind.REMINDER, Kind.OVERDUE),
        }
        html = render_to_string("billing/emails/notification.html", ctx)
        text = _text_version(ctx)
    return RenderedEmail(subject=subject, text=text, html=html)
