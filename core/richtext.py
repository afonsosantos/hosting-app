"""Texto formatado (editor Trix no admin), guardado como HTML limpo."""

import html

import nh3
from django import forms

# o que o editor Trix produz, e nada mais (sem scripts, estilos ou imagens)
ALLOWED_TAGS = {
    "div", "p", "br", "strong", "b", "em", "i", "u", "del", "s",
    "h1", "h2", "h3", "blockquote", "pre", "ul", "ol", "li", "a",
}  # fmt: skip


def clean_html(value: str) -> str:
    return nh3.clean(value or "", tags=ALLOWED_TAGS, attributes={"a": {"href"}})


def text_to_html(text: str) -> str:
    """Texto simples -> HTML do editor; a 1.ª linha de cada parágrafo com várias linhas fica a negrito."""
    paragraphs = []
    for paragraph in text.strip().split("\n\n"):
        lines = [html.escape(line, quote=False) for line in paragraph.strip().splitlines()]
        if len(lines) > 1:
            lines[0] = f"<strong>{lines[0]}</strong>"
        paragraphs.append("<br>".join(lines))
    return f"<div>{'<br><br>'.join(paragraphs)}</div>"


class RichTextWidget(forms.HiddenInput):
    """Campo escondido com o HTML, editado pelo <trix-editor> ligado a ele."""

    template_name = "core/widgets/richtext.html"
    is_hidden = False  # senão o admin esconde a linha do campo (label incluída)

    class Media:
        css = {"all": ["core/trix/trix.css", "core/richtext.css"]}
        js = ["core/trix/trix.umd.min.js", "core/richtext.js"]
