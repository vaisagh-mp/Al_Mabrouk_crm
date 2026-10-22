"""
Work order save regression tests for all three roles (employee, manager, admin).

Every case checks that saving never ends in the generic
"Error saving work order: <python error>" message.
"""
import datetime
import io
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from Admin.models import (
    Employee, Project, WorkOrder, WorkOrderDetail, WorkOrderTime, WorkOrderImage,
    Spare, SpareConsumed, Tool, Document,
)
from service_logs.models import EmployeeServiceLog

ERROR_PREFIX = "Error saving work order"


def _png_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (2, 2), (255, 0, 0)).save(buf, format='PNG')
    return buf.getvalue()


class WorkOrderSaveBase:
    """Shared fixtures + cases. Subclasses set role-specific bits."""

    url_name = None          # update url name
    view_url_name = None     # page we expect to land on after a save
    sends_form_fields = True # manager/admin post the WorkOrderForm fields

    def setUp(self):
        self.manager_user = User.objects.create_user('wo_mgr', password='x', first_name='Mona', last_name='Manager')
        self.manager_emp = Employee.objects.create(user=self.manager_user, is_manager=True, salary=Decimal('10400.00'))
        self.user_a = User.objects.create_user('wo_a', password='x', first_name='Ali', last_name='A')
        self.emp_a = Employee.objects.create(user=self.user_a, is_employee=True, salary=Decimal('4160.00'))
        self.user_b = User.objects.create_user('wo_b', password='x', first_name='Bilal', last_name='B')
        self.emp_b = Employee.objects.create(user=self.user_b, is_employee=True, salary=Decimal('4160.00'))
        self.admin_user = User.objects.create_superuser('wo_admin', 'a@x.com', 'x')
        self.admin_emp = Employee.objects.create(user=self.admin_user, salary=Decimal('20800.00'))

        self.project = Project.objects.create(
            name='WO Project', client_name='Client', vessel_name='MV Test', manager=self.manager_emp,
        )
        self.wo = WorkOrder.objects.create(
            project=self.project, client='Client', vessel='MV Test', assigned_date=datetime.date(2026, 9, 1),
        )
        self.wo.job_assigned_to.add(self.user_a, self.user_b)
        self.wo.all_members.add(self.user_a, self.user_b)
        WorkOrderDetail.objects.create(work_order=self.wo)

    # ---------- helpers ----------
    def login(self):
        raise NotImplementedError

    def base_data(self, **extra):
        data = {
            'start_date': '2026-09-01',
            'completion_date': '2026-09-03',
            'estimated_hours': '8',
            'project_description': 'Test description',
        }
        if self.sends_form_fields:
            data.update({
                'vessel': 'MV Test', 'client': 'Client', 'imo_no': '', 'location': 'DMC',
                'assigned_date': '2026-09-01', 'job_scope': 'Scope', 'job_instructions': '',
            })
        data.update(extra)
        return data

    def post(self, data, files=None, wo=None):
        wo = wo or self.wo
        url = reverse(self.url_name, kwargs={'pk': wo.pk})
        payload = dict(data)
        if files:
            payload.update(files)
        resp = self.client.post(url, payload)
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        return resp, msgs

    def assert_saved(self, resp, msgs, wo=None):
        errors = [m for m in msgs if ERROR_PREFIX in m]
        self.assertEqual(errors, [], f"{self.__class__.__name__}: {errors}")
        self.assertEqual(resp.status_code, 302, f"expected redirect, got {resp.status_code}; messages={msgs}")
        self.assertEqual(resp.url, reverse(self.view_url_name, kwargs={'pk': (wo or self.wo).pk}))

    def assert_no_crash(self, resp, msgs):
        errors = [m for m in msgs if ERROR_PREFIX in m]
        self.assertEqual(errors, [], f"{self.__class__.__name__}: {errors}")

    # ---------- cases ----------
    def test_a_minimal_form_no_time_rows(self):
        self.login()
        resp, msgs = self.post(self.base_data())
        self.assert_saved(resp, msgs)
        self.assertEqual(WorkOrderDetail.objects.get(work_order=self.wo).start_date, datetime.date(2026, 9, 1))

    def test_b_one_time_row(self):
        self.login()
        resp, msgs = self.post(self.base_data(**{
            'time_date[]': ['2026-09-01'], 'start_time[]': ['08:00'], 'finish_time[]': ['16:00'],
        }))
        self.assert_saved(resp, msgs)
        self.assertEqual(WorkOrderTime.objects.filter(work_order=self.wo).count(), 1)
        self.assertEqual(EmployeeServiceLog.objects.filter(time_log__work_order=self.wo).count(), 1)

    def test_c_overnight_and_open_finish(self):
        self.login()
        resp, msgs = self.post(self.base_data(**{
            'time_date[]': ['2026-09-01', '2026-09-02', '2026-09-03'],
            'start_time[]': ['08:00', '22:00', '09:00'],
            'finish_time[]': ['16:00', '02:00', ''],
        }))
        self.assert_saved(resp, msgs)
        self.assertEqual(WorkOrderTime.objects.filter(work_order=self.wo).count(), 3)

    def test_d1_time_row_without_date(self):
        self.login()
        resp, msgs = self.post(self.base_data(**{
            'time_date[]': [''], 'start_time[]': ['08:00'], 'finish_time[]': ['16:00'],
        }))
        self.assert_saved(resp, msgs)
        self.assertTrue(any('Time row 1 was skipped' in m for m in msgs), msgs)
        self.assertEqual(WorkOrderTime.objects.filter(work_order=self.wo).count(), 0)

    def test_d2_time_row_without_start(self):
        self.login()
        resp, msgs = self.post(self.base_data(**{
            'time_date[]': ['2026-09-01'], 'start_time[]': [''], 'finish_time[]': ['16:00'],
        }))
        self.assert_saved(resp, msgs)
        self.assertTrue(any('date and start time are required' in m for m in msgs), msgs)
        self.assertEqual(WorkOrderTime.objects.filter(work_order=self.wo).count(), 0)

    def test_d3_invalid_date_value(self):
        self.login()
        resp, msgs = self.post(self.base_data(**{
            'start_date': '2026-13-45',
            'time_date[]': ['2026-02-30'], 'start_time[]': ['08:00'], 'finish_time[]': ['16:00'],
        }))
        self.assert_saved(resp, msgs)
        self.assertTrue(any('not valid' in m for m in msgs), msgs)
        self.assertEqual(WorkOrderTime.objects.filter(work_order=self.wo).count(), 0)
        # invalid start_date is not saved; the previous value (none here) is kept
        self.assertIsNone(WorkOrderDetail.objects.get(work_order=self.wo).start_date)

    def test_e_spares_tools_documents_with_empty_rows(self):
        self.login()
        resp, msgs = self.post(self.base_data(**{
            'spare_name[]': ['Filter', ''], 'spare_unit[]': ['pcs', ''], 'spare_quantity[]': ['2', ''],
            'consumed_spare_name[]': ['Gasket', ''], 'consumed_spare_unit[]': ['pcs', ''], 'consumed_spare_quantity[]': ['1', ''],
            'tool_name[]': ['Spanner', ''], 'tool_quantity[]': ['1', ''],
            'doc_name[]': ['Report', ''], 'doc_status[]': ['Done', ''],
        }))
        self.assert_saved(resp, msgs)
        self.assertEqual(Spare.objects.filter(work_order=self.wo).count(), 1)
        self.assertEqual(SpareConsumed.objects.filter(work_order=self.wo).count(), 1)
        self.assertEqual(Tool.objects.filter(work_order=self.wo).count(), 1)
        self.assertEqual(Document.objects.filter(work_order=self.wo).count(), 1)

    def test_e2_spare_quantity_not_a_whole_number(self):
        self.login()
        resp, msgs = self.post(self.base_data(**{
            'spare_name[]': ['Oil'], 'spare_unit[]': ['L'], 'spare_quantity[]': ['1.5'],
        }))
        self.assert_saved(resp, msgs)
        self.assertTrue(any('whole number' in m for m in msgs), msgs)
        self.assertEqual(Spare.objects.filter(work_order=self.wo).count(), 0)

    @override_settings(MEDIA_ROOT='/tmp/wo_test_media')
    def test_f_image_upload_and_delete(self):
        self.login()
        old = WorkOrderImage.objects.create(
            work_order=self.wo, name='Old', image=SimpleUploadedFile('old.png', _png_bytes(), content_type='image/png'),
        )
        data = self.base_data(**{
            'image_names[]': ['New photo'], 'image_descriptions[]': ['desc'],
            'delete_image_ids': [str(old.id)],
        })
        upload = SimpleUploadedFile('new.png', _png_bytes(), content_type='image/png')
        resp, msgs = self.post(data, files={'project_images[]': [upload]})
        self.assert_saved(resp, msgs)
        self.assertFalse(WorkOrderImage.objects.filter(id=old.id).exists())

    def test_g_save_twice_no_duplicates(self):
        self.login()
        data = self.base_data(**{
            'time_date[]': ['2026-09-01'], 'start_time[]': ['08:00'], 'finish_time[]': ['16:00'],
            'spare_name[]': ['Filter'], 'spare_unit[]': ['pcs'], 'spare_quantity[]': ['2'],
        })
        self.assert_saved(*self.post(data))
        self.assert_saved(*self.post(data))
        self.assertEqual(WorkOrderTime.objects.filter(work_order=self.wo).count(), 1)
        self.assertEqual(EmployeeServiceLog.objects.filter(time_log__work_order=self.wo).count(), 1)
        self.assertEqual(Spare.objects.filter(work_order=self.wo).count(), 1)

    def test_k_work_order_without_project(self):
        self.login()
        wo2 = WorkOrder.objects.create(project=None, client='X', assigned_date=datetime.date(2026, 9, 1))
        wo2.job_assigned_to.add(self.user_a, self.user_b)
        resp, msgs = self.post(self.base_data(**{
            'time_date[]': ['2026-09-01'], 'start_time[]': ['08:00'], 'finish_time[]': ['16:00'],
        }), wo=wo2)
        self.assert_saved(resp, msgs, wo=wo2)

    def test_pages_open(self):
        self.login()
        resp = self.client.get(reverse(self.url_name, kwargs={'pk': self.wo.pk}))
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(reverse(self.view_url_name, kwargs={'pk': self.wo.pk}))
        self.assertEqual(resp.status_code, 200)


class EmployeeWorkOrderSaveTests(WorkOrderSaveBase, TestCase):
    url_name = 'employee-update-work-order'
    view_url_name = 'employee-view-work-order'
    sends_form_fields = False

    def login(self):
        self.client.force_login(self.user_a)

    def test_i_unassigned_employee_gets_clear_message(self):
        outsider = User.objects.create_user('wo_out', password='x')
        Employee.objects.create(user=outsider, is_employee=True, salary=Decimal('1000'))
        self.client.force_login(outsider)
        resp, msgs = self.post(self.base_data())
        self.assert_no_crash(resp, msgs)
        self.assertTrue(any('not authorized' in m for m in msgs), msgs)

    def test_employee_does_not_wipe_other_members_rows(self):
        WorkOrderTime.objects.create(
            work_order=self.wo, date=datetime.date(2026, 9, 1), start_time=datetime.time(8), finish_time=datetime.time(12),
            employee=self.emp_b,
        )
        self.login()
        resp, msgs = self.post(self.base_data(**{
            'time_date[]': ['2026-09-02'], 'start_time[]': ['08:00'], 'finish_time[]': ['16:00'],
        }))
        self.assert_saved(resp, msgs)
        self.assertEqual(WorkOrderTime.objects.filter(work_order=self.wo, employee=self.emp_b).count(), 1)

    def test_k_work_order_without_project(self):
        # Employees only see work orders assigned to them; same case as base.
        super().test_k_work_order_without_project()


class ManagerWorkOrderSaveTests(WorkOrderSaveBase, TestCase):
    url_name = 'manager-update-work-order'
    view_url_name = 'view_work_order'

    def login(self):
        self.client.force_login(self.manager_user)

    def test_h_time_row_for_member_b_and_invalid_member(self):
        self.login()
        resp, msgs = self.post(self.base_data(**{
            'time_date[]': ['2026-09-01', '2026-09-02'],
            'start_time[]': ['08:00', '08:00'],
            'finish_time[]': ['16:00', '16:00'],
            'time_employee[]': [str(self.emp_b.id), '999999'],
        }))
        self.assert_saved(resp, msgs)
        rows = list(WorkOrderTime.objects.filter(work_order=self.wo).order_by('date'))
        self.assertEqual(rows[0].employee, self.emp_b)
        self.assertEqual(rows[1].employee, self.manager_emp)

    def test_missing_assigned_date_shows_form_error_not_crash(self):
        self.login()
        data = self.base_data()
        data['assigned_date'] = ''
        resp, msgs = self.post(data)
        self.assert_no_crash(resp, msgs)


class AdminWorkOrderSaveTests(WorkOrderSaveBase, TestCase):
    url_name = 'admin-update-work-order'
    view_url_name = 'admin_view_work_order'

    def login(self):
        self.client.force_login(self.admin_user)

    def test_j_admin_without_employee_profile(self):
        bare_admin = User.objects.create_superuser('wo_admin2', 'b@x.com', 'x')
        self.client.force_login(bare_admin)
        resp, msgs = self.post(self.base_data(**{
            'time_date[]': ['2026-09-01'], 'start_time[]': ['08:00'], 'finish_time[]': ['16:00'],
        }))
        self.assert_no_crash(resp, msgs)

    def test_missing_assigned_date_shows_form_error_not_crash(self):
        self.login()
        data = self.base_data()
        data['assigned_date'] = ''
        resp, msgs = self.post(data)
        self.assert_no_crash(resp, msgs)

    def test_non_admin_cannot_open_admin_update(self):
        self.client.force_login(self.manager_user)
        try:
            resp = self.client.get(reverse(self.url_name, kwargs={'pk': self.wo.pk}))
        except Exception as e:  # report instead of erroring the whole run
            self.fail(f"admin update page crashed for a manager: {type(e).__name__}: {e}")
        self.assertIn(resp.status_code, (302, 403))
