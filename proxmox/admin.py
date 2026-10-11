from django import forms
from django.contrib import admin, messages
from django.template.defaultfilters import filesizeformat
from django.urls import reverse
from django.utils.html import format_html

from .models import ProxmoxCluster, ProxmoxGuest
from .sync import sync_cluster, test_connection


@admin.register(ProxmoxCluster)
class ProxmoxClusterAdmin(admin.ModelAdmin):
    list_display = ["name", "host", "is_active", "auto_sync", "last_sync", "guest_count", "sync_ok"]
    readonly_fields = ["last_sync", "last_error"]
    actions = ["action_test", "action_sync"]
    fieldsets = [
        (None, {"fields": ["name", ("host", "port"), "verify_ssl"]}),
        ("Autenticação (API token)", {"fields": ["user", "token_name", "token_value"]}),
        (None, {"fields": [("is_active", "auto_sync"), "last_sync", "last_error"]}),
    ]

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "token_value":
            kwargs["widget"] = forms.PasswordInput(render_value=True)
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    @admin.display(description="VMs/LXCs")
    def guest_count(self, obj):
        url = reverse("admin:proxmox_proxmoxguest_changelist") + f"?cluster__id__exact={obj.pk}"
        return format_html('<a href="{}">{}</a>', url, obj.guests.filter(missing=False).count())

    @admin.display(description="sem erros", boolean=True)
    def sync_ok(self, obj):
        return not obj.last_error

    @admin.action(description="Testar ligação")
    def action_test(self, request, queryset):
        for cluster in queryset:
            try:
                self.message_user(request, f"{cluster}: ligação OK — {test_connection(cluster)}")
            except Exception as exc:  # noqa: BLE001
                self.message_user(request, f"{cluster}: falhou — {exc}", messages.ERROR)

    @admin.action(description="Sincronizar VMs/LXCs")
    def action_sync(self, request, queryset):
        for cluster in queryset:
            try:
                r = sync_cluster(cluster)
            except Exception as exc:  # noqa: BLE001
                self.message_user(request, f"{cluster}: falhou — {exc}", messages.ERROR)
            else:
                self.message_user(
                    request,
                    f"{cluster}: {r['created']} novas, {r['updated']} atualizadas, "
                    f"{r['missing']} marcadas como removidas.",
                )


class AssignedFilter(admin.SimpleListFilter):
    title = "associação"
    parameter_name = "assigned"

    def lookups(self, request, model_admin):
        return [("yes", "Associadas a cliente"), ("no", "Sem cliente")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(subscription_item__isnull=False)
        if self.value() == "no":
            return queryset.filter(subscription_item__isnull=True)
        return queryset


@admin.register(ProxmoxGuest)
class ProxmoxGuestAdmin(admin.ModelAdmin):
    list_display = [
        "vmid",
        "name",
        "type",
        "node",
        "status",
        "resources",
        "customer_link",
        "missing",
    ]
    list_display_links = ["vmid", "name"]
    list_filter = [AssignedFilter, "type", "status", "node", "missing", "cluster"]
    search_fields = [
        "=vmid",
        "name",
        "tags",
        "subscription_item__subscription__customer__name",
        "subscription_item__subscription__customer__company",
    ]
    autocomplete_fields = ["subscription_item"]
    readonly_fields = [
        "cluster",
        "vmid",
        "type",
        "name",
        "node",
        "status",
        "resources",
        "tags",
        "is_template",
        "missing",
        "last_seen",
    ]
    fieldsets = [
        ("Associação", {"fields": ["subscription_item", "notes"]}),
        (
            "Dados do Proxmox (sincronizados)",
            {
                "fields": [
                    ("cluster", "vmid", "type"),
                    ("name", "node", "status"),
                    "resources",
                    "tags",
                    ("is_template", "missing", "last_seen"),
                ]
            },
        ),
    ]

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "cluster",
                "subscription_item__product",
                "subscription_item__subscription__customer",
            )
        )

    @admin.display(description="recursos")
    def resources(self, obj):
        parts = []
        if obj.cpus:
            parts.append(f"{obj.cpus} vCPU")
        if obj.memory:
            parts.append(f"{filesizeformat(obj.memory)} RAM")
        if obj.disk:
            parts.append(f"{filesizeformat(obj.disk)} disco")
        return " · ".join(parts) or "—"

    @admin.display(description="cliente / subscrição")
    def customer_link(self, obj):
        item = obj.subscription_item
        if not item:
            return "—"
        sub = item.subscription
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:billing_subscription_change", args=[sub.pk]),
            f"{sub.customer} — {item.product.name}"
            + (f" ({item.description})" if item.description else ""),
        )
