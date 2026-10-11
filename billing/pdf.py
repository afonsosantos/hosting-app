import base64
import mimetypes
import re

from django.conf import settings
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils import timezone, translation

from core.models import SiteSettings

from .models import Invoice, Subscription
from .schedule import PlanSummary, payment_schedule


def _logo_data_uri(cfg: SiteSettings) -> str:
    if not cfg.logo:
        return ""
    try:
        with cfg.logo.open("rb") as fh:
            data = base64.b64encode(fh.read()).decode()
    except OSError:
        return ""
    mime = mimetypes.guess_type(cfg.logo.name)[0] or "image/png"
    return f"data:{mime};base64,{data}"


def _html_to_pdf(html: str) -> bytes:
    from weasyprint import HTML  # import tardio: precisa das bibliotecas Pango do sistema

    return HTML(string=html).write_pdf()


def render_invoice_html(invoice: Invoice, *, for_print=False) -> str:
    cfg = SiteSettings.load()
    return render_to_string(
        "billing/invoice_document.html",
        {
            "document": invoice,
            "lines": invoice.lines.all(),
            "company": cfg,
            "logo": _logo_data_uri(cfg),
            "for_print": for_print,
        },
    )


def render_invoice_pdf(invoice: Invoice) -> bytes:
    return _html_to_pdf(render_invoice_html(invoice))


# --- Documentos da subscrição (plano de pagamentos e contrato) -------------------------------


def subscription_reference(subscription: Subscription) -> str:
    return f"Subscrição n.º {subscription.pk}"


def plan_filename(subscription: Subscription) -> str:
    return f"plano_pagamentos_{subscription.pk}.pdf"


def contract_filename(subscription: Subscription) -> str:
    return f"contrato_{subscription.pk}.pdf"


def _subscription_context(subscription: Subscription, cfg: SiteSettings, for_print: bool) -> dict:
    return {
        "subscription": subscription,
        "customer": subscription.customer,
        "items": list(subscription.items.all()),
        "summary": PlanSummary(payment_schedule(subscription)),
        "reference": subscription_reference(subscription),
        "today": timezone.localdate(),
        "company": cfg,
        "logo": _logo_data_uri(cfg),
        "for_print": for_print,
    }


def render_plan_html(subscription: Subscription, *, for_print=False) -> str:
    cfg = SiteSettings.load()
    with translation.override(settings.LANGUAGE_CODE):
        ctx = _subscription_context(subscription, cfg, for_print)
        return render_to_string("billing/plan_document.html", ctx)


def render_plan_pdf(subscription: Subscription) -> bytes:
    return _html_to_pdf(render_plan_html(subscription))


def _terms_blocks(text: str) -> list[tuple[str, str]]:
    """Parágrafos (título, texto): a 1.ª linha é título quando o parágrafo tem mais linhas."""
    blocks = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        head, _, body = paragraph.strip().partition("\n")
        if head:
            blocks.append((head, body) if body else ("", head))
    return blocks


def render_contract_html(subscription: Subscription, *, for_print=False) -> str:
    cfg = SiteSettings.load()
    with translation.override(settings.LANGUAGE_CODE):
        ctx = _subscription_context(subscription, cfg, for_print)
        # as condições gerais são um modelo editável nas Configurações (texto simples)
        terms = Template(cfg.contract_terms).render(Context(ctx, autoescape=False))
        ctx["terms"] = _terms_blocks(terms)
        ctx["has_setup_fees"] = any(item.pending_setup_fee for item in ctx["items"])
        return render_to_string("billing/contract_document.html", ctx)


def render_contract_pdf(subscription: Subscription) -> bytes:
    return _html_to_pdf(render_contract_html(subscription))
