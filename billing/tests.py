from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import BillingCycle, PricePlan, Product
from core.dates import add_months, parse_day_list
from core.models import SiteSettings
from customers.models import Customer

from .models import Invoice, Notification, Subscription, SubscriptionItem
from .notifications import run_notifications, send_contract, send_notification
from .pdf import render_contract_html
from .schedule import payment_schedule
from .services import (
    create_invoice_for_subscription,
    generate_due_invoices,
    mark_paid,
    subscription_created,
)

# os templates são renderizados na mesma; só a conversão para PDF (WeasyPrint) é simulada
FAKE_PDF = mock.patch("billing.pdf._html_to_pdf", return_value=b"%PDF-fake")


class DatesTests(TestCase):
    def test_add_months_clamps_end_of_month(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 11, 15), 3), date(2027, 2, 15))
        self.assertEqual(add_months(date(2028, 2, 29), 12), date(2029, 2, 28))

    def test_parse_day_list(self):
        self.assertEqual(parse_day_list(" 7, 3,x,3, 0"), [7, 3, 0])


class BillingTestCase(TestCase):
    def setUp(self):
        self.cfg = SiteSettings.load()
        self.cfg.smtp_host = "smtp.example.com"
        self.cfg.email_from_address = "faturacao@example.com"
        self.cfg.iban = "PT50 0000 0000 0000 0000 0000 0"
        self.cfg.save()
        self.customer = Customer.objects.create(name="Ana", email="ana@example.com")
        self.hosting = Product.objects.create(name="Alojamento Pro")
        PricePlan.objects.create(
            product=self.hosting, cycle=BillingCycle.ANNUAL, price=Decimal("120"), setup_fee=10
        )
        PricePlan.objects.create(product=self.hosting, cycle=BillingCycle.MONTHLY, price=12)
        self.domain = Product.objects.create(name="Domínio .pt", type="domain")
        PricePlan.objects.create(product=self.domain, cycle=BillingCycle.ANNUAL, price=15)

    def make_subscription(self, start, hosting_cycle=BillingCycle.ANNUAL):
        """Alojamento (anual ou mensal) + domínio (sempre anual), ambos a vencer em `start`."""
        sub = Subscription.objects.create(customer=self.customer, start_date=start)
        SubscriptionItem.objects.create(
            subscription=sub,
            product=self.hosting,
            description="a.pt",
            cycle=hosting_cycle,
            next_due_date=start,
        )
        # sem periodicidade: o domínio só tem plano anual, é escolhido automaticamente
        SubscriptionItem.objects.create(
            subscription=sub, product=self.domain, description="a.pt", next_due_date=start
        )
        return sub


class InvoiceGenerationTests(BillingTestCase):
    def test_creates_invoice_and_advances_due_date(self):
        sub = self.make_subscription(date(2026, 3, 1))
        invoice = create_invoice_for_subscription(sub, send_email=False)

        self.assertEqual(invoice.total, Decimal("145.00"))  # 120 + 10 ativação + 15
        self.assertEqual(invoice.lines.count(), 3)
        self.assertEqual(
            (invoice.period_start, invoice.period_end), (date(2026, 3, 1), date(2027, 2, 28))
        )
        self.assertEqual(invoice.due_date, date(2026, 3, 1))
        self.assertEqual(sub.next_due_date, date(2027, 3, 1))

        second = create_invoice_for_subscription(sub, send_email=False)
        self.assertEqual(second.total, Decimal("135.00"))  # sem taxa de ativação

    def test_sequential_numbering_per_year(self):
        sub = self.make_subscription(date(2026, 3, 1))
        a = create_invoice_for_subscription(sub, send_email=False)
        b = create_invoice_for_subscription(sub, send_email=False)
        self.assertEqual((a.sequence, b.sequence), (1, 2))
        self.assertEqual(a.number, f"NC {a.year}/0001")

    def test_generate_due_respects_horizon_and_catches_up(self):
        today = timezone.localdate()
        far = self.make_subscription(today + timedelta(days=60))
        late = self.make_subscription(
            add_months(today, -2) + timedelta(days=1), BillingCycle.MONTHLY
        )

        created = generate_due_invoices(today)

        self.assertFalse(far.invoices.exists())
        # 2 meses em atraso + o mês corrente, que vence dentro dos 15 dias de antecedência
        self.assertEqual(late.invoices.count(), 3)
        self.assertEqual(len(created), 3)
        self.assertEqual(generate_due_invoices(today), [])

    def test_cancelled_and_ended_subscriptions_are_not_billed(self):
        today = timezone.localdate()
        cancelled = self.make_subscription(today)
        cancelled.status = Subscription.Status.CANCELLED
        cancelled.save()
        ended = self.make_subscription(today, BillingCycle.MONTHLY)
        ended.end_date = today - timedelta(days=1)
        ended.save()
        self.assertEqual(generate_due_invoices(today), [])

    def test_mixed_cycles_monthly_hosting_and_annual_domain(self):
        sub = self.make_subscription(date(2026, 1, 1), BillingCycle.MONTHLY)

        first = generate_due_invoices(date(2025, 12, 20))
        self.assertEqual([i.total for i in first], [Decimal("27.00")])  # 12 + 15
        self.assertIsNone(first[0].period_start)  # períodos diferentes por linha

        totals = []
        for month in range(2, 13):
            totals += [i.total for i in generate_due_invoices(date(2026, month - 1, 20))]
        self.assertEqual(totals, [Decimal("12.00")] * 11)  # só alojamento

        renewal = generate_due_invoices(date(2026, 12, 20))
        self.assertEqual([i.total for i in renewal], [Decimal("27.00")])  # volta o domínio
        self.assertEqual(sub.items.get(product=self.domain).next_due_date, date(2028, 1, 1))

    def test_items_due_in_same_window_share_one_document(self):
        sub = self.make_subscription(date(2026, 5, 1), BillingCycle.MONTHLY)
        domain = sub.items.get(product=self.domain)
        domain.next_due_date = date(2026, 5, 10)
        domain.save()

        [invoice] = generate_due_invoices(date(2026, 4, 25))  # janela até 10/05
        self.assertEqual(invoice.due_date, date(2026, 5, 1))
        self.assertEqual(invoice.lines.count(), 2)

    def test_item_cycle_must_have_plan(self):
        sub = Subscription.objects.create(customer=self.customer)
        item = SubscriptionItem(subscription=sub, product=self.hosting)  # tem 2 planos
        with self.assertRaises(ValidationError):
            item.full_clean()
        item.cycle = BillingCycle.QUARTERLY  # sem plano trimestral
        with self.assertRaises(ValidationError):
            item.full_clean()
        item.unit_price_override = Decimal("30")
        item.full_clean()

    def test_price_override(self):
        sub = self.make_subscription(date(2026, 3, 1))
        item = sub.items.get(product=self.domain)
        item.unit_price_override = Decimal("9.99")
        item.save()
        invoice = create_invoice_for_subscription(sub, send_email=False)
        self.assertEqual(invoice.total, Decimal("139.99"))


@FAKE_PDF
class NotificationTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.cfg.notifications_enabled = True
        self.cfg.reminder_days = "7,1"
        self.cfg.overdue_days = "3,10"
        self.cfg.save()

    def make_invoice(self, issue, due):
        invoice = Invoice.objects.create(customer=self.customer, issue_date=issue, due_date=due)
        invoice.lines.create(description="Serviço", unit_price=50)
        invoice.recalculate_total()
        return invoice

    def test_issue_email_is_sent_with_pdf_and_bank_details(self, _pdf):
        sub = self.make_subscription(timezone.localdate() + timedelta(days=10))
        generate_due_invoices()
        run_notifications()

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["ana@example.com"])
        self.assertIn("PT50 0000", message.body)
        self.assertEqual(message.attachments[0].mimetype, "application/pdf")
        self.assertFalse(sub.invoices.get().email_pending)

        html, mimetype = message.alternatives[0]
        self.assertEqual(mimetype, "text/html")
        for expected in ["PT50 0000", "Alojamento Pro — a.pt", "145,00 €", "Documento em PDF"]:
            self.assertIn(expected, html)

    def test_email_amounts_in_portuguese_even_with_english_request(self, _pdf):
        from django.utils import translation

        invoice = self.make_invoice(issue=date(2026, 6, 1), due=date(2026, 6, 30))
        with translation.override("en"):
            send_notification(invoice, Notification.Kind.ISSUE)
        self.assertIn("50,00 €", mail.outbox[0].body)

    def test_reminders_sent_once_per_threshold(self, _pdf):
        due = date(2026, 6, 30)
        self.make_invoice(issue=due - timedelta(days=15), due=due)

        run_notifications(due - timedelta(days=10))
        self.assertEqual(len(mail.outbox), 0)
        run_notifications(due - timedelta(days=7))
        run_notifications(due - timedelta(days=6))  # já enviado para o limiar 7
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("daqui a 7 dias", mail.outbox[0].body)
        run_notifications(due)  # limiar 1 atingido (o agendador falhou o dia anterior)
        self.assertEqual(len(mail.outbox), 2)

    def test_skips_thresholds_already_past_at_issue(self, _pdf):
        due = date(2026, 6, 30)
        self.make_invoice(issue=due - timedelta(days=3), due=due)
        run_notifications(due - timedelta(days=2))
        self.assertEqual(len(mail.outbox), 0)

    def test_overdue_notices(self, _pdf):
        due = date(2026, 6, 30)
        invoice = self.make_invoice(issue=due - timedelta(days=15), due=due)
        Notification.objects.create(
            invoice=invoice, kind="reminder", offset_days=1, recipient="x", success=True
        )

        run_notifications(due + timedelta(days=2))
        self.assertEqual(len(mail.outbox), 0)
        run_notifications(due + timedelta(days=4))
        run_notifications(due + timedelta(days=5))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Em atraso há 4 dias", mail.outbox[0].body)
        html = mail.outbox[0].alternatives[0].content
        self.assertIn("Em atraso há 4 dias", html)
        self.assertIn("#b91c1c", html)  # destaque vermelho

    def test_paid_invoices_get_confirmation_and_no_more_reminders(self, _pdf):
        due = date(2026, 6, 30)
        invoice = self.make_invoice(issue=due - timedelta(days=15), due=due)
        self.assertTrue(mark_paid(invoice, paid_on=due, reference="TRF 123"))
        self.assertFalse(mark_paid(invoice))  # já estava paga
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.paid_on), (Invoice.Status.PAID, due))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Pagamento recebido", mail.outbox[0].subject)
        run_notifications(due + timedelta(days=20))
        self.assertEqual(len(mail.outbox), 1)

    def test_subscription_plan_email(self, _pdf):
        sub = self.make_subscription(date(2026, 1, 1), BillingCycle.MONTHLY)
        self.assertTrue(subscription_created(sub))
        body = mail.outbox[0].body
        self.assertIn("01/01/2026 — 27,00 €", body)  # alojamento + domínio
        self.assertIn("01/02/2026 — 12,00 €", body)
        self.assertIn("PT50 0000", body)
        self.assertEqual(sub.notifications.get().kind, Notification.Kind.SUBSCRIPTION)
        html = mail.outbox[0].alternatives[0].content
        self.assertIn("Plano de pagamentos — próximos 12 meses", html)
        self.assertIn("27,00 €", html)
        [attachment] = mail.outbox[0].attachments
        self.assertEqual(attachment.filename, f"plano_pagamentos_{sub.pk}.pdf")
        plan_html = _pdf.call_args.args[0]
        self.assertIn("Plano de pagamentos", plan_html)
        self.assertIn("27,00 €", plan_html)

        self.cfg.notify_on_subscription = False
        self.cfg.save()
        self.assertFalse(subscription_created(sub))

    def test_contract_email(self, _pdf):
        sub = self.make_subscription(date(2026, 1, 1), BillingCycle.MONTHLY)
        log = send_contract(sub)
        self.assertTrue(log.success)
        self.assertEqual(log.kind, Notification.Kind.CONTRACT)
        message = mail.outbox[0]
        self.assertIn("Contrato de prestação de serviços", message.subject)
        self.assertEqual(
            [a.filename for a in message.attachments],
            [f"contrato_{sub.pk}.pdf", f"plano_pagamentos_{sub.pk}.pdf"],
        )
        self.assertIn("Contrato e plano de pagamentos em PDF", message.alternatives[0].content)

    def test_contract_document(self, _pdf):
        sub = self.make_subscription(date(2026, 1, 1))  # alojamento anual: taxa de ativação 10 €
        self.cfg.company_name = "Silva & Filhos"
        self.cfg.contract_terms = "1. Teste\n{{ company.company_name }} e {{ customer.name }}."
        self.cfg.save()
        html = render_contract_html(sub)
        # condições gerais renderizadas como modelo, com o título a negrito e escapadas uma só vez
        self.assertIn("<strong>1. Teste</strong><br>Silva &amp; Filhos e Ana.", html)
        self.assertIn("não tem termo", html)
        self.assertIn("Taxa de ativação", html)
        self.assertIn("Anexo — Plano de pagamentos", html)
        self.assertIn("145,00 €", html)  # 1.ª cobrança: 120 + 10 de ativação + 15 do domínio
        sub.end_date = date(2026, 12, 31)
        sub.save()
        self.assertIn("termina a <strong>31/12/2026</strong>", render_contract_html(sub))

    def test_contract_terms_must_be_valid_template(self, _pdf):
        self.cfg.contract_terms = "{% if %}"
        with self.assertRaises(ValidationError):
            self.cfg.full_clean()

    def test_disabled_globally_or_per_customer(self, _pdf):
        due = date(2026, 6, 30)
        self.make_invoice(issue=due - timedelta(days=15), due=due)
        self.customer.notify = False
        self.customer.save()
        run_notifications(due - timedelta(days=7))
        self.customer.notify = True
        self.customer.save()
        self.cfg.notifications_enabled = False
        self.cfg.save()
        run_notifications(due - timedelta(days=7))
        self.assertEqual(len(mail.outbox), 0)

    def test_smtp_failure_is_logged(self, _pdf):
        self.cfg.smtp_host = ""
        self.cfg.save()
        invoice = self.make_invoice(issue=date(2026, 6, 1), due=date(2026, 6, 30))
        invoice.email_pending = True
        invoice.save()
        [log] = run_notifications(date(2026, 6, 2))
        self.assertFalse(log.success)
        invoice.refresh_from_db()
        self.assertTrue(invoice.email_pending)  # volta a tentar na próxima execução


class AdminSmokeTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))

    def test_pages_render(self):
        sub = self.make_subscription(date(2026, 3, 1))
        invoice = create_invoice_for_subscription(sub, send_email=False)
        for url in [
            reverse("admin:index"),
            reverse("admin:customers_customer_changelist"),
            reverse("admin:customers_customer_change", args=[self.customer.pk]),
            reverse("admin:billing_subscription_changelist"),
            reverse("admin:billing_subscription_change", args=[sub.pk]),
            reverse("admin:billing_subscription_add"),
            reverse("admin:billing_subscription_contract", args=[sub.pk]),
            reverse("admin:billing_invoice_changelist") + "?due=overdue",
            reverse("admin:billing_invoice_changelist") + "?q=NC+2026/1",
            reverse("admin:billing_invoice_change", args=[invoice.pk]),
            reverse("admin:billing_invoice_view", args=[invoice.pk]),
            reverse("admin:catalog_product_changelist"),
            reverse("admin:proxmox_proxmoxguest_changelist"),
            reverse("admin:proxmox_proxmoxcluster_add"),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertRedirects(
            self.client.get(reverse("admin:core_sitesettings_changelist")),
            reverse("admin:core_sitesettings_change", args=[1]),
        )
        self.assertEqual(
            self.client.get(reverse("admin:core_sitesettings_change", args=[1])).status_code, 200
        )

    def test_payment_schedule(self):
        sub = self.make_subscription(date(2026, 1, 1), BillingCycle.MONTHLY)
        charges = payment_schedule(sub)
        self.assertEqual(len(charges), 12)
        self.assertEqual([c.total for c in charges], [Decimal("27.00")] + [Decimal("12.00")] * 11)
        page = self.client.get(reverse("admin:billing_subscription_change", args=[sub.pk]))
        self.assertContains(page, "27,00 €")

    def subscription_form_data(self, **extra):
        data = {
            "customer": self.customer.pk,
            "name": "Site a.pt",
            "status": "active",
            "start_date": "2026-11-01",
            "items-TOTAL_FORMS": "2",
            "items-INITIAL_FORMS": "0",
            "items-0-product": self.hosting.pk,
            "items-0-cycle": BillingCycle.MONTHLY,
            "items-0-quantity": "1",
            "items-1-product": self.domain.pk,  # periodicidade automática (só tem anual)
            "items-1-quantity": "1",
        }
        for prefix in ("invoices", "notifications"):
            data |= {f"{prefix}-TOTAL_FORMS": "0", f"{prefix}-INITIAL_FORMS": "0"}
        return data | extra

    @FAKE_PDF
    def test_create_subscription_in_admin_sends_plan(self, _pdf):
        self.cfg.notifications_enabled = True
        self.cfg.save()
        url = reverse("admin:billing_subscription_add")
        response = self.client.post(url, self.subscription_form_data())

        sub = Subscription.objects.get(name="Site a.pt")
        self.assertRedirects(response, reverse("admin:billing_subscription_change", args=[sub.pk]))
        self.assertEqual(
            sorted(sub.items.values_list("cycle", flat=True)), [BillingCycle.MONTHLY, 12]
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Plano de pagamentos", mail.outbox[0].subject)

    @FAKE_PDF
    def test_create_subscription_in_admin_sends_contract(self, _pdf):
        # é uma escolha explícita no formulário: envia mesmo com as notificações desligadas
        url = reverse("admin:billing_subscription_add")
        self.client.post(url, self.subscription_form_data(send_contract="on"))

        sub = Subscription.objects.get(name="Site a.pt")
        self.assertEqual(len(mail.outbox), 1)  # só o contrato (que já inclui o plano)
        self.assertIn("Contrato", mail.outbox[0].subject)
        self.assertEqual(sub.notifications.get().kind, Notification.Kind.CONTRACT)

    def test_subscription_pdfs(self):
        sub = self.make_subscription(date(2026, 3, 1))
        for name in ("billing_subscription_plan_pdf", "billing_subscription_contract_pdf"):
            with self.subTest(name):
                response = self.client.get(reverse(f"admin:{name}", args=[sub.pk]))
                self.assertEqual(response["Content-Type"], "application/pdf")
                self.assertTrue(response.content.startswith(b"%PDF"))

    def test_mark_paid_button(self):
        sub = self.make_subscription(date(2026, 3, 1))
        invoice = create_invoice_for_subscription(sub, send_email=False)
        response = self.client.post(reverse("admin:billing_invoice_pay", args=[invoice.pk]))
        self.assertRedirects(response, reverse("admin:billing_invoice_change", args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.paid_on, timezone.localdate())

    def test_pdf(self):
        sub = self.make_subscription(date(2026, 3, 1))
        invoice = create_invoice_for_subscription(sub, send_email=False)
        response = self.client.get(reverse("admin:billing_invoice_pdf", args=[invoice.pk]))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
