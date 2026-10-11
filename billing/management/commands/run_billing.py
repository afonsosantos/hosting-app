from django.core.management.base import BaseCommand

from billing.notifications import run_notifications
from billing.services import generate_due_invoices
from core.models import SiteSettings


class Command(BaseCommand):
    help = "Gera as cobranças a vencer e envia emissões, lembretes e avisos de atraso."

    def handle(self, *args, **options):
        if SiteSettings.load().auto_generate:
            invoices = generate_due_invoices()
            self.stdout.write(f"Cobranças geradas: {len(invoices)}")
        logs = run_notifications()
        failed = sum(not log.success for log in logs)
        self.stdout.write(f"Notificações: {len(logs) - failed} enviadas, {failed} falhadas")
