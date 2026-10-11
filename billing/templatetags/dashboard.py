from datetime import timedelta
from decimal import Decimal

from django import template
from django.db.models import Count, Sum
from django.utils import timezone

from billing.models import Invoice, Subscription

register = template.Library()


@register.inclusion_tag("admin/dashboard.html")
def billing_dashboard():
    today = timezone.localdate()
    pending = Invoice.objects.filter(status=Invoice.Status.PENDING)

    def summary(qs):
        data = qs.aggregate(n=Count("id"), total=Sum("total"))
        return {"count": data["n"], "total": data["total"] or Decimal("0")}

    active = Subscription.objects.filter(status=Subscription.Status.ACTIVE).prefetch_related(
        "items__product__price_plans"
    )
    monthly = sum((s.monthly_value for s in active), Decimal("0"))

    return {
        "overdue": summary(pending.filter(due_date__lt=today)),
        "upcoming": summary(
            pending.filter(due_date__gte=today, due_date__lte=today + timedelta(30))
        ),
        "pending": summary(pending),
        "paid_month": summary(
            Invoice.objects.filter(
                status=Invoice.Status.PAID,
                paid_on__year=today.year,
                paid_on__month=today.month,
            )
        ),
        "active_count": len(active),
        "monthly": monthly,
        "overdue_list": pending.filter(due_date__lt=today)
        .select_related("customer")
        .order_by("due_date")[:10],
    }
