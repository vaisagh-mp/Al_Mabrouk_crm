import io
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import openpyxl

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse, get_resolver, URLPattern, URLResolver
from django.utils import timezone

from Admin.models import (
    Attendance,
    AttendanceCorrection,
    Branch,
    CompanyDocument,
    Department,
    DocumentType,
    DutySchedule,
    Employee,
    EmployeeDocument,
    EmployeeSchedule,
    Holiday,
    Leave,
    LeaveLedger,
    LeaveType,
    Notification,
    OTApproval,
    Project,
    RestDayRule,
    SalaryStructure,
    WorkOrder,
    WorkOrderTime,
)
from Admin.notifications import notify, notify_role
from Admin.attendance_register import build_attendance_register, build_attendance_summary
from finance.models import Payroll, Payslip, SalaryAdvance
from service_logs.models import EmployeeServiceLog


def _collect_noarg_url_names():
    """Collect all GET-safe named URLs taking 0 URL kwargs across CRM apps (excluding Django admin)."""
    names = []

    def walk(patterns):
        for p in patterns:
            if isinstance(p, URLResolver):
                if getattr(p, 'app_name', None) == 'admin' or getattr(p, 'namespace', None) == 'admin':
                    continue
                walk(p.url_patterns)
            elif isinstance(p, URLPattern) and p.name:
                if getattr(p.pattern, 'regex', None) and p.pattern.regex.groups == 0:
                    names.append(p.name)

    walk(get_resolver().url_patterns)
    return names


@override_settings(
    HR_ATTENDANCE_ENGINE_V2=True,
    HR_LEAVE_V2=True,
    HR_TIMESHEET_APPROVAL=True,
    HR_PAYROLL_V2=True,
)
class Phase8Tests(TestCase):
    def setUp(self):
        self.client = Client()
        self.today = timezone.localdate()

        self.dept_eng, _ = Department.objects.get_or_create(name='Engineering P8')
        self.dept_ops, _ = Department.objects.get_or_create(name='Operations P8')
        self.branch_dxb, _ = Branch.objects.get_or_create(name='Dubai P8')
        self.branch_au, _ = Branch.objects.get_or_create(name='Abu Dhabi P8')

        # Users & Profiles for all 6 roles
        self.su_user = User.objects.create_superuser('su_p8', 'su8@test.com', 'pass123')

        self.hr_user = User.objects.create_user('hr_p8', 'hr8@test.com', 'pass123', first_name='Fatima', last_name='HR')
        self.hr_emp = Employee.objects.create(
            user=self.hr_user, is_hr=True, department=self.dept_ops, branch=self.branch_dxb,
            salary=Decimal('10000.00'), date_of_join=date(2024, 1, 1), employee_code='AMMS-8001'
        )

        self.mgr_user = User.objects.create_user('mgr_p8', 'mgr8@test.com', 'pass123', first_name='Tariq', last_name='Mgr')
        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user, is_manager=True, department=self.dept_eng, branch=self.branch_dxb,
            salary=Decimal('12000.00'), date_of_join=date(2024, 1, 1), employee_code='AMMS-8002'
        )

        self.emp_user = User.objects.create_user('emp_p8', 'emp8@test.com', 'pass123', first_name='Salem', last_name='Eng')
        self.emp = Employee.objects.create(
            user=self.emp_user, is_employee=True, department=self.dept_eng, branch=self.branch_dxb,
            reporting_manager=self.mgr_emp, salary=Decimal('6000.00'),
            date_of_join=date(2024, 1, 1), employee_code='AMMS-8003'
        )

        self.other_emp_user = User.objects.create_user('other_p8', 'other8@test.com', 'pass123', first_name='Zaid', last_name='Ops')
        self.other_emp = Employee.objects.create(
            user=self.other_emp_user, is_employee=True, department=self.dept_ops, branch=self.branch_au,
            salary=Decimal('5000.00'), date_of_join=date(2024, 1, 1), employee_code='AMMS-8004'
        )

        self.acc_user = User.objects.create_user('acc_p8', 'acc8@test.com', 'pass123', first_name='Adil', last_name='Acc')
        self.acc_emp = Employee.objects.create(
            user=self.acc_user, is_accountant=True, department=self.dept_ops, branch=self.branch_dxb,
            salary=Decimal('8000.00'), date_of_join=date(2024, 1, 1), employee_code='AMMS-8005'
        )

        self.adm_user = User.objects.create_user('adm_p8', 'adm8@test.com', 'pass123', first_name='Amna', last_name='Adm')
        self.adm_emp = Employee.objects.create(
            user=self.adm_user, is_administration=True, department=self.dept_ops, branch=self.branch_dxb,
            salary=Decimal('7000.00'), date_of_join=date(2024, 1, 1), employee_code='AMMS-8006'
        )

        # Fixture project, leave, correction, timesheet, document, payroll
        self.project = Project.objects.create(
            name='MV Explorer P8', vessel_name='MV Explorer', client_name='Marine Client', manager=self.mgr_emp
        )
        self.wo = WorkOrder.objects.create(project=self.project, work_order_number='WO-P8-01')

        # Duty schedule + rest days
        sched, _ = DutySchedule.objects.get_or_create(name='Std 8h P8', defaults={'start_time': time(8, 0), 'end_time': time(17, 0), 'break_minutes': 60})
        for e in (self.hr_emp, self.mgr_emp, self.emp, self.other_emp, self.acc_emp, self.adm_emp):
            EmployeeSchedule.objects.create(employee=e, duty_schedule=sched, effective_from=date(2024, 1, 1))
            RestDayRule.objects.create(employee=e, rest_weekday=6, effective_from=date(2024, 1, 1))

        self.lt_annual = LeaveType.objects.filter(code='ANNUAL').first() or LeaveType.objects.create(
            code='ANNUAL', name='Annual Leave', yearly_entitlement=Decimal('30.00'), uses_balance=True
        )
        LeaveLedger.objects.create(
            employee=self.emp, leave_type=self.lt_annual, date=date(self.today.year, 1, 1),
            entry_type='OPENING', days=Decimal('20.00')
        )

        # Attendance today for self.emp (OFFICE) and self.hr_emp (MISSING_PUNCH if open yesterday or missing)
        tz = timezone.get_current_timezone()
        d_target = date(2026, 11, 4)  # Wednesday
        self.d_target = d_target
        Attendance.objects.create(
            employee=self.emp,
            login_time=timezone.make_aware(datetime.combine(d_target, time(8, 0)), tz),
            log_out_time=timezone.make_aware(datetime.combine(d_target, time(17, 0)), tz),
            location='BDO',
            status='APPROVED',
        )

        # Pending leave for self.emp
        self.leave_req = Leave.objects.create(
            user=self.emp_user,
            employee=self.emp,
            leave_type='ANNUAL LEAVE',
            leave_type_ref=self.lt_annual,
            from_date=d_target + timedelta(days=5),
            to_date=d_target + timedelta(days=6),
            no_of_days=2,
            days=Decimal('2.00'),
            reason='Family visit',
            approval_status='PENDING',
            approver=self.mgr_emp,
        )

        # Pending correction for self.other_emp
        corr_d = d_target - timedelta(days=1)
        self.corr_req = AttendanceCorrection.objects.create(
            employee=self.other_emp,
            date=corr_d,
            correction_type='MISSED_OUT',
            requested_login=timezone.make_aware(datetime.combine(corr_d, time(8, 0)), tz),
            requested_logout=timezone.make_aware(datetime.combine(corr_d, time(17, 0)), tz),
            reason='Forgot punch out',
            status='PENDING',
        )

        # Submitted service log + OT
        self.slog_sub = EmployeeServiceLog.objects.create(
            employee=self.emp,
            project=self.project,
            date=d_target - timedelta(days=2),
            vessel_name='MV Explorer',
            service_type='JOB',
            start_time=time(7, 0),
            end_time=time(20, 0),
            total_hours=Decimal('13.00'),
            ot_hours=Decimal('3.00'),
            ot_type='WEEKDAY',
            normal_cost=Decimal('288.46'),
            ot_cost=Decimal('108.17'),
            approval_status='SUBMITTED',
        )
        self.slog_app = EmployeeServiceLog.objects.create(
            employee=self.emp,
            project=self.project,
            date=d_target - timedelta(days=3),
            vessel_name='MV Explorer',
            service_type='JOB',
            start_time=time(7, 0),
            end_time=time(19, 0),
            total_hours=Decimal('12.00'),
            ot_hours=Decimal('2.00'),
            ot_type='WEEKDAY',
            normal_cost=Decimal('288.46'),
            ot_cost=Decimal('72.12'),
            approval_status='APPROVED',
        )
        OTApproval.objects.update_or_create(
            employee=self.emp, date=d_target - timedelta(days=2),
            defaults={'ot_hours': Decimal('3.00'), 'ot_cost': Decimal('108.17'), 'status': 'PENDING'}
        )
        OTApproval.objects.update_or_create(
            employee=self.emp, date=d_target - timedelta(days=3),
            defaults={'ot_hours': Decimal('2.00'), 'ot_cost': Decimal('72.12'), 'status': 'APPROVED'}
        )

        # Expiring employee doc (<= 30d)
        doc_type = DocumentType.objects.filter(category='EMPLOYEE', has_expiry=True).first() or DocumentType.objects.create(
            name='Passport P8', code='PASSPORT_P8', category='EMPLOYEE', has_expiry=True
        )
        EmployeeDocument.objects.create(
            employee=self.emp,
            doc_type=doc_type,
            document_number='PP-888',
            expiry_date=self.today + timedelta(days=10),
            file=SimpleUploadedFile('pp.pdf', b'%PDF-1.4 test'),
            is_current=True,
        )
        comp_dt = DocumentType.objects.filter(category='COMPANY', has_expiry=True).first() or DocumentType.objects.create(
            name='Trade License P8', code='TL_P8', category='COMPANY', has_expiry=True
        )
        CompanyDocument.objects.create(
            doc_type=comp_dt,
            title='Trade License 2026',
            document_number='TL-100',
            expiry_date=self.today + timedelta(days=15),
            file=SimpleUploadedFile('tl.pdf', b'%PDF-1.4 test'),
            is_current=True,
        )

        # Payroll fixture for current month
        m_first = self.d_target.replace(day=1)
        Payroll.objects.create(
            employee=self.emp,
            month=m_first,
            basic_salary=Decimal('4000.00'),
            allowances=Decimal('2000.00'),
            ot_amount=Decimal('72.12'),
            deductions=Decimal('0.00'),
            status='APPROVED',
        )

    # ------------------------------------------------------------------
    # 1. DASHBOARD TESTS
    # ------------------------------------------------------------------
    def test_dashboard_cards_match_target_lists_and_links_carry_filters(self):
        self.client.force_login(self.hr_user)
        d_str = self.d_target.strftime('%Y-%m-%d')
        resp = self.client.get(
            reverse('hr-dashboard'),
            {'date': d_str, 'department': self.dept_eng.id, 'branch': self.branch_dxb.id},
        )
        self.assertEqual(resp.status_code, 200)
        cards = resp.context['cards']

        # Check each card's link contains the active filters and following it yields matching row count
        # Card 1: Active Staff -> employee list
        self.assertIn(f'department={self.dept_eng.id}', cards['active_staff']['url'])
        self.assertIn(f'branch={self.branch_dxb.id}', cards['active_staff']['url'])
        r_emp = self.client.get(cards['active_staff']['url'])
        self.assertEqual(r_emp.status_code, 200)
        self.assertEqual(r_emp.context['page_obj'].paginator.count, cards['active_staff']['count'])

        # Cards 2, 3, 4, 5, 6, 7 -> attendance register filtered
        for key in ('present_today', 'absent', 'missing_punch', 'on_leave', 'vessel', 'rest_holiday'):
            url = cards[key]['url']
            self.assertIn(f'start={d_str}', url)
            self.assertIn(f'department={self.dept_eng.id}', url)
            r_reg = self.client.get(url)
            self.assertEqual(r_reg.status_code, 200)
            self.assertEqual(
                r_reg.context['page_obj'].paginator.count,
                cards[key]['count'],
                f"Card '{key}' count {cards[key]['count']} != register count {r_reg.context['page_obj'].paginator.count}"
            )

        # Card 8: Pending leave -> Manage Leave
        r_lv = self.client.get(cards['pending_leave']['url'])
        self.assertEqual(r_lv.status_code, 200)
        self.assertEqual(r_lv.context['leaves'].paginator.count, cards['pending_leave']['count'])

        # Card 9a: Pending corrections -> Team Requests
        r_corr = self.client.get(cards['pending_corrections']['url'])
        self.assertEqual(r_corr.status_code, 200)
        self.assertEqual(len(r_corr.context['team_pending']), cards['pending_corrections']['count'])

        # Card 9b: Pending timesheets -> Timesheet Approvals
        r_ts = self.client.get(cards['pending_timesheets']['url'])
        self.assertEqual(r_ts.status_code, 200)
        self.assertEqual(r_ts.context['page_obj'].paginator.count, cards['pending_timesheets']['count'])

        # Card 10: Documents expired / <=30 -> Documents & Expiry
        r_doc = self.client.get(cards['docs_expiring']['url'])
        self.assertEqual(r_doc.status_code, 200)
        self.assertEqual(r_doc.context['page_obj'].paginator.count, cards['docs_expiring']['count'])

        # Admin gets same HR Dashboard under Admin menu
        self.client.force_login(self.su_user)
        r_adm = self.client.get(reverse('admin_hr_dashboard'), {'date': d_str})
        self.assertEqual(r_adm.status_code, 200)
        self.assertIn('HR Dashboard', r_adm.content.decode('utf-8'))

        # Dashboard Excel & PDF exports work
        self.client.force_login(self.hr_user)
        r_xl = self.client.get(reverse('hr-dashboard'), {'date': d_str, 'export': 'excel'})
        self.assertEqual(r_xl.status_code, 200)
        self.assertIn('spreadsheetml', r_xl['Content-Type'])

        r_pdf = self.client.get(reverse('hr-dashboard'), {'date': d_str, 'export': 'pdf'})
        self.assertEqual(r_pdf.status_code, 200)
        self.assertEqual(r_pdf['Content-Type'], 'application/pdf')

    def test_dashboard_query_count_bounded_with_50_employees(self):
        # Create 50 additional employees
        bulk_users = [
            User(username=f'bulk_u_{i}', first_name=f'User{i}', last_name='Test', is_active=True)
            for i in range(50)
        ]
        User.objects.bulk_create(bulk_users)
        created_users = list(User.objects.filter(username__startswith='bulk_u_'))
        Employee.objects.bulk_create([
            Employee(user=u, is_employee=True, department=self.dept_eng, branch=self.branch_dxb, employee_code=f'BULK-{i:04d}')
            for i, u in enumerate(created_users)
        ])

        self.client.force_login(self.hr_user)
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse('hr-dashboard'), {'date': self.d_target.strftime('%Y-%m-%d')})
        self.assertEqual(resp.status_code, 200)
        # Constant bounded queries even with 56 employees (no N+1)
        self.assertLessEqual(len(ctx), 45, f"Too many queries on dashboard: {len(ctx)}")

    @override_settings(HR_ATTENDANCE_ENGINE_V2=False)
    def test_dashboard_hides_missing_punch_when_engine_v2_off(self):
        self.client.force_login(self.hr_user)
        resp = self.client.get(reverse('hr-dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['cards']['missing_punch']['visible'])
        self.assertNotIn('data-card="missing-punch"', resp.content.decode('utf-8'))

    # ------------------------------------------------------------------
    # 2. TYPED NOTIFICATIONS TESTS
    # ------------------------------------------------------------------
    def test_typed_notifications_helper_dedup_actor_open_and_legacy_json(self):
        Notification.objects.all().delete()

        # Actor is never notified
        created = notify(
            [self.emp_user, self.mgr_user, self.mgr_user],
            'LEAVE',
            'Leave submitted',
            link='/manager/manage-leave/',
            related=self.leave_req,
            actor=self.emp_user,
        )
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].recipient, self.mgr_user)
        self.assertEqual(created[0].type, 'LEAVE')
        self.assertEqual(created[0].link, '/manager/manage-leave/')
        self.assertEqual(created[0].related_kind, 'Leave')
        self.assertEqual(created[0].related_id, self.leave_req.id)

        # Inactive user skipped
        inactive_u = User.objects.create_user('inact_n', 'in@test.com', 'p', is_active=False)
        res_inact = notify(inactive_u, 'SYSTEM', 'Should not send')
        self.assertEqual(res_inact, [])

        # notify_role de-duplicates user having both HR and Superuser
        both_user = User.objects.create_superuser('both_hr_su', 'both@test.com', 'pass123')
        Employee.objects.create(user=both_user, is_hr=True, employee_code='AMMS-8099')
        role_notifs = notify_role(
            ['HR', 'ADMIN'],
            'ATTENDANCE_CORRECTION',
            'Queue check',
            link='/hr/attendance-status/?tab=team_requests',
            actor=self.emp_user,
        )
        recips = [n.recipient_id for n in role_notifs]
        self.assertEqual(len(recips), len(set(recips)), "notify_role must deduplicate recipients")
        self.assertIn(both_user.id, recips)
        self.assertNotIn(self.emp_user.id, recips)

        # Click opens link & marks read
        self.client.force_login(self.mgr_user)
        n = created[0]
        self.assertFalse(n.is_read)
        r_open = self.client.get(reverse('notification_open', args=[n.id]))
        self.assertEqual(r_open.status_code, 302)
        self.assertEqual(r_open['Location'], '/manager/manage-leave/')
        n.refresh_from_db()
        self.assertTrue(n.is_read)
        self.assertIsNotNone(n.read_at)

        # Notifications page filters + 20 per page
        for i in range(25):
            notify(self.mgr_user, 'PROJECT' if i % 2 == 0 else 'TIMESHEET', f'Msg {i}', link='/manager/projects/')
        r_pg = self.client.get(reverse('notifications_page'))
        self.assertEqual(r_pg.status_code, 200)
        self.assertEqual(len(r_pg.context['page_obj']), 20)

        r_flt = self.client.get(reverse('notifications_page'), {'type': 'PROJECT', 'read': 'unread'})
        self.assertEqual(r_flt.status_code, 200)
        for item in r_flt.context['page_obj']:
            self.assertEqual(item.type, 'PROJECT')
            self.assertFalse(item.is_read)

        # Old JSON endpoints keep old keys + include type & link
        notify(self.emp_user, 'PAYSLIP', 'Payslip ready', link='/finance/my-payslips/')
        self.client.force_login(self.emp_user)
        r_emp_j = self.client.get(reverse('employee-fetch-notifications')).json()
        self.assertIn('notifications', r_emp_j)
        self.assertGreaterEqual(len(r_emp_j['notifications']), 1)
        first_j = r_emp_j['notifications'][0]
        self.assertIn('id', first_j)
        self.assertIn('message', first_j)
        self.assertIn('created_at', first_j)
        self.assertEqual(first_j['type'], 'PAYSLIP')
        self.assertEqual(first_j['link'], '/finance/my-payslips/')

        # Mark all read via legacy endpoint
        r_mark = self.client.post(reverse('employee-mark-notifications-as-read'))
        self.assertEqual(r_mark.status_code, 200)
        self.assertEqual(Notification.objects.filter(recipient=self.emp_user, is_read=False).count(), 0)

        # Test remaining fetch endpoints don't error
        for user_obj, url_name in [
            (self.su_user, 'fetch-notifications'),
            (self.hr_user, 'hr_fetch_notifications'),
            (self.mgr_user, 'manager-fetch-notifications'),
            (self.acc_user, 'fin_fetch_notifications'),
            (self.adm_user, 'admstrn_fetch_notifications'),
        ]:
            self.client.force_login(user_obj)
            rj = self.client.get(reverse(url_name))
            self.assertEqual(rj.status_code, 200)
            self.assertIn('notifications', rj.json())

    # ------------------------------------------------------------------
    # 3. REPORTS CENTER TESTS
    # ------------------------------------------------------------------
    def test_reports_center_all_10_preview_excel_pdf_limits_and_permissions(self):
        report_keys = [
            'employee_master', 'daily_attendance', 'monthly_summary',
            'missing_punch_corrections', 'leave_balances', 'leave_history',
            'overtime', 'timesheets', 'document_expiry', 'payroll_summary',
        ]
        m_str = self.d_target.strftime('%Y-%m')
        start_s = self.d_target.replace(day=1).strftime('%Y-%m-%d')
        end_s = self.d_target.strftime('%Y-%m-%d')

        self.client.force_login(self.hr_user)
        for rkey in report_keys:
            params = {'report': rkey, 'start': start_s, 'end': end_s, 'month': m_str}
            r_prev = self.client.get(reverse('hr_reports_center'), params)
            self.assertEqual(r_prev.status_code, 200, f"Preview failed for {rkey}")
            prev_total = r_prev.context['total_count']

            r_xl = self.client.get(reverse('hr_reports_center'), {**params, 'export': 'excel'})
            self.assertEqual(r_xl.status_code, 200)
            wb = openpyxl.load_workbook(io.BytesIO(r_xl.content))
            ws = wb.active
            # Header starts at row 5; data starts row 6 (optional TOTAL row at end)
            data_rows = [
                row for row in ws.iter_rows(min_row=6, values_only=True)
                if any(v is not None for v in row) and row[0] != 'TOTAL'
            ]
            self.assertEqual(len(data_rows), prev_total, f"Row count mismatch on {rkey}: excel {len(data_rows)} vs preview {prev_total}")

        # Manager: limited to direct reports ONLY, NO salary columns, 403 on payroll_summary
        self.client.force_login(self.mgr_user)
        r_mgr_payroll = self.client.get(reverse('manager_reports_center'), {'report': 'payroll_summary'})
        self.assertEqual(r_mgr_payroll.status_code, 403)

        r_mgr_emp = self.client.get(reverse('manager_reports_center'), {'report': 'employee_master', 'export': 'excel'})
        self.assertEqual(r_mgr_emp.status_code, 200)
        wb_mgr = openpyxl.load_workbook(io.BytesIO(r_mgr_emp.content))
        ws_mgr = wb_mgr.active
        hdrs_mgr = [c.value for c in ws_mgr[5]]
        self.assertNotIn('Salary (AED)', hdrs_mgr)
        mgr_emp_names = [row[1] for row in ws_mgr.iter_rows(min_row=6, values_only=True) if row[1]]
        self.assertIn(self.emp.name, mgr_emp_names)
        self.assertNotIn(self.other_emp.name, mgr_emp_names)

        r_mgr_ot = self.client.get(reverse('manager_reports_center'), {'report': 'overtime', 'start': start_s, 'end': end_s, 'export': 'excel'})
        self.assertEqual(r_mgr_ot.status_code, 200)
        wb_mgr_ot = openpyxl.load_workbook(io.BytesIO(r_mgr_ot.content))
        self.assertNotIn('OT Cost (AED)', [c.value for c in wb_mgr_ot.active[5]])

        # Accountant: salary reports only (403 on leave_history, employee_master)
        self.client.force_login(self.acc_user)
        self.assertEqual(self.client.get(reverse('fin_reports_center'), {'report': 'payroll_summary', 'month': m_str}).status_code, 200)
        self.assertEqual(self.client.get(reverse('fin_reports_center'), {'report': 'overtime', 'start': start_s, 'end': end_s}).status_code, 200)
        self.assertEqual(self.client.get(reverse('fin_reports_center'), {'report': 'leave_history'}).status_code, 403)

        # Employee & Administration: 403 Forbidden
        self.client.force_login(self.emp_user)
        self.assertEqual(self.client.get(reverse('hr_reports_center')).status_code, 403)
        self.client.force_login(self.adm_user)
        self.assertEqual(self.client.get(reverse('hr_reports_center')).status_code, 403)

        # PDF > 31 days on day-level report shows message and does not return PDF
        self.client.force_login(self.hr_user)
        r_lim = self.client.get(reverse('hr_reports_center'), {
            'report': 'daily_attendance',
            'start': '2026-09-01',
            'end': '2026-10-15',
            'export': 'pdf',
        })
        self.assertEqual(r_lim.status_code, 200)
        self.assertNotEqual(r_lim['Content-Type'], 'application/pdf')
        self.assertIn('limited to 31 days', r_lim.content.decode('utf-8'))

        # Normal PDF <= 31 days returns application/pdf
        r_ok_pdf = self.client.get(reverse('hr_reports_center'), {
            'report': 'daily_attendance',
            'start': start_s,
            'end': end_s,
            'export': 'pdf',
        })
        self.assertEqual(r_ok_pdf.status_code, 200)
        self.assertEqual(r_ok_pdf['Content-Type'], 'application/pdf')

    # ------------------------------------------------------------------
    # 4. ANALYTICS TESTS
    # ------------------------------------------------------------------
    def test_analytics_absent_equals_register_and_department_filter_and_ot(self):
        self.client.force_login(self.su_user)
        start_d = self.d_target - timedelta(days=3)
        end_d = self.d_target
        date_param = f"{start_d.strftime('%Y-%m-%d')} to {end_d.strftime('%Y-%m-%d')}"

        # Same filters: Engineering department
        reg_rows = build_attendance_register(start_d, end_d, department=self.dept_eng.id, branch=self.branch_dxb.id)
        _, reg_totals = build_attendance_summary(reg_rows)

        r_an = self.client.get(reverse('analytics_employee'), {
            'date': date_param,
            'department': self.dept_eng.id,
            'branch': self.branch_dxb.id,
        })
        self.assertEqual(r_an.status_code, 200)
        self.assertEqual(r_an.context['kpi_total_absent'], reg_totals['absent_days'])

        # Department filter narrows results (self.other_emp in OPS must be absent)
        usernames = [e['employee'].user.username for e in r_an.context['all_filtered_analytics']]
        self.assertIn(self.emp_user.username, usernames)
        self.assertNotIn(self.other_emp_user.username, usernames)

        # Approved OT only counts APPROVED OTApproval when HR_TIMESHEET_APPROVAL=True
        emp_entry = [e for e in r_an.context['all_filtered_analytics'] if e['employee'].id == self.emp.id][0]
        self.assertEqual(emp_entry['approved_ot_hours'], Decimal('2.00'))
        self.assertIn('days_rest', emp_entry)
        self.assertIn('days_missing_punch', emp_entry)

        # Excel export works for Employee Analytics & Project Analytics
        r_emp_xl = self.client.get(reverse('analytics_employee'), {
            'date': date_param, 'department': self.dept_eng.id, 'export': 'excel'
        })
        self.assertEqual(r_emp_xl.status_code, 200)
        self.assertIn('spreadsheetml', r_emp_xl['Content-Type'])

        r_proj = self.client.get(reverse('analytics_project'), {
            'date': date_param, 'department': self.dept_eng.id, 'export': 'excel'
        })
        self.assertEqual(r_proj.status_code, 200)
        self.assertIn('spreadsheetml', r_proj['Content-Type'])

    # ------------------------------------------------------------------
    # 5. PERMISSION SMOKE TEST (ALL 6 ROLES x ALL NAMED URLS)
    # ------------------------------------------------------------------
    def test_permission_smoke_all_roles_never_500(self):
        noarg_urls = _collect_noarg_url_names()
        # Exclude mutating logout endpoints that invalidate test session
        skip_names = {'custom_logout', 'logout', 'password_change', 'password_change_done'}
        test_urls = [reverse(name) for name in noarg_urls if name not in skip_names]

        # Key parameterized URLs with fixture IDs / month
        m_str = self.d_target.strftime('%Y-%m')
        param_urls = [
            reverse('employee_profile', args=[self.emp.id]),
            reverse('hr_employee_profile', args=[self.emp.id]),
            reverse('manager_employee_profile', args=[self.emp.id]),
            reverse('employee_attendance_month', args=[self.emp.id, m_str]),
            reverse('hr_employee_attendance_month', args=[self.emp.id, m_str]),
            reverse('salary_advance_detail', args=[SalaryAdvance.objects.create(
                employee=self.emp, amount=Decimal('1000.00'), instalments=2,
                start_month=self.d_target.replace(day=1), created_by=self.hr_user
            ).id]),
            reverse('project-summary', args=[self.project.id]),
            reverse('project-summary-view', args=[self.project.id]),
            reverse('project_details', args=[self.project.id]),
        ]
        all_urls = test_urls + param_urls

        roles = [
            ('employee', self.emp_user),
            ('manager', self.mgr_user),
            ('hr', self.hr_user),
            ('accountant', self.acc_user),
            ('administration', self.adm_user),
            ('superuser', self.su_user),
        ]

        failures_500 = []
        for role_name, user_obj in roles:
            self.client.force_login(user_obj)
            for url in all_urls:
                try:
                    resp = self.client.get(url)
                    if resp.status_code == 500:
                        failures_500.append((role_name, url, 500))
                    self.assertIn(
                        resp.status_code,
                        (200, 302, 400, 403, 404, 405),
                        f"Unexpected status {resp.status_code} for {role_name} on {url}",
                    )
                except Exception as exc:
                    failures_500.append((role_name, url, repr(exc)))

        self.assertEqual(failures_500, [], f"500 / unhandled errors found: {failures_500}")

    # ------------------------------------------------------------------
    # 6. QUICK FIXES REGRESSION TESTS
    # ------------------------------------------------------------------
    def test_dashboard_cards_three_per_row_no_gaps_and_admin_parity(self):
        self.client.force_login(self.hr_user)
        resp = self.client.get(reverse('hr-dashboard'))
        self.assertEqual(resp.status_code, 200)
        cards_list = resp.context['cards_list']
        self.assertEqual(len(cards_list), 10)
        html = resp.content.decode('utf-8')
        self.assertIn('col-12 col-sm-6 col-lg-4', html)
        self.assertNotIn('col-xl', html)

        # Admin copy renders same layout
        self.client.force_login(self.su_user)
        resp_admin = self.client.get(reverse('admin_hr_dashboard'))
        self.assertEqual(resp_admin.status_code, 200)
        self.assertEqual(len(resp_admin.context['cards_list']), 10)

        # Hidden cards drop cleanly without leaving gaps
        with override_settings(HR_ATTENDANCE_ENGINE_V2=False, HR_TIMESHEET_APPROVAL=False):
            self.client.force_login(self.hr_user)
            resp_off = self.client.get(reverse('hr-dashboard'))
            self.assertEqual(resp_off.status_code, 200)
            off_keys = [c['key'] for c in resp_off.context['cards_list']]
            self.assertEqual(len(off_keys), 9)
            self.assertNotIn('missing-punch', off_keys)
            pending_card = next(c for c in resp_off.context['cards_list'] if c['key'] == 'pending-approvals')
            sub_keys = [s['key'] for s in pending_card['sub_items']]
            self.assertEqual(sub_keys, ['pending-corrections'])

    def test_employeebase_sidebar_uses_fa506_icons(self):
        self.client.force_login(self.emp_user)
        resp = self.client.get(reverse('my_payslips'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('fa-file-alt', html)
        self.assertNotIn('fa-file-invoice-dollar', html)
        self.assertNotIn('fa-hand-holding-usd', html)

    def test_my_payslips_unavailable_when_v2_off_and_autogens_when_v2_on(self):
        SalaryStructure.objects.create(
            employee=self.emp,
            basic=Decimal('4000.00'),
            housing_allowance=Decimal('1500.00'),
            transport_allowance=Decimal('500.00'),
            effective_from=date(2024, 1, 1),
            created_by=self.hr_user,
        )
        paid_payroll = Payroll.objects.create(
            employee=self.emp,
            month=date(2026, 10, 1),
            basic_salary=Decimal('4000.00'),
            allowances=Decimal('2000.00'),
            ot_amount=Decimal('0.00'),
            deductions=Decimal('0.00'),
            status='PAID',
        )
        self.client.force_login(self.emp_user)

        with override_settings(HR_PAYROLL_V2=False):
            resp = self.client.get(reverse('my_payslips'))
            self.assertEqual(resp.status_code, 200)
            html = resp.content.decode('utf-8')
            self.assertIn('Not available', html)
            self.assertNotIn('Generating...', html)
            self.assertFalse(Payslip.objects.filter(payroll=paid_payroll).exists())

        with override_settings(HR_PAYROLL_V2=True):
            resp = self.client.get(reverse('my_payslips'))
            self.assertEqual(resp.status_code, 200)
            html = resp.content.decode('utf-8')
            self.assertNotIn('Generating...', html)
            ps = Payslip.objects.filter(payroll=paid_payroll).first()
            self.assertIsNotNone(ps)
            self.assertTrue(bool(ps.file))
            self.assertIn(reverse('payslip_download', args=[ps.id]), html)

    def test_profile_shows_dash_for_empty_fields(self):
        self.emp.date_of_join = None
        self.emp.date_of_birth = None
        self.emp.phone_number = ''
        self.emp.address = ''
        self.emp.save()
        self.client.force_login(self.emp_user)
        resp = self.client.get(reverse('profile_view'))
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.content.decode('utf-8').count('—'), 4)

        self.hr_emp.date_of_join = None
        self.hr_emp.date_of_birth = None
        self.hr_emp.phone_number = ''
        self.hr_emp.address = ''
        self.hr_emp.save()
        self.client.force_login(self.hr_user)
        resp_hr = self.client.get(reverse('hr_profile_view'))
        self.assertEqual(resp_hr.status_code, 200)
        self.assertGreaterEqual(resp_hr.content.decode('utf-8').count('—'), 4)

    def test_timesheet_reopen_and_ot_actions_respect_switch(self):
        from django.core.exceptions import ValidationError
        from Admin.timesheet_service import reopen_service_log

        # Use an approved log in a month with no approved/paid payroll lock
        unlocked_log = EmployeeServiceLog.objects.create(
            employee=self.other_emp,
            project=self.project,
            date=self.d_target - timedelta(days=3),
            vessel_name='MV Explorer',
            service_type='JOB',
            start_time=time(7, 0),
            end_time=time(17, 0),
            total_hours=Decimal('10.00'),
            ot_hours=Decimal('0.00'),
            approval_status='APPROVED',
        )
        m_str = self.d_target.strftime('%Y-%m')
        ts_url = reverse('timesheet_approvals_hr')
        ot_url = reverse('ot_approval_hr')

        with override_settings(HR_TIMESHEET_APPROVAL=False):
            with self.assertRaises(ValidationError):
                reopen_service_log(unlocked_log, self.hr_user, 'Need fix')

            self.client.force_login(self.hr_user)
            resp_get = self.client.get(ts_url)
            self.assertEqual(resp_get.status_code, 200)
            ts_html = resp_get.content.decode('utf-8')
            self.assertIn('Timesheet approval is switched off', ts_html)
            self.assertNotIn('onclick="openReopenModal', ts_html)
            self.assertNotIn('onclick="submitBulkAction', ts_html)

            resp_post = self.client.post(ts_url, {
                'action': 'reopen',
                'log_id': unlocked_log.id,
                'reason': 'Need fix',
            }, follow=True)
            self.assertEqual(resp_post.status_code, 200)
            unlocked_log.refresh_from_db()
            self.assertEqual(unlocked_log.approval_status, 'APPROVED')
            self.assertIn('Timesheet approval is switched off', resp_post.content.decode('utf-8'))

            resp_ot_get = self.client.get(f"{ot_url}?month={m_str}")
            self.assertEqual(resp_ot_get.status_code, 200)
            ot_html = resp_ot_get.content.decode('utf-8')
            self.assertIn('Timesheet approval is switched off', ot_html)
            self.assertNotIn('Approve All for', ot_html)
            self.assertNotIn('onclick="approveOT', ot_html)
            self.assertNotIn('onclick="openRejectOTModal', ot_html)

            pending_ot = OTApproval.objects.get(employee=self.emp, date=self.d_target - timedelta(days=2))
            resp_ot_post = self.client.post(f"{ot_url}?month={m_str}", {
                'action': 'approve_ot',
                'ot_id': pending_ot.id,
            }, follow=True)
            pending_ot.refresh_from_db()
            self.assertEqual(pending_ot.status, 'PENDING')
            self.assertIn('Timesheet approval is switched off', resp_ot_post.content.decode('utf-8'))

        # With HR_TIMESHEET_APPROVAL=True, reopen moves APPROVED -> DRAFT and shows under ?status=DRAFT
        self.client.force_login(self.hr_user)
        resp_reopen = self.client.post(ts_url, {
            'action': 'reopen',
            'log_id': unlocked_log.id,
            'reason': 'Correction needed',
        }, follow=True)
        self.assertEqual(resp_reopen.status_code, 200)
        unlocked_log.refresh_from_db()
        self.assertEqual(unlocked_log.approval_status, 'DRAFT')
        resp_draft = self.client.get(f"{ts_url}?status=DRAFT")
        self.assertEqual(resp_draft.status_code, 200)
        self.assertIn(self.other_emp.name, resp_draft.content.decode('utf-8'))
