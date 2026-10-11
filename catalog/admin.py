from django.contrib import admin

from .models import PricePlan, Product


class PricePlanInline(admin.TabularInline):
    model = PricePlan
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "plans_summary", "is_active"]
    list_filter = ["type", "is_active"]
    search_fields = ["name", "description"]
    inlines = [PricePlanInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("price_plans")

    @admin.display(description="planos")
    def plans_summary(self, obj):
        return " · ".join(
            f"{p.get_cycle_display()}: {p.price} €" for p in obj.price_plans.all() if p.is_active
        )
