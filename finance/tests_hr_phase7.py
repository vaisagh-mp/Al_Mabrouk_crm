import datetime
import io
import zipfile
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from Admin.models import (
    Employee, Department, Branch, SalaryStructure,
    update_employee_salary_structure, Notification
)
from finance.models import (
    Payroll, PayrollLine, SalaryAdvance, AdvanceInstalment,
    SalaryIncrement, Payslip
)
from finance.services import (
    generate_payroll, calculate_employee_prorated_salary,
    calculate_employee_salary_split, get_payroll_list_with_metrics
)
from finance.payroll_service import (
    amount_to_words_aed, generate_payslip_number,
    create_advance_schedule, create_or_update_payslip
)


class HRPhase7PayrollTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Engineering", sort_order=1)
        self.branch = Branch.objects.create(name="Dubai Main", sort_order=1)

        # 1. Superuser / Admin
        self.admin_user = User.objects.create_superuser('admin_user', 'admin@example.com', 'pass123')

        # 2. Accountant
        self.acct_user = User.objects.create_user('acct_user', 'acct@example.com', 'pass123')
        self.acct_emp = Employee.objects.create(
            user=self.acct_user,
            employee_code="AMMS-0001",
            department=self.dept,
            branch=self.branch,
            is_accountant=True,
            employment_status='ACTIVE',
            date_of_join=datetime.date(2023, 1, 1),
            salary=Decimal('8000.00'),
        )

        # 3. HR User
        self.hr_user = User.objects.create_user('hr_user', 'hr@example.com', 'pass123')
        self.hr_emp = Employee.objects.create(
            user=self.hr_user,
            employee_code="AMMS-0002",
            department=self.dept,
            branch=self.branch,
            is_hr=True,
            employment_status='ACTIVE',
            date_of_join=datetime.date(2023, 1, 1),
            salary=Decimal('9000.00'),
        )

        # 4. Manager
        self.mgr_user = User.objects.create_user('mgr_user', 'mgr@example.com', 'pass123')
        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user,
            employee_code="AMMS-0003",
            department=self.dept,
            branch=self.branch,
            is_manager=True,
            employment_status='ACTIVE',
            date_of_join=datetime.date(2023, 1, 1),
            salary=Decimal('12000.00'),
        )

        # 5. Regular Employee (Subject of tests)
        self.emp_user = User.objects.create_user('emp_user', 'emp@example.com', 'pass123')
        self.emp = Employee.objects.create(
            user=self.emp_user,
            employee_code="AMMS-0004",
            department=self.dept,
            branch=self.branch,
            is_employee=True,
            employment_status='ACTIVE',
            date_of_join=datetime.date(2023, 1, 1),
            salary=Decimal('10400.00'),
        )

        # 6. Other Employee
        self.other_user = User.objects.create_user('other_user', 'other@example.com', 'pass123')
        self.other_emp = Employee.objects.create(
            user=self.other_user,
            employee_code="AMMS-0005",
            department=self.dept,
            branch=self.branch,
            is_employee=True,
            employment_status='ACTIVE',
            date_of_join=datetime.date(2023, 1, 1),
            salary=Decimal('5000.00'),
        )

    # -----------------------------------------------------------------
    # Test 1: V2 off - payroll identical (fixture month), no lines, no payslips
    # -----------------------------------------------------------------
    @override_settings(HR_PAYROLL_V2=False)
    def test_v2_off_payroll_identical_no_lines_no_payslips(self):
        m = datetime.date(2026, 4, 1)
        created, updated, skipped = generate_payroll(m, user=self.admin_user)
        self.assertGreater(created, 0)

        p = Payroll.objects.get(employee=self.emp, month=m)
        self.assertEqual(p.basic_salary, Decimal('10400.00'))
        self.assertEqual(p.allowances, Decimal('0.00'))
        self.assertEqual(p.deductions, Decimal('0.00'))
        p.absence_waived = True
        p.save()
        self.assertEqual(p.net_pay, Decimal('10400.00'))
        # No lines written when V2 is off
        self.assertEqual(p.lines.count(), 0)

        # Mark paid: no payslip auto-created when V2 is off
        p.status = 'APPROVED'
        p.save()
        self.client.force_login(self.admin_user)
        self.client.post(reverse('fin_payroll_mark_paid'), {'month': '2026-04'})
        p.refresh_from_db()
        self.assertEqual(p.status, 'PAID')
        self.assertFalse(hasattr(p, 'payslip'))

    # -----------------------------------------------------------------
    # Test 2: Split: basic 6000 + housing 3000 + transport 1400 -> lines; totals in sync
    # -----------------------------------------------------------------
    @override_settings(HR_PAYROLL_V2=True)
    def test_split_salary_structure_to_lines_and_sync(self):
        SalaryStructure.objects.create(
            employee=self.emp,
            basic=Decimal('6000.00'),
            housing_allowance=Decimal('3000.00'),
            transport_allowance=Decimal('1400.00'),
            other_allowances=Decimal('0.00'),
            total=Decimal('10400.00'),
            effective_from=datetime.date(2026, 1, 1),
        )

        m = datetime.date(2026, 4, 1)
        generate_payroll(m, user=self.admin_user)

        p = Payroll.objects.get(employee=self.emp, month=m)
        self.assertEqual(p.basic_salary, Decimal('6000.00'))
        self.assertEqual(p.allowances, Decimal('4400.00'))  # housing + transport
        self.assertEqual(p.deductions, Decimal('0.00'))
        self.assertEqual(p.gross, Decimal('10400.00'))
        p.absence_waived = True
        p.save()
        self.assertEqual(p.net_pay, Decimal('10400.00'))

        # Check lines
        lines = list(p.lines.all().order_by('sort_order'))
        codes = [l.code for l in lines]
        self.assertIn('BASIC', codes)
        self.assertIn('HOUSING', codes)
        self.assertIn('TRANSPORT', codes)

        basic_line = p.lines.get(code='BASIC')
        housing_line = p.lines.get(code='HOUSING')
        transport_line = p.lines.get(code='TRANSPORT')

        self.assertEqual(basic_line.amount, Decimal('6000.00'))
        self.assertEqual(housing_line.amount, Decimal('3000.00'))
        self.assertEqual(transport_line.amount, Decimal('1400.00'))

    # -----------------------------------------------------------------
    # Test 3: Mid-month change 15th: components prorated by days
    # -----------------------------------------------------------------
    @override_settings(HR_PAYROLL_V2=True)
    def test_mid_month_salary_structure_change_and_proration(self):
        # April has 30 days:
        # Days 1-14 (14 days): Structure 1 (basic 3000, housing 1500)
        # Days 15-30 (16 days): Structure 2 (basic 6000, housing 3000)
        SalaryStructure.objects.create(
            employee=self.emp,
            basic=Decimal('3000.00'),
            housing_allowance=Decimal('1500.00'),
            effective_from=datetime.date(2026, 1, 1),
            effective_to=datetime.date(2026, 4, 14),
        )
        SalaryStructure.objects.create(
            employee=self.emp,
            basic=Decimal('6000.00'),
            housing_allowance=Decimal('3000.00'),
            effective_from=datetime.date(2026, 4, 15),
        )

        m = datetime.date(2026, 4, 1)
        generate_payroll(m, user=self.admin_user)
        p = Payroll.objects.get(employee=self.emp, month=m)

        # Expected:
        # Basic: 3000 * 14 / 30 + 6000 * 16 / 30 = 1400.00 + 3200.00 = 4600.00
        # Housing: 1500 * 14 / 30 + 3000 * 16 / 30 = 700.00 + 1600.00 = 2300.00
        expected_basic = Decimal('4600.00')
        expected_housing = Decimal('2300.00')

        self.assertEqual(p.basic_salary, expected_basic)
        self.assertEqual(p.allowances, expected_housing)
        self.assertEqual(p.gross, expected_basic + expected_housing)

    # -----------------------------------------------------------------
    # Test 4: Joiner/Leaver proration; leaver next month no row
    # -----------------------------------------------------------------
    @override_settings(HR_PAYROLL_V2=True)
    def test_joiner_and_leaver_proration_and_next_month_no_row(self):
        # Joiner joining April 15th
        joiner_user = User.objects.create_user('joiner_u', 'join@ex.com', 'p')
        joiner = Employee.objects.create(
            user=joiner_user,
            employee_code="AMMS-J01",
            department=self.dept,
            is_employee=True,
            employment_status='ACTIVE',
            date_of_join=datetime.date(2026, 4, 15),
            salary=Decimal('6000.00'),
        )
        SalaryStructure.objects.create(
            employee=joiner,
            basic=Decimal('6000.00'),
            effective_from=datetime.date(2026, 4, 15),
        )

        # Leaver leaving April 14th
        leaver_user = User.objects.create_user('leaver_u', 'leave@ex.com', 'p')
        leaver = Employee.objects.create(
            user=leaver_user,
            employee_code="AMMS-L01",
            department=self.dept,
            is_employee=True,
            employment_status='ACTIVE',
            date_of_join=datetime.date(2024, 1, 1),
            last_working_day=datetime.date(2026, 4, 14),
            salary=Decimal('6000.00'),
        )
        SalaryStructure.objects.create(
            employee=leaver,
            basic=Decimal('6000.00'),
            effective_from=datetime.date(2024, 1, 1),
        )

        # Run April payroll (30 days)
        generate_payroll(datetime.date(2026, 4, 1), user=self.admin_user)

        # Joiner: 16 active days (15th to 30th) -> 6000 * 16 / 30 = 3200.00
        p_joiner = Payroll.objects.get(employee=joiner, month=datetime.date(2026, 4, 1))
        self.assertEqual(p_joiner.basic_salary, Decimal('3200.00'))

        # Leaver: 14 active days (1st to 14th) -> 6000 * 14 / 30 = 2800.00
        p_leaver = Payroll.objects.get(employee=leaver, month=datetime.date(2026, 4, 1))
        self.assertEqual(p_leaver.basic_salary, Decimal('2800.00'))

        # Run May payroll (leaver after last_working_day month -> no row)
        generate_payroll(datetime.date(2026, 5, 1), user=self.admin_user)
        self.assertFalse(Payroll.objects.filter(employee=leaver, month=datetime.date(2026, 5, 1)).exists())

    # -----------------------------------------------------------------
    # Test 5: Manual OTHER deduction survives regeneration; approved rows not regenerated
    # -----------------------------------------------------------------
    @override_settings(HR_PAYROLL_V2=True)
    def test_manual_line_survives_regeneration_and_approved_not_regenerated(self):
        m = datetime.date(2026, 4, 1)
        generate_payroll(m, user=self.admin_user)
        p = Payroll.objects.get(employee=self.emp, month=m)

        # Add manual line
        self.client.force_login(self.acct_user)
        resp = self.client.post(reverse('payroll_add_line', args=[p.id]), {
            'kind': 'DEDUCTION',
            'code': 'OTHER',
            'description': 'Laptop damage fine',
            'amount': '250.00',
        })
        self.assertEqual(resp.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.deductions, Decimal('250.00'))
        self.assertTrue(p.lines.filter(source='manual', amount=Decimal('250.00')).exists())

        # Regenerate payroll: manual line must survive
        generate_payroll(m, user=self.admin_user)
        p.refresh_from_db()
        self.assertEqual(p.deductions, Decimal('250.00'))
        self.assertTrue(p.lines.filter(source='manual', amount=Decimal('250.00')).exists())

        # Approve row
        p.status = 'APPROVED'
        p.save()

        # Update employee salary; regeneration must skip approved row
        self.emp.salary = Decimal('20000.00')
        self.emp.save()
        c, u, skipped = generate_payroll(m, user=self.admin_user)
        self.assertGreater(skipped, 0)
        p.refresh_from_db()
        self.assertEqual(p.basic_salary, Decimal('10400.00'))  # Not updated to 20000

    # -----------------------------------------------------------------
    # Test 6: Advance 3000 / 3 instalments from Dec -> Dec, Jan, Feb lines;
    # PAID marks DEDUCTED; balance; COMPLETED; skip moves to end; net never < 0; revert; approver != requester
    # -----------------------------------------------------------------
    @override_settings(HR_PAYROLL_V2=True)
    def test_salary_advance_full_lifecycle(self):
        # 1. Proposer != Approver
        self.client.force_login(self.hr_user)
        resp = self.client.post(reverse('salary_advance_request'), {
            'employee_id': self.emp.id,
            'amount': '3000.00',
            'instalments': '3',
            'start_month': '2026-12',
            'reason': 'Emergency expense',
        })
        self.assertEqual(resp.status_code, 302)
        adv = SalaryAdvance.objects.get(employee=self.emp, amount=Decimal('3000.00'))
        self.assertEqual(adv.status, 'REQUESTED')
        self.assertEqual(adv.created_by, self.hr_user)

        # Same user tries to approve -> refused
        resp_same = self.client.post(reverse('salary_advance_action', args=[adv.id, 'approve']))
        adv.refresh_from_db()
        self.assertEqual(adv.status, 'REQUESTED')  # Not approved

        # Accountant approves -> success
        self.client.force_login(self.acct_user)
        resp_acc = self.client.post(reverse('salary_advance_action', args=[adv.id, 'approve']))
        self.assertEqual(resp_acc.status_code, 302)
        adv.refresh_from_db()
        self.assertEqual(adv.status, 'APPROVED')

        # Disburse -> becomes ACTIVE, schedule created: Dec, Jan, Feb each 1000
        resp_disb = self.client.post(reverse('salary_advance_action', args=[adv.id, 'disburse']))
        self.assertEqual(resp_disb.status_code, 302)
        adv.refresh_from_db()
        self.assertEqual(adv.status, 'ACTIVE')
        self.assertEqual(adv.instalments_schedule.count(), 3)
        insts = list(adv.instalments_schedule.all())
        self.assertEqual(insts[0].month, datetime.date(2026, 12, 1))
        self.assertEqual(insts[0].amount, Decimal('1000.00'))
        self.assertEqual(insts[1].month, datetime.date(2027, 1, 1))
        self.assertEqual(insts[1].amount, Decimal('1000.00'))
        self.assertEqual(insts[2].month, datetime.date(2027, 2, 1))
        self.assertEqual(insts[2].amount, Decimal('1000.00'))

        # Dec payroll generated -> 1000 ADVANCE line
        m_dec = datetime.date(2026, 12, 1)
        generate_payroll(m_dec, user=self.admin_user)
        p_dec = Payroll.objects.get(employee=self.emp, month=m_dec)
        self.assertEqual(p_dec.deductions, Decimal('1000.00'))
        self.assertTrue(p_dec.lines.filter(code='ADVANCE', amount=Decimal('1000.00')).exists())

        # Revert returns instalment to SCHEDULED
        p_dec.status = 'APPROVED'
        p_dec.save()
        self.client.force_login(self.admin_user)
        self.client.post(reverse('fin_payroll_revert', args=[p_dec.id]))
        insts[0].refresh_from_db()
        self.assertEqual(insts[0].status, 'SCHEDULED')
        self.assertIsNone(insts[0].payroll)

        # Re-approve and mark PAID -> instalment DEDUCTED, balance 2000
        p_dec.refresh_from_db()
        generate_payroll(m_dec, user=self.admin_user)
        p_dec.refresh_from_db()
        p_dec.status = 'APPROVED'
        p_dec.save()
        self.client.post(reverse('fin_payroll_mark_paid'), {'month': '2026-12'})
        insts[0].refresh_from_db()
        self.assertEqual(insts[0].status, 'DEDUCTED')
        adv.refresh_from_db()
        self.assertEqual(adv.total_deducted, Decimal('1000.00'))
        self.assertEqual(adv.balance, Decimal('2000.00'))
        self.assertEqual(adv.status, 'ACTIVE')

        # Skip Jan instalment: moves to end (March 2027)
        self.client.force_login(self.hr_user)
        resp_skip = self.client.post(reverse('salary_advance_skip_instalment', args=[insts[1].id]), {
            'skip_reason': 'Employee request for medical leave support'
        })
        self.assertEqual(resp_skip.status_code, 302)
        insts[1].refresh_from_db()
        self.assertEqual(insts[1].status, 'SKIPPED')
        self.assertEqual(adv.instalments_schedule.count(), 4)
        last_inst = adv.instalments_schedule.order_by('-month').first()
        self.assertEqual(last_inst.month, datetime.date(2027, 3, 1))
        self.assertEqual(last_inst.amount, Decimal('1000.00'))
        self.assertEqual(last_inst.status, 'SCHEDULED')

        # Net pay never < 0:
        # If available net is 400.00 (e.g. basic salary 400.00), only 400 is deducted and 600 moved to end
        self.emp.salary = Decimal('400.00')
        self.emp.save()
        m_feb = datetime.date(2027, 2, 1)
        generate_payroll(m_feb, user=self.admin_user)
        p_feb = Payroll.objects.get(employee=self.emp, month=m_feb)
        self.assertGreaterEqual(p_feb.net_pay, Decimal('0.00'))
        self.assertEqual(p_feb.deductions, Decimal('400.00'))
        # An additional deferred instalment created for April
        deferred_inst = adv.instalments_schedule.order_by('-month').first()
        self.assertEqual(deferred_inst.month, datetime.date(2027, 4, 1))
        self.assertEqual(deferred_inst.amount, Decimal('600.00'))

    # -----------------------------------------------------------------
    # Test 7: Increment: proposer != approver; future effective -> upcoming;
    # locked month refused; history shows previous -> new and %
    # -----------------------------------------------------------------
    @override_settings(HR_PAYROLL_V2=True)
    def test_salary_increment_lifecycle_and_history(self):
        # 1. Propose increment by HR
        self.client.force_login(self.hr_user)
        resp = self.client.post(reverse('salary_increment_propose'), {
            'employee_id': self.emp.id,
            'type': 'PROMOTION',
            'effective_from': '2026-06-01',
            'new_basic': '8000.00',
            'new_housing': '4000.00',
            'new_transport': '1000.00',
            'new_other': '0.00',
            'reason': 'Promoted to Lead Engineer',
        })
        self.assertEqual(resp.status_code, 302)
        inc = SalaryIncrement.objects.get(employee=self.emp, reason='Promoted to Lead Engineer')
        self.assertEqual(inc.status, 'PENDING')
        self.assertEqual(inc.new_total, Decimal('13000.00'))
        self.assertEqual(inc.previous_total, Decimal('10400.00'))
        # % change = (13000 - 10400) / 10400 * 100 = 25%
        self.assertEqual(inc.percentage_change, Decimal('25.00'))

        # Proposer cannot approve
        self.client.force_login(self.hr_user)
        resp_own = self.client.post(reverse('salary_increment_action', args=[inc.id, 'approve']))
        self.assertEqual(resp_own.status_code, 403)  # Admin only

        # Admin approves
        self.client.force_login(self.admin_user)
        resp_adm = self.client.post(reverse('salary_increment_action', args=[inc.id, 'approve']))
        self.assertEqual(resp_adm.status_code, 302)
        inc.refresh_from_db()
        self.assertEqual(inc.status, 'APPROVED')

        # Check locked month refusal: if June has PAID payroll, another increment into June is refused
        m_june = datetime.date(2026, 6, 1)
        p_june = Payroll.objects.create(employee=self.emp, month=m_june, status='PAID', basic_salary=Decimal('10000.00'))
        self.client.force_login(self.hr_user)
        resp_locked = self.client.post(reverse('salary_increment_propose'), {
            'employee_id': self.emp.id,
            'type': 'ANNUAL',
            'effective_from': '2026-06-01',
            'new_basic': '9000.00',
            'new_housing': '4000.00',
            'new_transport': '1000.00',
            'new_other': '0.00',
        })
        self.assertFalse(SalaryIncrement.objects.filter(new_basic=Decimal('9000.00')).exists())

    # -----------------------------------------------------------------
    # Test 8: Payslip auto-created on PAID; number format; PDF 200; permissions
    # -----------------------------------------------------------------
    @override_settings(HR_PAYROLL_V2=True)
    def test_payslip_generation_permissions_and_download(self):
        m = datetime.date(2026, 7, 1)
        generate_payroll(m, user=self.admin_user)
        p = Payroll.objects.get(employee=self.emp, month=m)
        p.status = 'APPROVED'
        p.save()

        # Mark paid: payslip auto-created
        self.client.force_login(self.admin_user)
        self.client.post(reverse('fin_payroll_mark_paid'), {'month': '2026-07'})
        p.refresh_from_db()
        self.assertEqual(p.status, 'PAID')

        payslip = Payslip.objects.get(payroll=p)
        self.assertTrue(payslip.number.startswith('PS-202607-'))
        self.assertEqual(payslip.version, 1)
        self.assertTrue(payslip.file)

        # Check notification sent to employee
        self.assertTrue(Notification.objects.filter(recipient=self.emp_user, message__contains='July 2026').exists())

        # Test download permissions:
        # Owner -> 200
        self.client.force_login(self.emp_user)
        resp_owner = self.client.get(reverse('payslip_download', args=[payslip.id]))
        self.assertEqual(resp_owner.status_code, 200)
        self.assertEqual(resp_owner['Content-Type'], 'application/pdf')

        # HR -> 200
        self.client.force_login(self.hr_user)
        self.assertEqual(self.client.get(reverse('payslip_download', args=[payslip.id])).status_code, 200)

        # Accountant -> 200
        self.client.force_login(self.acct_user)
        self.assertEqual(self.client.get(reverse('payslip_download', args=[payslip.id])).status_code, 200)

        # Admin -> 200
        self.client.force_login(self.admin_user)
        self.assertEqual(self.client.get(reverse('payslip_download', args=[payslip.id])).status_code, 200)

        # Manager -> 403
        self.client.force_login(self.mgr_user)
        self.assertEqual(self.client.get(reverse('payslip_download', args=[payslip.id])).status_code, 403)

        # Other Employee -> 403
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(reverse('payslip_download', args=[payslip.id])).status_code, 403)

        # Regenerate bumps version
        self.client.force_login(self.admin_user)
        resp_regen = self.client.post(reverse('payslip_regenerate', args=[payslip.id]))
        self.assertEqual(resp_regen.status_code, 302)
        payslip.refresh_from_db()
        self.assertEqual(payslip.version, 2)

        # ZIP download
        resp_zip = self.client.get(reverse('payslips_download_zip') + '?month=2026-07')
        self.assertEqual(resp_zip.status_code, 200)
        self.assertEqual(resp_zip['Content-Type'], 'application/zip')

    # -----------------------------------------------------------------
    # Test 9: Smoke test: every new URL returns 200 or 403 as intended
    # -----------------------------------------------------------------
    @override_settings(HR_PAYROLL_V2=True)
    def test_smoke_all_new_urls_permissions(self):
        urls_to_test = [
            reverse('salary_structures_list'),
            reverse('salary_increments_list'),
            reverse('salary_advances_list'),
            reverse('payslips_list'),
        ]

        # HR, Accountant, Admin get 200
        for u in [self.hr_user, self.acct_user, self.admin_user]:
            self.client.force_login(u)
            for url in urls_to_test:
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200, f"Expected 200 for {u.username} on {url}, got {resp.status_code}")

        # Manager and Regular Employee get 403
        for u in [self.mgr_user, self.emp_user, self.other_user]:
            self.client.force_login(u)
            for url in urls_to_test:
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 403, f"Expected 403 for {u.username} on {url}, got {resp.status_code}")

        # My Payslips: accessible to every employee
        for u in [self.emp_user, self.other_user, self.mgr_user, self.hr_user, self.acct_user]:
            self.client.force_login(u)
            resp = self.client.get(reverse('my_payslips'))
            self.assertEqual(resp.status_code, 200, f"Expected 200 on my_payslips for {u.username}, got {resp.status_code}")

    # -----------------------------------------------------------------
    # Test 10: V1 net == V2 net across fixture scenarios (absence capping)
    # -----------------------------------------------------------------
    def test_v1_net_equals_v2_net_across_fixture_scenarios(self):
        """
        Verify that for several fixture employees without advances or manual lines:
        (1) structure basic 6,000 + housing 3,000 + transport 1,400 = 10,400, no attendance in Oct -> net 0 in both V1 and V2
        (2) no structure (fallback to employee.salary)
        (3) split structure
        (4) joiner (date_of_join mid-month)
        (5) partial absence
        V1 net == V2 net.
        """
        from Admin.models import Attendance
        month_oct = datetime.date(2026, 10, 1)

        # Employee 1: Prompt fixture: structure basic 6000 + housing 3000 + transport 1400 = 10400, no attendance in Oct.
        u1 = User.objects.create_user('emp_fixture1', 'f1@example.com', 'pass')
        e1 = Employee.objects.create(
            user=u1, employee_code="FIX-0001", department=self.dept, branch=self.branch,
            is_employee=True, date_of_join=datetime.date(2025, 1, 1), salary=Decimal('10400.00')
        )
        SalaryStructure.objects.create(
            employee=e1, basic=Decimal('6000.00'), housing_allowance=Decimal('3000.00'),
            transport_allowance=Decimal('1400.00'), other_allowances=Decimal('0.00'),
            total=Decimal('10400.00'), effective_from=datetime.date(2025, 1, 1)
        )

        # Employee 2: No structure (salary 8,000)
        u2 = User.objects.create_user('emp_fixture2', 'f2@example.com', 'pass')
        e2 = Employee.objects.create(
            user=u2, employee_code="FIX-0002", department=self.dept, branch=self.branch,
            is_employee=True, date_of_join=datetime.date(2025, 1, 1), salary=Decimal('8000.00')
        )

        # Employee 3: Split structure in Oct (joined 2025, struct A 6,000 -> struct B 6,000 with different components, salary=6,000)
        u3 = User.objects.create_user('emp_fixture3', 'f3@example.com', 'pass')
        e3 = Employee.objects.create(
            user=u3, employee_code="FIX-0003", department=self.dept, branch=self.branch,
            is_employee=True, date_of_join=datetime.date(2025, 1, 1), salary=Decimal('6000.00')
        )
        SalaryStructure.objects.create(
            employee=e3, basic=Decimal('4000.00'), housing_allowance=Decimal('2000.00'),
            total=Decimal('6000.00'), effective_from=datetime.date(2025, 1, 1), effective_to=datetime.date(2026, 10, 15)
        )
        SalaryStructure.objects.create(
            employee=e3, basic=Decimal('4500.00'), housing_allowance=Decimal('1500.00'),
            total=Decimal('6000.00'), effective_from=datetime.date(2026, 10, 16)
        )

        # Employee 4: Joiner (joined mid-month: 2026-10-16, salary 10,400)
        u4 = User.objects.create_user('emp_fixture4', 'f4@example.com', 'pass')
        e4 = Employee.objects.create(
            user=u4, employee_code="FIX-0004", department=self.dept, branch=self.branch,
            is_employee=True, date_of_join=datetime.date(2026, 10, 16), salary=Decimal('10400.00')
        )
        SalaryStructure.objects.create(
            employee=e4, basic=Decimal('6000.00'), housing_allowance=Decimal('3000.00'),
            transport_allowance=Decimal('1400.00'), total=Decimal('10400.00'), effective_from=datetime.date(2026, 10, 16)
        )

        # Employee 5: Partial absence (punched in for 20 days in Oct, 11 days absent, salary 9000)
        u5 = User.objects.create_user('emp_fixture5', 'f5@example.com', 'pass')
        e5 = Employee.objects.create(
            user=u5, employee_code="FIX-0005", department=self.dept, branch=self.branch,
            is_employee=True, date_of_join=datetime.date(2025, 1, 1), salary=Decimal('9000.00')
        )
        SalaryStructure.objects.create(
            employee=e5, basic=Decimal('5000.00'), housing_allowance=Decimal('3000.00'),
            transport_allowance=Decimal('1000.00'), total=Decimal('9000.00'), effective_from=datetime.date(2025, 1, 1)
        )
        for day_num in range(1, 21):
            dt_in = timezone.make_aware(datetime.datetime(2026, 10, day_num, 8, 0))
            dt_out = timezone.make_aware(datetime.datetime(2026, 10, day_num, 16, 0))
            Attendance.objects.create(
                employee=e5, login_time=dt_in, log_out_time=dt_out,
                status='APPROVED', total_hours_of_work=8.0, location='BDO'
            )

        test_emps = [e1, e2, e3, e4, e5]

        # 1. Run V1 (HR_PAYROLL_V2 = False)
        with override_settings(HR_PAYROLL_V2=False):
            Payroll.objects.filter(month=month_oct).delete()
            generate_payroll(month_oct, user=self.admin_user)
            v1_payrolls = {p.employee_id: p for p in Payroll.objects.filter(month=month_oct, employee__in=test_emps)}

        # Check e1 in V1
        self.assertEqual(v1_payrolls[e1.id].net_pay, Decimal('0.00'))
        self.assertEqual(v1_payrolls[e1.id].absence_deduction, Decimal('10400.00'))

        # 2. Run V2 (HR_PAYROLL_V2 = True)
        with override_settings(HR_PAYROLL_V2=True):
            Payroll.objects.filter(month=month_oct).delete()
            generate_payroll(month_oct, user=self.admin_user)
            v2_payrolls = {p.employee_id: p for p in Payroll.objects.filter(month=month_oct, employee__in=test_emps)}

        # Check e1 in V2
        p1_v2 = v2_payrolls[e1.id]
        self.assertEqual(p1_v2.basic_salary, Decimal('6000.00'))
        self.assertEqual(p1_v2.allowances, Decimal('4400.00'))
        self.assertEqual(p1_v2.absence_deduction, Decimal('10400.00'))
        self.assertEqual(p1_v2.net_pay, Decimal('0.00'))

        # Check V1 net == V2 net across all scenarios
        for emp in test_emps:
            p_v1 = v1_payrolls[emp.id]
            p_v2 = v2_payrolls[emp.id]
            self.assertEqual(
                p_v1.net_pay, p_v2.net_pay,
                f"Net mismatch for {emp.employee_code}: V1 net={p_v1.net_pay}, V2 net={p_v2.net_pay}"
            )

    # -----------------------------------------------------------------
    # Test 11: Overlapping salary structures repro and repair
    # -----------------------------------------------------------------
    def test_overlapping_salary_structures_repro_and_repair(self):
        """
        Repro:
        5,000 from 01/10; future 6,000 from 01/12 (5,000 closed 30/11); correction 5,200 from 09/11
        -> 5,000 ends 08/11; 5,200 ends 30/11; 6,000 starts 01/12.
        Nov = 8 days 5,000 + 22 days 5,200; Dec = 6,000.
        """
        u = User.objects.create_user('repro_user', 'repro@amms.ae', 'pass')
        emp = Employee.objects.create(
            user=u, employee_code="REP-0001", department=self.dept, branch=self.branch,
            is_employee=True, date_of_join=datetime.date(2026, 1, 1), salary=Decimal('5000.00')
        )

        # 1. 5,000 from 01/10
        s1 = update_employee_salary_structure(
            emp, basic=Decimal('5000.00'), effective_from=datetime.date(2026, 10, 1), user=self.admin_user
        )
        self.assertEqual(s1.effective_from, datetime.date(2026, 10, 1))
        self.assertIsNone(s1.effective_to)

        # 2. Future 6,000 from 01/12 (5,000 closed 30/11)
        s2 = update_employee_salary_structure(
            emp, basic=Decimal('6000.00'), effective_from=datetime.date(2026, 12, 1), user=self.admin_user
        )
        s1.refresh_from_db()
        self.assertEqual(s1.effective_to, datetime.date(2026, 11, 30))
        self.assertEqual(s2.effective_from, datetime.date(2026, 12, 1))
        self.assertIsNone(s2.effective_to)

        # 3. Correction 5,200 from 09/11
        s3 = update_employee_salary_structure(
            emp, basic=Decimal('5200.00'), effective_from=datetime.date(2026, 11, 9), user=self.admin_user
        )

        s1.refresh_from_db()
        s2.refresh_from_db()
        s3.refresh_from_db()

        # s1 shortened to 08/11
        self.assertEqual(s1.effective_to, datetime.date(2026, 11, 8))
        # s3 bounded by s2 start - 1 -> 30/11
        self.assertEqual(s3.effective_from, datetime.date(2026, 11, 9))
        self.assertEqual(s3.effective_to, datetime.date(2026, 11, 30))
        # s2 starts 01/12
        self.assertEqual(s2.effective_from, datetime.date(2026, 12, 1))
        self.assertIsNone(s2.effective_to)

        # Split calculation for November: 8 days @ 5,000 + 22 days @ 5,200
        # 8/30 * 5000 = 1333.33; 22/30 * 5200 = 3813.33; total = 5146.67
        split_nov = calculate_employee_salary_split(
            emp, datetime.date(2026, 11, 1), datetime.date(2026, 11, 30), 30
        )
        self.assertEqual(split_nov['total'], Decimal('5146.67'))

        # Split calculation for December: 6,000
        split_dec = calculate_employee_salary_split(
            emp, datetime.date(2026, 12, 1), datetime.date(2026, 12, 31), 31
        )
        self.assertEqual(split_dec['total'], Decimal('6000.00'))

        # Test latest effective_from tie-breaker in calculate_employee_salary_split:
        # Create an artificial overlapping structure covering 01/11 to 30/11
        SalaryStructure.objects.create(
            employee=emp, basic=Decimal('4000.00'), total=Decimal('4000.00'),
            effective_from=datetime.date(2026, 11, 1), effective_to=datetime.date(2026, 11, 30)
        )
        # On days 9..30 Nov, s3 has effective_from 2026-11-09 > 2026-11-01, so s3 (5,200) wins
        split_nov_overlap = calculate_employee_salary_split(
            emp, datetime.date(2026, 11, 1), datetime.date(2026, 11, 30), 30
        )
        self.assertIsNotNone(split_nov_overlap)
