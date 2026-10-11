from django.core.validators import MinValueValidator
from django.db import models


class BillingCycle(models.IntegerChoices):
    """O valor é o número de meses do ciclo."""

    MONTHLY = 1, "Mensal"
    QUARTERLY = 3, "Trimestral"
    SEMIANNUAL = 6, "Semestral"
    ANNUAL = 12, "Anual"
    BIENNIAL = 24, "Bienal"
    TRIENNIAL = 36, "Trienal"


class ProductType(models.TextChoices):
    HOSTING = "hosting", "Alojamento"
    VPS = "vps", "VPS / Servidor"
    DOMAIN = "domain", "Domínio"
    EMAIL = "email", "Email"
    SSL = "ssl", "Certificado SSL"
    ADDON = "addon", "Addon"
    SERVICE = "service", "Serviço / Manutenção"
    OTHER = "other", "Outro"


class Product(models.Model):
    name = models.CharField("nome", max_length=200)
    type = models.CharField("tipo", max_length=20, choices=ProductType, default=ProductType.HOSTING)
    description = models.TextField("descrição", blank=True)
    is_active = models.BooleanField("ativo", default=True)

    class Meta:
        verbose_name = "produto"
        verbose_name_plural = "produtos"
        ordering = ["type", "name"]

    def __str__(self):
        return f"{self.name} [{self.get_type_display()}]"

    def price_for(self, cycle: int):
        plan = self.price_plans.filter(cycle=cycle, is_active=True).first()
        return plan.price if plan else None


class PricePlan(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="price_plans", verbose_name="produto"
    )
    cycle = models.PositiveSmallIntegerField("periodicidade", choices=BillingCycle)
    price = models.DecimalField(
        "preço (€)", max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )
    setup_fee = models.DecimalField(
        "taxa de ativação (€)",
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Cobrada uma única vez, na primeira cobrança.",
    )
    is_active = models.BooleanField("ativo", default=True)

    class Meta:
        verbose_name = "plano de pagamento"
        verbose_name_plural = "planos de pagamento"
        ordering = ["product", "cycle"]
        constraints = [
            models.UniqueConstraint(fields=["product", "cycle"], name="unique_product_cycle"),
        ]

    def __str__(self):
        return f"{self.product.name} — {self.get_cycle_display()}: {self.price} €"
