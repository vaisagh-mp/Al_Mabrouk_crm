import datetime
from decimal import Decimal
from io import StringIO
from django.contrib.auth.models import User
from django.core.management import call_command
from django.template import Context, Template
from django.test import TestCase, Client
from django.urls import reverse

from Admin.models import Employee, Project, Team
from finance.currency import BASE_CURRENCY, CURRENCY_CHOICES, get_rate, to_aed, format_money
from finance.models import ExchangeRate


class NormalizeProjectCurrencyCommandTests(TestCase):
    def test_normalize_and_dry_run(self):
        # Create test projects with various currency spellings
        p_aed = Project.objects.create(name='P_AED', currency_code='aed')
        p_dhs = Project.objects.create(name='P_DHS', currency_code='Dirham')
        p_blank = Project.objects.create(name='P_BLANK', currency_code='')
        p_usd = Project.objects.create(name='P_USD', currency_code='usd')
        p_dollar = Project.objects.create(name='P_DOLLAR', currency_code='$')
        p_eur = Project.objects.create(name='P_EUR', currency_code='Euro')
        p_inr = Project.objects.create(name='P_INR', currency_code='rs')
        p_unknown = Project.objects.create(name='P_UNKNOWN', currency_code='XYZ')

        # 1. Run with --dry-run
        out = StringIO()
        call_command('normalize_project_currency', '--dry-run', stdout=out)
        output = out.getvalue()

        self.assertIn('[Dry Run]', output)
        self.assertIn('P_UNKNOWN', output)
        self.assertIn('XYZ', output)

        # Verify nothing was changed in the database
        p_aed.refresh_from_db()
        self.assertEqual(p_aed.currency_code, 'aed')
        p_unknown.refresh_from_db()
        self.assertEqual(p_unknown.currency_code, 'XYZ')

        # 2. Run without --dry-run
        out = StringIO()
        call_command('normalize_project_currency', stdout=out)
        output = out.getvalue()

        self.assertIn('Successfully updated', output)

        p_aed.refresh_from_db()
        self.assertEqual(p_aed.currency_code, 'AED')
        p_dhs.refresh_from_db()
        self.assertEqual(p_dhs.currency_code, 'AED')
        p_blank.refresh_from_db()
        self.assertEqual(p_blank.currency_code, 'AED')
        p_usd.refresh_from_db()
        self.assertEqual(p_usd.currency_code, 'USD')
        p_dollar.refresh_from_db()
        self.assertEqual(p_dollar.currency_code, 'USD')
        p_eur.refresh_from_db()
        self.assertEqual(p_eur.currency_code, 'EUR')
        p_inr.refresh_from_db()
        self.assertEqual(p_inr.currency_code, 'INR')
        p_unknown.refresh_from_db()
        self.assertEqual(p_unknown.currency_code, 'XYZ')  # Left unchanged


class CurrencyRateTests(TestCase):
    def test_get_rate_aed(self):
        # AED always returns Decimal('1') on any date
        self.assertEqual(get_rate('AED', datetime.date(2026, 1, 1)), Decimal('1'))
        self.assertEqual(get_rate('aed', datetime.date(2010, 1, 1)), Decimal('1'))
        self.assertEqual(get_rate('AED'), Decimal('1'))

    def test_get_rate_usd_seeded(self):
        # USD seeded migration: 3.672500 from 2020-01-01
        rate = get_rate('USD', datetime.date(2026, 6, 1))
        self.assertIsNotNone(rate)
        self.assertEqual(rate, Decimal('3.672500'))

    def test_get_rate_effective_dates_and_inactive(self):
        # Before any USD rate
        self.assertIsNone(get_rate('USD', datetime.date(2019, 12, 31)))

        # Create two new USD rates
        rate_jan = ExchangeRate.objects.create(
            currency='USD',
            rate_to_aed=Decimal('3.670000'),
            effective_from=datetime.date(2026, 1, 1),
            notes='Jan 2026 rate',
            is_active=True,
        )
        rate_oct = ExchangeRate.objects.create(
            currency='USD',
            rate_to_aed=Decimal('3.680000'),
            effective_from=datetime.date(2026, 10, 1),
            notes='Oct 2026 rate',
            is_active=True,
        )

        # 2026-05-15 gives 3.67
        self.assertEqual(get_rate('USD', datetime.date(2026, 5, 15)), Decimal('3.670000'))
        # 2026-10-15 gives 3.68
        self.assertEqual(get_rate('USD', datetime.date(2026, 10, 15)), Decimal('3.680000'))

        # Inactive rate ignored
        rate_oct.is_active = False
        rate_oct.save()
        # Should fall back to Jan 2026 rate
        self.assertEqual(get_rate('USD', datetime.date(2026, 10, 15)), Decimal('3.670000'))

    def test_to_aed_rounding(self):
        # 1000 USD * 3.6725 = 3672.50
        res1 = to_aed(Decimal('1000'), Decimal('3.6725'))
        self.assertEqual(res1, Decimal('3672.50'))

        # 333.33 * 3.6725 = 1224.154425 -> 1224.15
        res2 = to_aed(Decimal('333.33'), Decimal('3.6725'))
        self.assertEqual(res2, Decimal('1224.15'))

        # Test rounding boundary 0.005 -> 0.01
        res3 = to_aed(Decimal('1'), Decimal('1.005'))
        self.assertEqual(res3, Decimal('1.01'))

    def test_format_money_and_template_filter(self):
        self.assertEqual(format_money(1000, 'USD'), 'USD 1,000.00')
        self.assertEqual(format_money(Decimal('1234567.89'), 'AED'), 'AED 1,234,567.89')
        self.assertEqual(format_money(None, 'EUR'), 'EUR 0.00')

        t = Template("{% load currency_tags %}{{ val|money:cur }}")
        rendered = t.render(Context({'val': Decimal('5000'), 'cur': 'USD'}))
        self.assertEqual(rendered, 'USD 5,000.00')


class ExchangeRatesViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        # 1. Superuser
        self.admin = User.objects.create_superuser('admin_cur', 'admin@example.com', 'pass1234')

        # 2. Accountant
        self.acc_user = User.objects.create_user('acc_user', 'acc@example.com', 'pass1234')
        self.acc_emp = Employee.objects.create(
            user=self.acc_user,
            is_accountant=True,
            date_of_join=datetime.date(2026, 1, 1),
        )

        # 3. Regular non-finance employee
        self.reg_user = User.objects.create_user('reg_user', 'reg@example.com', 'pass1234')
        self.reg_emp = Employee.objects.create(
            user=self.reg_user,
            is_employee=True,
            date_of_join=datetime.date(2026, 1, 1),
        )

    def test_non_finance_user_gets_403(self):
        self.client.force_login(self.reg_user)
        resp = self.client.get(reverse('fin_exchange_rates'))
        self.assertEqual(resp.status_code, 403)

        resp_add = self.client.post(reverse('fin_exchange_rate_add'), {
            'currency': 'EUR',
            'rate_to_aed': '4.000000',
            'effective_from': '2026-11-01',
            'is_active': True,
        })
        self.assertEqual(resp_add.status_code, 403)

    def test_accountant_can_view_and_filter(self):
        self.client.force_login(self.acc_user)
        resp = self.client.get(reverse('fin_exchange_rates'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Exchange Rates Settings')

        # Filter by USD
        resp_usd = self.client.get(reverse('fin_exchange_rates') + '?currency=USD')
        self.assertEqual(resp_usd.status_code, 200)
        self.assertContains(resp_usd, '3.6725')

    def test_accountant_can_add_and_edit(self):
        self.client.force_login(self.acc_user)
        # Add EUR rate
        post_data = {
            'currency': 'EUR',
            'rate_to_aed': '4.012345',
            'effective_from': '2026-11-01',
            'notes': 'Test EUR rate',
            'is_active': 'on',
        }
        resp = self.client.post(reverse('fin_exchange_rate_add'), post_data)
        self.assertRedirects(resp, reverse('fin_exchange_rates'))

        eur_rate = ExchangeRate.objects.get(currency='EUR', effective_from=datetime.date(2026, 11, 1))
        self.assertEqual(eur_rate.rate_to_aed, Decimal('4.012345'))
        self.assertEqual(eur_rate.created_by, self.acc_user)

        # Edit EUR rate
        edit_data = {
            'currency': 'EUR',
            'rate_to_aed': '4.050000',
            'effective_from': '2026-11-01',
            'notes': 'Updated EUR rate',
            'is_active': 'on',
        }
        resp_edit = self.client.post(reverse('fin_exchange_rate_edit', args=[eur_rate.id]), edit_data)
        self.assertRedirects(resp_edit, reverse('fin_exchange_rates'))

        eur_rate.refresh_from_db()
        self.assertEqual(eur_rate.rate_to_aed, Decimal('4.050000'))

    def test_rate_zero_or_negative_rejected(self):
        self.client.force_login(self.acc_user)
        # Rate 0
        resp_zero = self.client.post(reverse('fin_exchange_rate_add'), {
            'currency': 'EUR',
            'rate_to_aed': '0.000000',
            'effective_from': '2026-11-01',
        })
        self.assertEqual(resp_zero.status_code, 200)
        self.assertIn('rate_to_aed', resp_zero.context['form'].errors)
        self.assertIn('Rate must be greater than zero.', resp_zero.context['form'].errors['rate_to_aed'])

        # Negative rate
        resp_neg = self.client.post(reverse('fin_exchange_rate_add'), {
            'currency': 'EUR',
            'rate_to_aed': '-2.500000',
            'effective_from': '2026-11-01',
        })
        self.assertEqual(resp_neg.status_code, 200)
        self.assertIn('rate_to_aed', resp_neg.context['form'].errors)
        self.assertIn('Rate must be greater than zero.', resp_neg.context['form'].errors['rate_to_aed'])

    def test_duplicate_currency_and_date_rejected(self):
        self.client.force_login(self.acc_user)
        # Attempt to add rate for USD on 2020-01-01 (seeded rate already exists)
        resp = self.client.post(reverse('fin_exchange_rate_add'), {
            'currency': 'USD',
            'rate_to_aed': '3.672500',
            'effective_from': '2020-01-01',
        })
        self.assertEqual(resp.status_code, 200)
        form_errors = resp.context['form'].errors
        self.assertTrue('__all__' in form_errors or 'effective_from' in form_errors)

    def test_accountant_can_toggle_active(self):
        self.client.force_login(self.acc_user)
        rate = ExchangeRate.objects.filter(currency='USD').first()
        initial_active = rate.is_active

        resp = self.client.post(reverse('fin_exchange_rate_toggle', args=[rate.id]))
        self.assertRedirects(resp, reverse('fin_exchange_rates'))

        rate.refresh_from_db()
        self.assertEqual(rate.is_active, not initial_active)

    def test_only_superuser_can_delete(self):
        rate = ExchangeRate.objects.create(
            currency='INR',
            rate_to_aed=Decimal('0.044000'),
            effective_from=datetime.date(2026, 1, 1),
            is_active=True,
        )

        # 1. Accountant attempt to delete -> 403
        self.client.force_login(self.acc_user)
        resp_acc = self.client.post(reverse('fin_exchange_rate_delete', args=[rate.id]))
        self.assertEqual(resp_acc.status_code, 403)
        self.assertTrue(ExchangeRate.objects.filter(id=rate.id).exists())

        # 2. Superuser delete -> success
        self.client.force_login(self.admin)
        resp_admin = self.client.post(reverse('fin_exchange_rate_delete', args=[rate.id]))
        self.assertRedirects(resp_admin, reverse('fin_exchange_rates'))
        self.assertFalse(ExchangeRate.objects.filter(id=rate.id).exists())


class ProjectCurrencyFormAndViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        # Admin
        self.admin = User.objects.create_superuser('admin_pcur', 'admin_pcur@example.com', 'pass1234')

        # Manager
        self.mgr_user = User.objects.create_user('mgr_pcur', 'mgr_pcur@example.com', 'pass1234')
        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user,
            is_manager=True,
            rank='Operations Manager',
            salary=Decimal('8000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )

        # Administration
        self.adm_user = User.objects.create_user('adm_pcur', 'adm_pcur@example.com', 'pass1234')
        self.adm_emp = Employee.objects.create(
            user=self.adm_user,
            is_administration=True,
            rank='Admin Officer',
            salary=Decimal('6000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )

    def base_data(self, **extra):
        data = {
            'name': 'Sample Project',
            'code': 'AMMS-P-001',
            'service_reference': 'SR-001',
            'client_name': 'Global Maritime',
            'vessel_name': 'MV Ocean',
            'purchase_and_expenses': '1000.00',
            'invoice_amount': '5000.00',
            'currency_code': 'AED',
            'status': 'PENDING',
            'category': 'ANCHORAGE',
            'manager': str(self.mgr_emp.id),
            'priority': 'MEDIUM',
            'assigned_date': '2026-11-01',
        }
        data.update(extra)
        return data

    def test_admin_create_project_with_currency(self):
        self.client.force_login(self.admin)
        data = self.base_data(name='Admin USD Project', code='AMMS-P-USD1', currency_code='USD')
        resp = self.client.post(reverse('add_project'), data)
        self.assertEqual(resp.status_code, 302)
        project = Project.objects.get(code='AMMS-P-USD1')
        self.assertEqual(project.currency_code, 'USD')

    def test_manager_create_project_with_currency(self):
        self.client.force_login(self.mgr_user)
        data = self.base_data(name='Manager EUR Project', code='AMMS-P-EUR1', currency_code='EUR')
        resp = self.client.post(reverse('manager-add-project'), data)
        self.assertEqual(resp.status_code, 302)
        project = Project.objects.get(code='AMMS-P-EUR1')
        self.assertEqual(project.currency_code, 'EUR')

    def test_administration_create_project_with_currency(self):
        self.client.force_login(self.adm_user)
        data = self.base_data(name='Administration INR Project', code='AMMS-P-INR1', currency_code='INR')
        resp = self.client.post(reverse('admstrn_add_project'), data)
        self.assertEqual(resp.status_code, 302)
        project = Project.objects.get(code='AMMS-P-INR1')
        self.assertEqual(project.currency_code, 'INR')

    def test_invalid_currency_code_rejected(self):
        self.client.force_login(self.admin)
        data = self.base_data(name='Invalid Currency Project', code='AMMS-P-BAD', currency_code='XYZ')
        resp = self.client.post(reverse('add_project'), data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('currency_code', resp.context['form'].errors)
        self.assertFalse(Project.objects.filter(code='AMMS-P-BAD').exists())


class ProjectCompletionCurrencyBugFixTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser('admin_bf', 'admin_bf@example.com', 'pass1234')
        self.mgr_user = User.objects.create_user('mgr_bf', 'mgr_bf@example.com', 'pass1234')
        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user,
            is_manager=True,
            date_of_join=datetime.date(2026, 1, 1),
        )

        self.project_usd = Project.objects.create(
            name='USD Vessel Overhaul',
            code='AMMS-P-FIX1',
            category='ANCHORAGE',
            status='ONGOING',
            manager=self.mgr_emp,
            currency_code='USD',
        )

    def test_admin_completing_project_without_posting_currency_keeps_usd(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('update_team_admin_status', args=[self.project_usd.id]),
            {'status': 'COMPLETED'},
        )
        self.assertRedirects(resp, reverse('project-summary', args=[self.project_usd.id]))
        self.project_usd.refresh_from_db()
        self.assertEqual(self.project_usd.status, 'COMPLETED')
        self.assertEqual(self.project_usd.currency_code, 'USD')

    def test_manager_completing_project_without_posting_currency_keeps_usd(self):
        self.client.force_login(self.mgr_user)
        resp = self.client.post(
            reverse('update_team_manager_status', args=[self.project_usd.id]),
            {'status': 'COMPLETED'},
        )
        self.assertRedirects(resp, reverse('project-summary-view', args=[self.project_usd.id]))
        self.project_usd.refresh_from_db()
        self.assertEqual(self.project_usd.status, 'COMPLETED')
        self.assertEqual(self.project_usd.currency_code, 'USD')

    def test_completing_project_with_valid_currency_updates(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('update_team_admin_status', args=[self.project_usd.id]),
            {
                'status': 'COMPLETED',
                'invoice_amount': '5000.00',
                'currency_code': 'EUR',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.project_usd.refresh_from_db()
        self.assertEqual(self.project_usd.currency_code, 'EUR')

    def test_completing_project_with_invalid_currency_keeps_existing(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('update_team_admin_status', args=[self.project_usd.id]),
            {
                'status': 'COMPLETED',
                'currency_code': 'INVALID_CUR',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.project_usd.refresh_from_db()
        self.assertEqual(self.project_usd.currency_code, 'USD')
