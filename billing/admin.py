from datetime import timedelta

from django.contrib import admin, messages
from django.contrib.admin.options import IS_POPUP_VAR
from django.core.exceptions import PermissionDenied
from django.db.models import Min, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from .models import Invoice, InvoiceLine, Notification, Subscription, SubscriptionItem
from .notifications import send_notification, send_subscription_plan
from .schedule import PlanSummary, euros, payment_schedule
from .services import (
    create_invoice_for_subscription,
    mark_paid,
    notify_paid,
    subscription_created,
)


class NotificationInline(admin.TabularInline):
    model = Notification
    extra = 0
    can_delete = False
    fields = ["created_at", "kind", "recipient", "subject", "success", "error"]
    readonly_fields = fields
    verbose_name_plural = "emails enviados"

    def has_add_permission(self, request, obj=None):
        return False


# --- Subscrições ----------------------------------------------------------------------------


class SubscriptionItemInline(admin.TabularInline):
    model = SubscriptionItem
    extra = 1
    autocomplete_fields = ["product"]
    fields = [
        "product",
        "description",
        "cycle",
        "next_due_date",
        "quantity",
        "unit_price_override",
        "price_display",
        "setup_fee_charged",
        "guests_display",
    ]
    readonly_fields = ["price_display", "guests_display"]

    @admin.display(description="valor por período")
    def price_display(self, obj):
        if not obj.pk:
            return "—"
        if obj.unit_price is None:
            return format_html('<span style="color:#ba2121">sem plano</span>')
        return euros(obj.total)

    @admin.display(description="VMs/LXCs")
    def guests_display(self, obj):
        if not obj.pk:
            return "—"
        guests = obj.proxmox_guests.all()
        if not guests:
            return "—"
        return format_html_join(
            ", ",
            '<a href="{}">{}</a>',
            (
                (reverse("admin:proxmox_proxmoxguest_change", args=[g.pk]), g.short_label)
                for g in guests
            ),
        )


class InvoiceInlineForSubscription(admin.TabularInline):
    model = Invoice
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ["number_display", "issue_date", "due_date", "total", "status"]
    readonly_fields = fields
    verbose_name_plural = "cobranças"

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="número")
    def number_display(self, obj):
        return obj.number


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    change_form_template = "admin/billing/subscription/change_form.html"
    list_display = [
        "__str__",
        "customer",
        "products_summary",
        "next_due_display",
        "monthly_display",
        "status",
    ]
    list_filter = ["status", "items__cycle"]
    search_fields = ["name", "customer__name", "customer__company", "items__description"]
    autocomplete_fields = ["customer"]
    inlines = [SubscriptionItemInline, InvoiceInlineForSubscription, NotificationInline]
    actions = ["generate_next_invoice"]
    fieldsets = [
        (None, {"fields": ["customer", "name", "status"]}),
        ("Datas", {"fields": [("start_date", "end_date")]}),
        (None, {"fields": ["notes"]}),
    ]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("customer")
            .prefetch_related("items__product__price_plans")
            .annotate(_next_due=Min("items__next_due_date"))
        )

    @admin.display(description="produtos")
    def products_summary(self, obj):
        return " · ".join(
            f"{item.label} ({item.get_cycle_display().lower()})" for item in obj.items.all()
        )

    @admin.display(description="próximo vencimento", ordering="_next_due")
    def next_due_display(self, obj):
        return obj._next_due

    @admin.display(description="valor mensal")
    def monthly_display(self, obj):
        return euros(obj.monthly_value)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        # plano de pagamentos, mostrado no fim do formulário (ver change_form.html)
        obj = self.get_object(request, object_id)
        charges = payment_schedule(obj) if obj else []
        extra_context = {**(extra_context or {}), "summary": PlanSummary(charges)}
        return super().change_view(request, object_id, form_url, extra_context)

    def add_view(self, request, form_url="", extra_context=None):
        extra_context = {**(extra_context or {}), "summary": PlanSummary([])}
        return super().add_view(request, form_url, extra_context)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if not change:
            sub = form.instance
            if subscription_created(sub):
                self.message_user(
                    request, f"Plano de pagamentos enviado para {sub.customer.notification_email}."
                )

    def response_add(self, request, obj, post_url_continue=None):
        # Depois de criar, fica na subscrição para ver o plano de pagamentos.
        if "_addanother" in request.POST or IS_POPUP_VAR in request.POST:
            return super().response_add(request, obj, post_url_continue)
        self.message_user(request, f"Subscrição “{obj}” criada.")
        return redirect("admin:billing_subscription_change", obj.pk)

    @admin.action(description="Gerar próxima cobrança agora")
    def generate_next_invoice(self, request, queryset):
        for sub in queryset:
            invoice = create_invoice_for_subscription(sub)
            if invoice:
                self.message_user(
                    request, f"Gerada {invoice.number} ({euros(invoice.total)}) — {sub}."
                )
            else:
                self.message_user(
                    request,
                    f"{sub}: nada a cobrar (sem produtos, inativa ou terminada).",
                    messages.WARNING,
                )

    def get_urls(self):
        return [
            path(
                "<int:pk>/send-plan/",
                self.admin_site.admin_view(self.send_plan_view),
                name="billing_subscription_send_plan",
            ),
        ] + super().get_urls()

    def send_plan_view(self, request, pk):
        sub = get_object_or_404(Subscription.objects.select_related("customer"), pk=pk)
        if request.method == "POST" and self.has_change_permission(request, sub):
            log = send_subscription_plan(sub)
            if log.success:
                self.message_user(request, f"Plano de pagamentos enviado para {log.recipient}.")
            else:
                self.message_user(request, f"Falhou o envio: {log.error}", messages.ERROR)
        return redirect("admin:billing_subscription_change", pk)


# --- Cobranças ------------------------------------------------------------------------------


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    fields = ["description", "quantity", "unit_price", "total"]
    readonly_fields = ["total"]


class OverdueFilter(admin.SimpleListFilter):
    title = "vencimento"
    parameter_name = "due"

    def lookups(self, request, model_admin):
        return [("overdue", "Em atraso"), ("next30", "Vence nos próximos 30 dias")]

    def queryset(self, request, queryset):
        today = timezone.localdate()
        pending = queryset.filter(status=Invoice.Status.PENDING)
        if self.value() == "overdue":
            return pending.filter(due_date__lt=today)
        if self.value() == "next30":
            return pending.filter(due_date__gte=today, due_date__lte=today + timedelta(days=30))
        return queryset


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    change_form_template = "admin/billing/invoice/change_form.html"
    list_display = [
        "number_display",
        "customer",
        "issue_date",
        "due_date",
        "total",
        "status_badge",
        "paid_on",
        "document_links",
    ]
    list_filter = ["status", OverdueFilter, "issue_date"]
    search_fields = [
        "sequence",
        "customer__name",
        "customer__company",
        "billing_name",
        "payment_reference",
    ]
    autocomplete_fields = ["customer", "subscription"]
    date_hierarchy = "due_date"
    inlines = [InvoiceLineInline, NotificationInline]
    actions = ["action_mark_paid", "action_mark_pending", "action_cancel", "action_send_email"]
    readonly_fields = ["number_display", "total", "created_at"]
    fieldsets = [
        (
            None,
            {
                "fields": [
                    "number_display",
                    ("customer", "subscription"),
                    ("issue_date", "due_date"),
                    ("period_start", "period_end"),
                    "notes",
                ]
            },
        ),
        ("Pagamento", {"fields": [("status", "paid_on"), "payment_reference", "total"]}),
        (
            "Dados do cliente no documento",
            {
                "classes": ["collapse"],
                "description": "Copiados da ficha do cliente na emissão. "
                "Deixe o nome vazio para voltar a copiar.",
                "fields": [
                    "billing_name",
                    "billing_tax_id",
                    "billing_address",
                    "billing_email",
                    "email_pending",
                ],
            },
        ),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("customer")

    def get_search_results(self, request, queryset, search_term):
        # Permite procurar pelo número completo, ex. "NC 2026/0007" ou "2026/7".
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        if "/" in search_term:
            year, _, seq = search_term.rpartition("/")
            year = year.split()[-1] if year.split() else ""
            if year.isdigit() and seq.isdigit():
                queryset |= self.model.objects.filter(year=int(year), sequence=int(seq))
        return queryset, may_have_duplicates

    def save_model(self, request, obj, form, change):
        became_paid = obj.status == Invoice.Status.PAID and (
            not change or "status" in form.changed_data
        )
        if became_paid and not obj.paid_on:
            obj.paid_on = timezone.localdate()
        if obj.status == Invoice.Status.PAID:
            obj.email_pending = False
        super().save_model(request, obj, form, change)
        if became_paid and change:
            notify_paid(obj)

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        if formset.model is InvoiceLine:
            form.instance.recalculate_total()

    @admin.display(description="número", ordering="sequence")
    def number_display(self, obj):
        return obj.number

    @admin.display(description="estado", ordering="status")
    def status_badge(self, obj):
        if obj.is_overdue:
            color, label = "#ba2121", "Em atraso"
        else:
            color = {"paid": "#1d7a33", "pending": "#a86b00", "cancelled": "#777"}[obj.status]
            label = obj.get_status_display()
        return format_html('<strong style="color:{}">{}</strong>', color, label)

    @admin.display(description="documento")
    def document_links(self, obj):
        return format_html(
            '<a href="{}" target="_blank">ver</a> · <a href="{}">PDF</a>',
            reverse("admin:billing_invoice_view", args=[obj.pk]),
            reverse("admin:billing_invoice_pdf", args=[obj.pk]),
        )

    # --- ações em massa ---

    @admin.action(description="Marcar como pago (hoje)")
    def action_mark_paid(self, request, queryset):
        count = sum(mark_paid(inv) for inv in queryset.exclude(status=Invoice.Status.CANCELLED))
        self.message_user(request, f"{count} cobrança(s) marcada(s) como paga(s).")

    @admin.action(description="Marcar como por pagar")
    def action_mark_pending(self, request, queryset):
        count = queryset.update(status=Invoice.Status.PENDING, paid_on=None)
        self.message_user(request, f"{count} cobrança(s) marcada(s) como por pagar.")

    @admin.action(description="Anular")
    def action_cancel(self, request, queryset):
        count = queryset.update(status=Invoice.Status.CANCELLED, email_pending=False)
        self.message_user(request, f"{count} cobrança(s) anulada(s).")

    @admin.action(description="Enviar por email ao cliente")
    def action_send_email(self, request, queryset):
        for invoice in queryset:
            self._send(request, invoice)

    def _send(self, request, invoice):
        kind = Notification.Kind.PAID if invoice.status == "paid" else Notification.Kind.ISSUE
        log = send_notification(invoice, kind)
        if log.success:
            self.message_user(request, f"{invoice.number} enviada para {log.recipient}.")
        else:
            self.message_user(request, f"{invoice.number}: falhou — {log.error}", messages.ERROR)

    # --- vistas extra ---

    def get_urls(self):
        view = self.admin_site.admin_view
        return [
            path("<int:pk>/view/", view(self.document_view), name="billing_invoice_view"),
            path("<int:pk>/pdf/", view(self.pdf_view), name="billing_invoice_pdf"),
            path("<int:pk>/send/", view(self.send_view), name="billing_invoice_send"),
            path("<int:pk>/pay/", view(self.pay_view), name="billing_invoice_pay"),
        ] + super().get_urls()

    def _get(self, request, pk):
        invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
        if not self.has_view_permission(request, invoice):
            raise PermissionDenied
        return invoice

    def document_view(self, request, pk):
        from .pdf import render_invoice_html

        return HttpResponse(render_invoice_html(self._get(request, pk), for_print=True))

    def pdf_view(self, request, pk):
        from .pdf import render_invoice_pdf

        invoice = self._get(request, pk)
        response = HttpResponse(render_invoice_pdf(invoice), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{invoice.pdf_filename}"'
        return response

    def send_view(self, request, pk):
        invoice = self._get(request, pk)
        if request.method == "POST" and self.has_change_permission(request, invoice):
            self._send(request, invoice)
        return redirect("admin:billing_invoice_change", pk)

    def pay_view(self, request, pk):
        invoice = self._get(request, pk)
        if request.method == "POST" and self.has_change_permission(request, invoice):
            if mark_paid(invoice):
                self.message_user(request, f"{invoice.number} marcada como paga.")
        return redirect("admin:billing_invoice_change", pk)


@admin.register(SubscriptionItem)
class SubscriptionItemAdmin(admin.ModelAdmin):
    """Necessário para a pesquisa (autocomplete) ao associar VMs/LXCs. Não aparece no menu."""

    search_fields = [
        "description",
        "product__name",
        "subscription__name",
        "subscription__customer__name",
        "subscription__customer__company",
    ]

    def get_model_perms(self, request):
        return {}

    def get_search_results(self, request, queryset, search_term):
        queryset = queryset.select_related("product", "subscription__customer").filter(
            ~Q(subscription__status=Subscription.Status.CANCELLED)
        )
        return super().get_search_results(request, queryset, search_term)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["created_at", "kind", "invoice", "subscription", "recipient", "success"]
    list_filter = ["kind", "success"]
    search_fields = [
        "recipient",
        "subject",
        "invoice__customer__name",
        "subscription__customer__name",
    ]
    readonly_fields = [f.name for f in Notification._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
