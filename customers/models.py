from django.db import models


class Customer(models.Model):
    name = models.CharField("nome", max_length=200)
    company = models.CharField("empresa", max_length=200, blank=True)
    tax_id = models.CharField("NIF", max_length=30, blank=True)
    email = models.EmailField("email")
    billing_email = models.EmailField(
        "email de faturação",
        blank=True,
        help_text="Se preenchido, as cobranças e lembretes são enviados para aqui.",
    )
    phone = models.CharField("telefone", max_length=50, blank=True)
    address = models.TextField("morada", blank=True)
    postal_code = models.CharField("código postal", max_length=20, blank=True)
    city = models.CharField("localidade", max_length=100, blank=True)
    country = models.CharField("país", max_length=100, default="Portugal")
    notify = models.BooleanField(
        "receber notificações", default=True, help_text="Emails de cobrança, lembretes e avisos."
    )
    is_active = models.BooleanField("ativo", default=True)
    notes = models.TextField("notas internas", blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.company})" if self.company else self.name

    @property
    def billing_name(self) -> str:
        return self.company or self.name

    @property
    def notification_email(self) -> str:
        return self.billing_email or self.email

    @property
    def full_address(self) -> str:
        city_line = " ".join(p for p in [self.postal_code, self.city] if p)
        return "\n".join(p for p in [self.address, city_line, self.country] if p)
