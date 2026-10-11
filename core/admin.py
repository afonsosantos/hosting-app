from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse

from .email import build_email
from .models import SiteSettings
from .richtext import RichTextWidget


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    change_form_template = "admin/core/sitesettings/change_form.html"
    fieldsets = [
        (
            "Empresa",
            {
                "fields": [
                    "company_name",
                    "company_tax_id",
                    "company_address",
                    ("company_email", "company_phone"),
                    "company_website",
                    ("logo", "brand_color"),
                ]
            },
        ),
        (
            "Documentos de cobrança",
            {
                "fields": [
                    ("document_title", "document_prefix"),
                    "document_footer",
                    ("auto_generate", "days_before_due"),
                ]
            },
        ),
        (
            "Contrato",
            {"classes": ["collapse"], "fields": ["contract_title", "contract_terms"]},
        ),
        (
            "Pagamento por transferência bancária",
            {
                "fields": [
                    ("bank_name", "bank_account_holder"),
                    ("iban", "bic_swift"),
                    "payment_instructions",
                ]
            },
        ),
        (
            "Servidor de email (SMTP)",
            {
                "fields": [
                    ("smtp_host", "smtp_port", "smtp_security"),
                    ("smtp_username", "smtp_password"),
                    "smtp_timeout",
                    ("email_from_name", "email_from_address"),
                    "email_reply_to",
                ]
            },
        ),
        (
            "Notificações a clientes",
            {
                "fields": [
                    "notifications_enabled",
                    "notify_on_subscription",
                    "notify_on_issue",
                    ("reminder_days", "overdue_days"),
                    "notify_on_payment",
                    "notification_bcc",
                ]
            },
        ),
        (
            "Modelos de email",
            {
                "classes": ["collapse"],
                "description": (
                    "Escreva só a mensagem: o resumo do documento, as tabelas de produtos "
                    "e do plano, os dados de transferência e a assinatura são acrescentados "
                    "automaticamente pelo layout do email.<br>"
                    "Sintaxe de templates Django. Variáveis: <code>customer</code>, "
                    "<code>document</code> (number, total, due_date…), "
                    "<code>company</code> (estas configurações), <code>days</code> "
                    "e <code>subscription</code>."
                ),
                "fields": [
                    "subscription_subject",
                    "subscription_body",
                    "contract_subject",
                    "contract_body",
                    "issue_subject",
                    "issue_body",
                    "reminder_subject",
                    "reminder_body",
                    "overdue_subject",
                    "overdue_body",
                    "paid_subject",
                    "paid_body",
                ],
            },
        ),
    ]

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "smtp_password":
            from django import forms

            kwargs["widget"] = forms.PasswordInput(render_value=True)
        if db_field.name == "contract_terms":
            kwargs["widget"] = RichTextWidget()
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = SiteSettings.load()
        return redirect(reverse("admin:core_sitesettings_change", args=[obj.pk]))

    def get_urls(self):
        return [
            path(
                "test-email/",
                self.admin_site.admin_view(self.test_email_view),
                name="core_sitesettings_test_email",
            ),
        ] + super().get_urls()

    def test_email_view(self, request):
        if not self.has_change_permission(request):
            return redirect("admin:index")
        url = reverse("admin:core_sitesettings_change", args=[1])
        if request.method != "POST":
            return redirect(url)
        to = request.user.email
        if not to:
            messages.error(request, "O teu utilizador não tem email definido.")
            return redirect(url)
        try:
            build_email(
                "Email de teste",
                "Se recebeste este email, a configuração SMTP está a funcionar.",
                [to],
                bcc="",
            ).send()
        except Exception as exc:  # noqa: BLE001 - mostrar qualquer erro SMTP ao utilizador
            messages.error(request, f"Falhou o envio para {to}: {exc}")
        else:
            messages.success(request, f"Email de teste enviado para {to}.")
        return redirect(url)
