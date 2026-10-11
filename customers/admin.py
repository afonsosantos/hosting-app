from django.contrib import admin
from django.db.models import Count, OuterRef, Q, Subquery, Sum
from django.urls import reverse
from django.utils.html import format_html

from billing.models import Invoice, Subscription

from .models import Customer


class SubscriptionInline(admin.TabularInline):
    model = Subscription
    extra = 0
    show_change_link = True
    fields = ["name", "next_due_date", "status"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "company",
        "email",
        "phone",
        "active_subscriptions",
        "pending_total",
        "is_active",
    ]
    list_filter = ["is_active", "notify"]
    search_fields = ["name", "company", "email", "billing_email", "tax_id", "phone"]
    inlines = [SubscriptionInline]
    readonly_fields = ["created_at", "updated_at", "quick_links"]
    fieldsets = [
        (None, {"fields": [("name", "company"), "tax_id", "quick_links"]}),
        ("Contactos", {"fields": [("email", "billing_email"), "phone", "notify"]}),
        ("Morada", {"fields": ["address", ("postal_code", "city"), "country"]}),
        (None, {"fields": ["is_active", "notes", ("created_at", "updated_at")]}),
    ]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _active_subs=Count(
                    "subscriptions",
                    filter=Q(subscriptions__status=Subscription.Status.ACTIVE),
                    distinct=True,
                ),
                # subquery: um Sum direto seria multiplicado pelo join com as subscrições
                _pending=Subquery(
                    Invoice.objects.filter(customer=OuterRef("pk"), status=Invoice.Status.PENDING)
                    .values("customer")
                    .annotate(s=Sum("total"))
                    .values("s")
                ),
            )
        )

    @admin.display(description="subscrições ativas", ordering="_active_subs")
    def active_subscriptions(self, obj):
        return obj._active_subs

    @admin.display(description="por pagar", ordering="_pending")
    def pending_total(self, obj):
        return f"{obj._pending} €" if obj._pending else "—"

    @admin.display(description="atalhos")
    def quick_links(self, obj):
        if not obj.pk:
            return "—"
        return format_html(
            '<a href="{}?customer__id__exact={}">Cobranças</a> · '
            '<a href="{}?customer={}">Nova subscrição</a>',
            reverse("admin:billing_invoice_changelist"),
            obj.pk,
            reverse("admin:billing_subscription_add"),
            obj.pk,
        )
