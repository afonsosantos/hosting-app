import logging

from django.db import transaction
from django.utils import timezone

from .models import ProxmoxCluster, ProxmoxGuest

logger = logging.getLogger(__name__)


def test_connection(cluster: ProxmoxCluster) -> str:
    version = cluster.api().version.get()
    return f"Proxmox VE {version.get('version')} (release {version.get('release')})"


def sync_cluster(cluster: ProxmoxCluster) -> dict:
    """Importa/atualiza as VMs e LXCs do cluster. Mantém as associações a subscrições."""
    now = timezone.now()
    try:
        resources = cluster.api().cluster.resources.get(type="vm")
    except Exception as exc:
        cluster.last_error = str(exc)
        cluster.save(update_fields=["last_error"])
        raise

    created = updated = 0
    seen = set()
    with transaction.atomic():
        for r in resources:
            if r.get("type") not in ("qemu", "lxc"):
                continue
            vmid = int(r["vmid"])
            seen.add(vmid)
            _, was_created = ProxmoxGuest.objects.update_or_create(
                cluster=cluster,
                vmid=vmid,
                defaults={
                    "type": r["type"],
                    "name": r.get("name", ""),
                    "node": r.get("node", ""),
                    "status": r.get("status", ""),
                    "cpus": r.get("maxcpu"),
                    "memory": r.get("maxmem"),
                    "disk": r.get("maxdisk"),
                    "tags": (r.get("tags") or "").replace(";", ", "),
                    "is_template": bool(r.get("template")),
                    "missing": False,
                    "last_seen": now,
                },
            )
            created += was_created
            updated += not was_created
        missing = cluster.guests.exclude(vmid__in=seen).filter(missing=False).update(missing=True)
        cluster.last_sync = now
        cluster.last_error = ""
        cluster.save(update_fields=["last_sync", "last_error"])

    result = {"created": created, "updated": updated, "missing": missing}
    logger.info("Sync Proxmox %s: %s", cluster, result)
    return result


def sync_all(only_auto=True) -> None:
    clusters = ProxmoxCluster.objects.filter(is_active=True)
    if only_auto:
        clusters = clusters.filter(auto_sync=True)
    for cluster in clusters:
        try:
            sync_cluster(cluster)
        except Exception:
            logger.exception("Falha ao sincronizar o cluster Proxmox %s", cluster)
