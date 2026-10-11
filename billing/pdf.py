import base64
import mimetypes

from django.template.loader import render_to_string

from core.models import SiteSettings

from .models import Invoice


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
    from weasyprint import HTML  # import tardio: precisa das bibliotecas Pango do sistema

    return HTML(string=render_invoice_html(invoice)).write_pdf()
