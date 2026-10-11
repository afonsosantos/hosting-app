import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Cria o superutilizador definido em DJANGO_SUPERUSER_USERNAME/EMAIL/PASSWORD, "
        "se ainda não existir. Não altera utilizadores existentes."
    )

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        if not username or not password:
            return
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            return
        User.objects.create_superuser(
            username=username, email=os.environ.get("DJANGO_SUPERUSER_EMAIL", ""), password=password
        )
        self.stdout.write(f"Superutilizador “{username}” criado.")
