from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from .dates import parse_day_list
from .richtext import clean_html, text_to_html

# Os textos têm só a mensagem: o resumo do documento, a tabela de produtos/plano,
# os dados de pagamento e a assinatura são acrescentados pelo layout do email.

DEFAULT_ISSUE_SUBJECT = "{{ document.number }} — {{ company.company_name }}"
DEFAULT_ISSUE_BODY = """Olá {{ customer.name }},

Enviamos em anexo a {{ company.document_title|lower }} {{ document.number }}, com vencimento a {{ document.due_date|date:"d/m/Y" }}. Encontra abaixo o resumo e os dados para pagamento.

Obrigado pela preferência!"""

DEFAULT_REMINDER_SUBJECT = (
    "Lembrete: {{ document.number }} vence a {{ document.due_date|date:'d/m/Y' }}"
)
DEFAULT_REMINDER_BODY = """Olá {{ customer.name }},

Lembramos que a {{ company.document_title|lower }} {{ document.number }} vence {% if days == 0 %}hoje{% else %}daqui a {{ days }} dia{{ days|pluralize }}{% endif %}. Enviamos novamente o documento em anexo.

Se já efetuou o pagamento, por favor ignore esta mensagem."""

DEFAULT_OVERDUE_SUBJECT = "Pagamento em atraso: {{ document.number }}"
DEFAULT_OVERDUE_BODY = """Olá {{ customer.name }},

A {{ company.document_title|lower }} {{ document.number }} venceu a {{ document.due_date|date:"d/m/Y" }} e ainda não registámos o pagamento.

Se já efetuou o pagamento, por favor responda a este email com o comprovativo."""

DEFAULT_PAID_SUBJECT = "Pagamento recebido: {{ document.number }}"
DEFAULT_PAID_BODY = """Olá {{ customer.name }},

Confirmamos a receção do pagamento referente à {{ company.document_title|lower }} {{ document.number }}. Obrigado!"""

DEFAULT_SUBSCRIPTION_SUBJECT = (
    "Plano de pagamentos — {{ subscription.name|default:company.company_name }}"
)
DEFAULT_SUBSCRIPTION_BODY = """Olá {{ customer.name }},

Obrigado pela confiança! Abaixo encontra os serviços da sua subscrição{% if subscription.name %} “{{ subscription.name }}”{% endif %} e o plano de pagamentos para os próximos 12 meses.

Antes de cada vencimento receberá a respetiva {{ company.document_title|lower }} por email."""

DEFAULT_CONTRACT_SUBJECT = (
    "{{ company.contract_title }} — {{ subscription.name|default:company.company_name }}"
)
DEFAULT_CONTRACT_BODY = """Olá {{ customer.name }},

Enviamos em anexo o contrato dos serviços da sua subscrição{% if subscription.name %} “{{ subscription.name }}”{% endif %} e o respetivo plano de pagamentos. Abaixo encontra o resumo dos serviços e das próximas cobranças.

Se concordar, agradecemos que nos devolva uma cópia assinada. Para qualquer questão, basta responder a este email."""

DEFAULT_CONTRACT_TERMS_TEXT = """1. Prestação dos serviços
{{ company.company_name }} compromete-se a prestar os serviços contratados com diligência e a informar o cliente, com a antecedência possível, de interrupções programadas.

2. Obrigações do cliente
O cliente compromete-se a pagar cada cobrança até à data de vencimento e a utilizar os serviços de acordo com a lei, sendo o único responsável pelos conteúdos que aloja.

3. Falta de pagamento
A falta de pagamento por mais de 15 dias após o vencimento pode levar à suspensão dos serviços até à regularização.

4. Renovação e cancelamento
Salvo data de fim indicada, o contrato renova-se automaticamente no fim de cada período. Qualquer das partes pode cancelá-lo por escrito, incluindo por email, com 30 dias de antecedência em relação ao fim do período em curso. Os períodos já iniciados não são reembolsáveis.

5. Alteração de preços
Alterações de preço são comunicadas com pelo menos 30 dias de antecedência e aplicam-se a partir do período seguinte.

6. Proteção de dados
Os dados pessoais do cliente são tratados apenas para a gestão deste contrato e da faturação, nos termos do RGPD.

7. Disposições finais
Em tudo o que não estiver previsto neste contrato aplica-se a lei portuguesa."""
DEFAULT_CONTRACT_TERMS = text_to_html(DEFAULT_CONTRACT_TERMS_TEXT)

DEFAULT_PAYMENT_INSTRUCTIONS = (
    "Indique o número do documento no descritivo da transferência "
    "e envie o comprovativo para este email."
)


def validate_day_list(value):
    for part in (value or "").split(","):
        if part.strip() and not part.strip().isdigit():
            raise ValidationError("Use números inteiros separados por vírgulas, ex.: 7,3,1")


def validate_template(value):
    from django.template import Template, TemplateSyntaxError

    try:
        Template(value)
    except TemplateSyntaxError as exc:
        raise ValidationError(f"Erro no modelo: {exc}") from exc


class SiteSettings(models.Model):
    """Configuração global (singleton: existe sempre e apenas o registo pk=1)."""

    # Empresa
    company_name = models.CharField("nome da empresa", max_length=200, default="A Minha Empresa")
    company_tax_id = models.CharField("NIF", max_length=30, blank=True)
    company_address = models.TextField("morada", blank=True)
    company_email = models.EmailField("email", blank=True)
    company_phone = models.CharField("telefone", max_length=50, blank=True)
    company_website = models.CharField("website", max_length=200, blank=True)
    logo = models.ImageField("logótipo", upload_to="branding/", blank=True)
    brand_color = models.CharField(
        "cor de destaque",
        max_length=7,
        default="#1f5f8b",
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use o formato #RRGGBB.")],
        help_text="Usada no cabeçalho e destaques dos emails, ex.: #1f5f8b.",
    )

    # Documentos de cobrança
    document_title = models.CharField(
        "título do documento", max_length=60, default="Nota de cobrança"
    )
    document_prefix = models.CharField("prefixo da numeração", max_length=10, default="NC")
    document_footer = models.TextField(
        "rodapé do documento",
        blank=True,
        default="Este documento não serve de fatura.",
    )
    days_before_due = models.PositiveIntegerField(
        "emitir com antecedência (dias)",
        default=15,
        help_text="As cobranças das subscrições são geradas automaticamente "
        "este número de dias antes da data de vencimento.",
    )
    auto_generate = models.BooleanField(
        "gerar cobranças automaticamente",
        default=True,
        help_text="Se desligado, as cobranças só são geradas manualmente "
        "(ação “Gerar próxima cobrança” nas subscrições).",
    )

    # Contrato
    contract_title = models.CharField(
        "título do contrato", max_length=100, default="Contrato de prestação de serviços"
    )
    contract_terms = models.TextField(
        "condições gerais",
        blank=True,
        default=DEFAULT_CONTRACT_TERMS,
        validators=[validate_template],
        help_text="Aparecem no contrato depois dos serviços, duração e pagamento (preenchidos "
        "automaticamente). Pode usar variáveis, ex.: <code>{{ company.company_name }}</code>, "
        "<code>{{ customer.name }}</code>, <code>{{ subscription.name }}</code>.",
    )

    # Pagamento por transferência bancária
    bank_name = models.CharField("banco", max_length=100, blank=True)
    bank_account_holder = models.CharField("titular da conta", max_length=200, blank=True)
    iban = models.CharField("IBAN", max_length=50, blank=True)
    bic_swift = models.CharField("BIC/SWIFT", max_length=20, blank=True)
    payment_instructions = models.TextField(
        "instruções de pagamento adicionais", blank=True, default=DEFAULT_PAYMENT_INSTRUCTIONS
    )

    # Notificações
    notifications_enabled = models.BooleanField("notificações ativas", default=False)
    notify_on_subscription = models.BooleanField(
        "enviar plano de pagamentos ao criar subscrição", default=True
    )
    notify_on_issue = models.BooleanField("enviar documento quando é emitido", default=True)
    reminder_days = models.CharField(
        "lembretes antes do vencimento (dias)",
        max_length=100,
        default="7,1",
        blank=True,
        validators=[validate_day_list],
        help_text="Dias antes do vencimento, separados por vírgulas. 0 = no próprio dia.",
    )
    overdue_days = models.CharField(
        "avisos de atraso (dias após vencimento)",
        max_length=100,
        default="3,10",
        blank=True,
        validators=[validate_day_list],
    )
    notify_on_payment = models.BooleanField("confirmar receção do pagamento", default=True)
    notification_bcc = models.EmailField(
        "enviar cópia (BCC) para", blank=True, help_text="Ex.: o teu email, para arquivo."
    )
    issue_subject = models.CharField(
        "emissão — assunto", max_length=200, default=DEFAULT_ISSUE_SUBJECT
    )
    issue_body = models.TextField("emissão — mensagem", default=DEFAULT_ISSUE_BODY)
    reminder_subject = models.CharField(
        "lembrete — assunto", max_length=200, default=DEFAULT_REMINDER_SUBJECT
    )
    reminder_body = models.TextField("lembrete — mensagem", default=DEFAULT_REMINDER_BODY)
    overdue_subject = models.CharField(
        "atraso — assunto", max_length=200, default=DEFAULT_OVERDUE_SUBJECT
    )
    overdue_body = models.TextField("atraso — mensagem", default=DEFAULT_OVERDUE_BODY)
    paid_subject = models.CharField(
        "pagamento — assunto", max_length=200, default=DEFAULT_PAID_SUBJECT
    )
    paid_body = models.TextField("pagamento — mensagem", default=DEFAULT_PAID_BODY)
    subscription_subject = models.CharField(
        "plano de pagamentos — assunto", max_length=200, default=DEFAULT_SUBSCRIPTION_SUBJECT
    )
    subscription_body = models.TextField(
        "plano de pagamentos — mensagem", default=DEFAULT_SUBSCRIPTION_BODY
    )
    contract_subject = models.CharField(
        "contrato — assunto", max_length=200, default=DEFAULT_CONTRACT_SUBJECT
    )
    contract_body = models.TextField("contrato — mensagem", default=DEFAULT_CONTRACT_BODY)

    # SMTP
    smtp_host = models.CharField("servidor SMTP", max_length=200, blank=True)
    smtp_port = models.PositiveIntegerField("porta", default=587)
    smtp_username = models.CharField("utilizador", max_length=200, blank=True)
    smtp_password = models.CharField("password", max_length=200, blank=True)
    smtp_security = models.CharField(
        "segurança",
        max_length=10,
        choices=[("starttls", "STARTTLS (587)"), ("ssl", "SSL/TLS (465)"), ("none", "Nenhuma")],
        default="starttls",
    )
    smtp_timeout = models.PositiveIntegerField("timeout (s)", default=20)
    email_from_address = models.EmailField("remetente (email)", blank=True)
    email_from_name = models.CharField("remetente (nome)", max_length=100, blank=True)
    email_reply_to = models.EmailField("responder para", blank=True)

    class Meta:
        verbose_name = "configurações"
        verbose_name_plural = "configurações"

    def __str__(self):
        return "Configurações"

    def save(self, *args, **kwargs):
        self.pk = 1
        self.contract_terms = clean_html(self.contract_terms)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls) -> "SiteSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.email_from_address)

    @property
    def from_email(self) -> str:
        if self.email_from_name:
            return f"{self.email_from_name} <{self.email_from_address}>"
        return self.email_from_address

    @property
    def reminder_day_list(self) -> list[int]:
        return parse_day_list(self.reminder_days)

    @property
    def overdue_day_list(self) -> list[int]:
        return parse_day_list(self.overdue_days)

    @property
    def bank_details(self) -> list[tuple[str, str]]:
        return [
            (label, value)
            for label, value in [
                ("Titular", self.bank_account_holder),
                ("Banco", self.bank_name),
                ("IBAN", self.iban),
                ("BIC/SWIFT", self.bic_swift),
            ]
            if value
        ]

    def payment_instructions_text(self) -> str:
        lines = ["Pagamento por transferência bancária:"]
        lines += [f"  {label}: {value}" for label, value in self.bank_details]
        if self.payment_instructions:
            lines.append("")
            lines.append(self.payment_instructions)
        return "\n".join(lines)
