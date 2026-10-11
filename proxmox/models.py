from django.db import models

from billing.models import SubscriptionItem


class ProxmoxCluster(models.Model):
    name = models.CharField("nome", max_length=100, default="Cluster")
    host = models.CharField(
        "host", max_length=200, help_text="IP ou hostname de um nó do cluster (sem https://)."
    )
    port = models.PositiveIntegerField("porta", default=8006)
    user = models.CharField("utilizador", max_length=100, default="root@pam")
    token_name = models.CharField(
        "nome do API token",
        max_length=100,
        help_text="Datacenter → Permissions → API Tokens. Basta permissão de leitura "
        "(ex. papel PVEAuditor em /).",
    )
    token_value = models.CharField("segredo do API token", max_length=200)
    verify_ssl = models.BooleanField(
        "verificar certificado SSL",
        default=False,
        help_text="Desligue com certificados self-signed.",
    )
    auto_sync = models.BooleanField("sincronizar automaticamente", default=True)
    is_active = models.BooleanField("ativo", default=True)
    last_sync = models.DateTimeField("última sincronização", null=True, blank=True, editable=False)
    last_error = models.TextField("último erro", blank=True, editable=False)

    class Meta:
        verbose_name = "cluster Proxmox"
        verbose_name_plural = "clusters Proxmox"

    def __str__(self):
        return self.name

    def api(self):
        from proxmoxer import ProxmoxAPI

        return ProxmoxAPI(
            self.host,
            port=self.port,
            user=self.user,
            token_name=self.token_name,
            token_value=self.token_value,
            verify_ssl=self.verify_ssl,
            timeout=15,
        )


class ProxmoxGuest(models.Model):
    class Type(models.TextChoices):
        QEMU = "qemu", "VM"
        LXC = "lxc", "LXC"

    cluster = models.ForeignKey(
        ProxmoxCluster, on_delete=models.CASCADE, related_name="guests", verbose_name="cluster"
    )
    vmid = models.PositiveIntegerField("VMID")
    type = models.CharField("tipo", max_length=10, choices=Type)
    name = models.CharField("nome", max_length=200, blank=True)
    node = models.CharField("nó", max_length=100, blank=True)
    status = models.CharField("estado", max_length=30, blank=True)
    cpus = models.PositiveIntegerField("vCPUs", null=True, blank=True)
    memory = models.BigIntegerField("RAM (bytes)", null=True, blank=True)
    disk = models.BigIntegerField("disco (bytes)", null=True, blank=True)
    tags = models.CharField("tags", max_length=300, blank=True)
    is_template = models.BooleanField("template", default=False)
    missing = models.BooleanField(
        "removida", default=False, help_text="Não apareceu na última sincronização."
    )
    last_seen = models.DateTimeField("vista em", null=True, blank=True)

    subscription_item = models.ForeignKey(
        SubscriptionItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proxmox_guests",
        verbose_name="produto da subscrição",
    )
    notes = models.TextField("notas", blank=True)

    class Meta:
        verbose_name = "VM / LXC"
        verbose_name_plural = "VMs / LXCs"
        ordering = ["vmid"]
        constraints = [
            models.UniqueConstraint(fields=["cluster", "vmid"], name="unique_cluster_vmid"),
        ]

    def __str__(self):
        return f"{self.vmid} {self.name}".strip()

    @property
    def short_label(self) -> str:
        return f"{self.get_type_display()} {self.vmid} {self.name}".strip()
