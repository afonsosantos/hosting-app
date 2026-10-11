from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.smtp import EmailBackend


class SMTPNotConfigured(Exception):
    pass


class SettingsEmailBackend(EmailBackend):
    """Backend SMTP que lê as credenciais das Configurações guardadas na base de dados."""

    def __init__(self, fail_silently=False, **kwargs):
        from .models import SiteSettings

        cfg = SiteSettings.load()
        if not cfg.smtp_host:
            raise SMTPNotConfigured("O servidor SMTP não está configurado.")
        super().__init__(
            host=cfg.smtp_host,
            port=cfg.smtp_port,
            username=cfg.smtp_username or None,
            password=cfg.smtp_password or None,
            use_tls=cfg.smtp_security == "starttls",
            use_ssl=cfg.smtp_security == "ssl",
            timeout=cfg.smtp_timeout,
            fail_silently=fail_silently,
            **kwargs,
        )


def build_email(
    subject, body, to, *, html=None, attachments=(), bcc=None
) -> EmailMultiAlternatives:
    """Email com `body` em texto simples e, opcionalmente, versão `html`."""
    from .models import SiteSettings

    cfg = SiteSettings.load()
    if not cfg.smtp_configured:
        raise SMTPNotConfigured("Configure o servidor SMTP e o remetente nas Configurações.")
    message = EmailMultiAlternatives(
        subject=" ".join(subject.split()),  # assunto numa só linha
        body=body,
        from_email=cfg.from_email,
        to=to,
        bcc=[b for b in [cfg.notification_bcc if bcc is None else bcc] if b],
        reply_to=[cfg.email_reply_to] if cfg.email_reply_to else None,
    )
    if html:
        message.attach_alternative(html, "text/html")
    for filename, content, mimetype in attachments:
        message.attach(filename, content, mimetype)
    return message
