from datetime import timedelta
from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from catalog.models import BillingCycle, Product
from core.dates import add_months
from customers.models import Customer


class Subscription(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Ativa"
        SUSPENDED = "suspended", "Suspensa"
        CANCELLED = "cancelled", "Cancelada"

    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="subscriptions", verbose_name="cliente"
    )
    name = models.CharField(
        "designação", max_length=200, blank=True, help_text="Ex.: “Site exemplo.pt”."
    )
    start_date = models.DateField(
        "data de início",
        default=timezone.localdate,
        help_text="Primeiro vencimento dos produtos que não tenham data própria.",
    )
    end_date = models.DateField(
        "data de fim",
        null=True,
        blank=True,
        help_text="Opcional. Não são geradas cobranças para períodos que comecem depois desta data.",
    )
    status = models.CharField("estado", max_length=20, choices=Status, default=Status.ACTIVE)
    notes = models.TextField("notas internas", blank=True)
    created_at = models.DateTimeField("criada em", auto_now_add=True)

    class Meta:
        verbose_name = "subscrição"
        verbose_name_plural = "subscrições"
        ordering = ["customer__name", "name"]

    def __str__(self):
        label = self.name or f"Subscrição #{self.pk}"
        return f"{label} — {self.customer}"

    @property
    @admin.display(description="próximo vencimento")
    def next_due_date(self):
        """Vencimento mais próximo entre os produtos da subscrição."""
        return min((item.next_due_date for item in self.items.all()), default=None)

    @property
    def monthly_value(self) -> Decimal:
        return sum((item.monthly_value for item in self.items.all()), Decimal("0"))

    def is_billable(self, period_start) -> bool:
        return self.status == self.Status.ACTIVE and (
            self.end_date is None or period_start <= self.end_date
        )


class SubscriptionItem(models.Model):
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="items", verbose_name="subscrição"
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name="produto")
    description = models.CharField(
        "detalhe", max_length=200, blank=True, help_text="Ex.: domínio, nome do site, servidor."
    )
    cycle = models.PositiveSmallIntegerField(
        "periodicidade",
        choices=BillingCycle,
        blank=True,
        help_text="Vazio = a única periodicidade com plano ativo para o produto.",
    )
    next_due_date = models.DateField(
        "próximo vencimento",
        blank=True,
        help_text="Início do próximo período a cobrar; avança sempre que é gerada uma cobrança. "
        "Vazio = data de início da subscrição (ou hoje, se já passou).",
    )
    quantity = models.PositiveIntegerField("quantidade", default=1)
    unit_price_override = models.DecimalField(
        "preço especial (€)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Deixe vazio para usar o preço do plano desta periodicidade.",
    )
    setup_fee_charged = models.BooleanField(
        "taxa de ativação já cobrada",
        default=False,
        help_text="Marque se este item não deve cobrar taxa de ativação (ex. migrações).",
    )

    class Meta:
        verbose_name = "produto da subscrição"
        verbose_name_plural = "produtos da subscrição"
        ordering = ["id"]

    def __str__(self):
        label = (
            f"{self.product.name} — {self.description}" if self.description else self.product.name
        )
        return f"{label} ({self.subscription.customer})"

    def save(self, *args, **kwargs):
        if not self.cycle:
            self.cycle = self._default_cycle()
        if not self.next_due_date:
            self.next_due_date = max(self.subscription.start_date, timezone.localdate())
        super().save(*args, **kwargs)

    @property
    def label(self) -> str:
        return (
            f"{self.product.name} — {self.description}" if self.description else self.product.name
        )

    def _default_cycle(self):
        cycles = [p.cycle for p in self.product.price_plans.all() if p.is_active]
        return cycles[0] if len(cycles) == 1 else None

    def period_for(self, start):
        """Devolve (início, fim) do período que começa em `start`."""
        return start, add_months(start, self.cycle) - timedelta(days=1)

    @property
    def plan(self):
        if not self.cycle:
            return None
        return next(
            (p for p in self.product.price_plans.all() if p.cycle == self.cycle and p.is_active),
            None,
        )

    @property
    def unit_price(self) -> Decimal | None:
        if self.unit_price_override is not None:
            return self.unit_price_override
        plan = self.plan
        return plan.price if plan else None

    @property
    def total(self) -> Decimal:
        return (self.unit_price or Decimal("0")) * self.quantity

    @property
    def monthly_value(self) -> Decimal:
        if not self.cycle:
            return Decimal("0")
        return (self.total / self.cycle).quantize(Decimal("0.01"))

    @property
    def pending_setup_fee(self) -> Decimal:
        if self.setup_fee_charged:
            return Decimal("0")
        plan = self.plan
        return plan.setup_fee if plan else Decimal("0")

    def clean(self):
        if not self.product_id:
            return
        if not self.cycle:
            self.cycle = self._default_cycle()
            if not self.cycle:
                raise ValidationError(
                    {"cycle": "Escolha a periodicidade (o produto tem vários planos ou nenhum)."}
                )
        if self.unit_price_override is None and self.plan is None:
            raise ValidationError(
                {
                    "cycle": f"“{self.product.name}” não tem plano "
                    f"{self.get_cycle_display().lower()} ativo. "
                    "Crie o plano no catálogo ou indique um preço especial."
                }
            )


class Invoice(models.Model):
    """Documento de cobrança (nota de cobrança / aviso de pagamento)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Por pagar"
        PAID = "paid", "Pago"
        CANCELLED = "cancelled", "Anulado"

    series = models.CharField("série", max_length=10, editable=False)
    year = models.PositiveIntegerField("ano", editable=False)
    sequence = models.PositiveIntegerField("n.º", editable=False)

    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="invoices", verbose_name="cliente"
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
        verbose_name="subscrição",
    )
    issue_date = models.DateField("data de emissão", default=timezone.localdate)
    due_date = models.DateField("data de vencimento", default=timezone.localdate)
    period_start = models.DateField("período — início", null=True, blank=True)
    period_end = models.DateField("período — fim", null=True, blank=True)
    status = models.CharField("estado", max_length=20, choices=Status, default=Status.PENDING)
    paid_on = models.DateField("pago em", null=True, blank=True)
    payment_reference = models.CharField("referência do pagamento", max_length=200, blank=True)
    total = models.DecimalField(
        "total (€)", max_digits=12, decimal_places=2, default=0, editable=False
    )
    notes = models.TextField("observações (aparecem no documento)", blank=True)
    email_pending = models.BooleanField(
        "envio por email pendente",
        default=False,
        help_text="Quando marcado, o documento é enviado ao cliente na próxima execução automática.",
    )

    # Dados do cliente no momento da emissão
    billing_name = models.CharField("nome", max_length=200, blank=True)
    billing_tax_id = models.CharField("NIF", max_length=30, blank=True)
    billing_address = models.TextField("morada", blank=True)
    billing_email = models.EmailField("email", blank=True)

    created_at = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "cobrança"
        verbose_name_plural = "cobranças"
        ordering = ["-year", "-sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["series", "year", "sequence"], name="unique_invoice_number"
            ),
        ]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.billing_name and self.customer_id:
            self.snapshot_customer()
        if self.sequence:
            super().save(*args, **kwargs)
            return
        from .services import assign_number

        # O lock da numeração tem de durar até o registo estar inserido.
        with transaction.atomic():
            assign_number(self)
            super().save(*args, **kwargs)

    @property
    def number(self) -> str:
        if not self.sequence:
            return "(por numerar)"
        return f"{self.series} {self.year}/{self.sequence:04d}"

    @property
    def is_overdue(self) -> bool:
        return self.status == self.Status.PENDING and self.due_date < timezone.localdate()

    @property
    def pdf_filename(self) -> str:
        return f"{self.series}_{self.year}_{self.sequence:04d}.pdf"

    def snapshot_customer(self):
        c = self.customer
        self.billing_name = c.billing_name
        self.billing_tax_id = c.tax_id
        self.billing_address = c.full_address
        self.billing_email = c.notification_email

    def recalculate_total(self, save=True):
        self.total = sum((line.total for line in self.lines.all()), Decimal("0"))
        if save:
            Invoice.objects.filter(pk=self.pk).update(total=self.total)


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="lines", verbose_name="cobrança"
    )
    subscription_item = models.ForeignKey(
        SubscriptionItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_lines",
        verbose_name="produto da subscrição",
    )
    description = models.CharField("descrição", max_length=300)
    quantity = models.DecimalField("qtd.", max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField("preço unit. (€)", max_digits=10, decimal_places=2)
    total = models.DecimalField("total (€)", max_digits=12, decimal_places=2, editable=False)

    class Meta:
        verbose_name = "linha"
        verbose_name_plural = "linhas"
        ordering = ["id"]

    def __str__(self):
        return self.description

    def save(self, *args, **kwargs):
        total = Decimal(str(self.quantity)) * Decimal(str(self.unit_price))
        self.total = total.quantize(Decimal("0.01"))
        super().save(*args, **kwargs)


class Notification(models.Model):
    class Kind(models.TextChoices):
        ISSUE = "issue", "Emissão"
        REMINDER = "reminder", "Lembrete"
        OVERDUE = "overdue", "Atraso"
        PAID = "paid", "Pagamento recebido"
        SUBSCRIPTION = "subscription", "Plano de pagamentos"

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
        verbose_name="cobrança",
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
        verbose_name="subscrição",
    )
    kind = models.CharField("tipo", max_length=20, choices=Kind)
    offset_days = models.IntegerField("dias", null=True, blank=True)
    recipient = models.CharField("destinatário", max_length=300)
    subject = models.CharField("assunto", max_length=300, blank=True)
    success = models.BooleanField("enviado", default=False)
    error = models.TextField("erro", blank=True)
    created_at = models.DateTimeField("data", auto_now_add=True)

    class Meta:
        verbose_name = "notificação enviada"
        verbose_name_plural = "notificações enviadas"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} — {self.invoice or self.subscription}"
