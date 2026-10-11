from django.core.management.base import BaseCommand

from proxmox.sync import sync_all


class Command(BaseCommand):
    help = "Sincroniza VMs/LXCs de todos os clusters Proxmox ativos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all", action="store_true", help="Inclui clusters sem sincronização automática."
        )

    def handle(self, *args, **options):
        sync_all(only_auto=not options["all"])
