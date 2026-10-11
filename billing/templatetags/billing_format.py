from django import template

from billing.schedule import euros as format_euros

register = template.Library()


@register.filter
def euros(value):
    """1234.5 -> “1234,50 €” (formato do idioma ativo)."""
    if value in (None, ""):
        return ""
    return format_euros(value)
