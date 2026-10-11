import logging
import os
import signal
import threading

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections

logger = logging.getLogger("scheduler")


class Command(BaseCommand):
    help = "Corre as tarefas periódicas (cobranças, notificações, sync Proxmox) em ciclo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=int(os.environ.get("SCHEDULER_INTERVAL", 3600)),
            help="Segundos entre execuções (por omissão SCHEDULER_INTERVAL ou 3600).",
        )

    def handle(self, *args, interval, **options):
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())

        logger.info("Agendador iniciado (intervalo %ss).", interval)
        while not stop.is_set():
            for command in ("sync_proxmox", "run_billing"):
                close_old_connections()
                try:
                    call_command(command)
                except Exception:
                    logger.exception("Tarefa %s falhou", command)
            close_old_connections()
            stop.wait(interval)
        logger.info("Agendador terminado.")
