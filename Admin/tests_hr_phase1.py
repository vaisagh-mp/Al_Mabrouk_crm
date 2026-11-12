import datetime
from decimal import Decimal
from django.apps import apps
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from Admin.forms import EmployeeCreationForm, ManagerEmployeeUpdateForm
from Admin.models import (
    Employee, Department, Branch, SalaryStructure, EmployeeChangeLog,
    generate_next_employee_code, Project, Team, Attendance, Leave,
    update_employee_salary_structure, log_employee_changes
)
from Admin.permissions import can_view_salary, can_manage_hr_settings
from Admin.staff_lifecycle import deactivate_employee, reactivate_employee
import importlib

migration_0013 = importlib.import_module("Admin.migrations.0013_assign_employee_codes_and_salary_structures")
migrate_employee_codes_and_salary = migration_0013.migrate_employee_codes_and_salary
from finance.models import Payroll
from finance.views import generate_payroll
from service_logs.models import EmployeeServiceLog
from Admin.attendance_register import build_attendance_register


class HRPhase1MigrationAndCodeTests(TestCase):
    def setUp(self):
        # Clean any preexisting employees if needed
        pass

    def test_migration_numbering_and_sequential_assignment(self):
        """
        - Migration numbering: existing staff numbered by date_of_join
          (oldest first; no joining date -> after the others, by id).
        - New employee gets next number.
        - Two creates in a row never duplicate.
        """
        # Create 4 test users & employees with no codes initially
        u1 = User.objects.create_user('user_old', 'old@amms.ae', 'pass1')
        e1 = Employee.objects.create(user=u1, date_of_join=datetime.date(2021, 5, 10), salary=Decimal('3000.00'))

        u2 = User.objects.create_user('user_older', 'older@amms.ae', 'pass2')
        e2 = Employee.objects.create(user=u2, date_of_join=datetime.date(2020, 1, 15), salary=Decimal('4000.00'))

        u3 = User.objects.create_user('user_nojoin1', 'nj1@amms.ae', 'pass3')
        e3 = Employee.objects.create(user=u3, date_of_join=None, salary=Decimal('5000.00'))

        u4 = User.objects.create_user('user_nojoin2', 'nj2@amms.ae', 'pass4')
        e4 = Employee.objects.create(user=u4, date_of_join=None, salary=Decimal('6000.00'))

        # Clear codes in case auto-generation ran on create
        Employee.objects.filter(id__in=[e1.id, e2.id, e3.id, e4.id]).update(employee_code=None)
        SalaryStructure.objects.filter(employee__in=[e1, e2, e3, e4]).delete()

        # Run migration logic
        migrate_employee_codes_and_salary(apps, None)

        e1.refresh_from_db()
        e2.refresh_from_db()
        e3.refresh_from_db()
        e4.refresh_from_db()

        # Oldest date_of_join (2020-01-15) must have AMMS-0001
        self.assertEqual(e2.employee_code, 'AMMS-0001')
        # Second oldest (2021-05-10) must have AMMS-0002
        self.assertEqual(e1.employee_code, 'AMMS-0002')
        # Null date_of_join ordered by id
        if e3.id < e4.id:
            self.assertEqual(e3.employee_code, 'AMMS-0003')
            self.assertEqual(e4.employee_code, 'AMMS-0004')
        else:
            self.assertEqual(e4.employee_code, 'AMMS-0003')
            self.assertEqual(e3.employee_code, 'AMMS-0004')

        # Check SalaryStructure was created by migration
        self.assertTrue(SalaryStructure.objects.filter(employee=e2, total=Decimal('4000.00')).exists())
        self.assertTrue(SalaryStructure.objects.filter(employee=e1, total=Decimal('3000.00')).exists())

        # Next employee created gets next number
        u_new1 = User.objects.create_user('user_next1', 'n1@amms.ae', 'pass')
        e_new1 = Employee.objects.create(user=u_new1, rank='Technician', salary=Decimal('2500.00'))
        self.assertEqual(e_new1.employee_code, 'AMMS-0005')

        # Sequential creates never duplicate
        u_new2 = User.objects.create_user('user_next2', 'n2@amms.ae', 'pass')
        e_new2 = Employee.objects.create(user=u_new2, rank='Technician', salary=Decimal('2500.00'))
        self.assertEqual(e_new2.employee_code, 'AMMS-0006')
        self.assertNotEqual(e_new1.employee_code, e_new2.employee_code)


class HRPhase1AddEmployeeTests(TestCase):
    def setUp(self):
        self.client = Client()
        # Admin / Superuser
        self.admin_user = User.objects.create_superuser('admin_user', 'admin@amms.ae', 'pass1234')
        self.admin_emp = Employee.objects.create(user=self.admin_user, is_manager=True, salary=Decimal('10000.00'))

        # HR User
        self.hr_user = User.objects.create_user('hr_user', 'hr@amms.ae', 'pass1234', is_staff=True)
        self.hr_emp = Employee.objects.create(user=self.hr_user, is_hr=True, salary=Decimal('8000.00'))

        # Manager user for reporting manager dropdown
        self.mgr_user = User.objects.create_user('mgr_active', 'mgr@amms.ae', 'pass1234', is_staff=True)
        self.mgr_emp = Employee.objects.create(user=self.mgr_user, is_manager=True, rank='Operations Manager')

        # Master data
        self.dept = Department.objects.create(name='Marine Engineering', is_active=True, sort_order=1)
        self.branch = Branch.objects.create(name='Dubai Maritime City', is_active=True, sort_order=1)

    def test_add_employee_hr_all_four_sections_saved(self):
        self.client.login(username='hr_user', password='pass1234')
        url = reverse('hr_create_employee')

        post_data = {
            # Section 1 Personal
            'first_name': 'Ahmed',
            'last_name': 'Mansoor',
            'personal_email': 'ahmed.m@example.com',
            'company_email': 'ahmed@amms.ae',
            'phone_number': '+971501234567',
            'date_of_birth': '1990-06-15',
            'nationality': 'Emirati',
            'emergency_contact_number': '+971509999999',
            'address': 'Al Barsha, Dubai',
            # Section 2 Employment
            'department': self.dept.id,
            'branch': self.branch.id,
            'rank': 'Senior Surveyor',
            'reporting_manager': self.mgr_emp.id,
            'date_of_join': '2026-01-10',
            'employment_status': 'ACTIVE',
            'work_type': 'OFFICE_VESSEL',
            'contract_start_date': '2026-01-10',
            'contract_end_date': '2028-01-09',
            # Section 3 Salary
            'basic': '5000.00',
            'housing_allowance': '2000.00',
            'transport_allowance': '1000.00',
            'other_allowances': '500.00',
            # Section 4 CRM Login
            'username': 'ahmed_m',
            'role': 'employee',
            'password': 'SecurePassword123!',
            'password2': 'SecurePassword123!',
            'is_active': 'on',
        }

        response = self.client.post(url, post_data)
        # Should redirect to employee list
        self.assertRedirects(response, reverse('hr_employee_list'))

        # Check DB
        emp = Employee.objects.get(user__username='ahmed_m')
        self.assertEqual(emp.user.first_name, 'Ahmed')
        self.assertEqual(emp.user.last_name, 'Mansoor')
        self.assertEqual(emp.personal_email, 'ahmed.m@example.com')
        self.assertEqual(emp.company_email, 'ahmed@amms.ae')
        self.assertEqual(emp.nationality, 'Emirati')
        self.assertEqual(emp.department, self.dept)
        self.assertEqual(emp.branch, self.branch)
        self.assertEqual(emp.reporting_manager, self.mgr_emp)
        self.assertEqual(emp.work_type, 'OFFICE_VESSEL')
        self.assertEqual(emp.employment_status, 'ACTIVE')
        self.assertEqual(emp.rank, 'Senior Surveyor')
        self.assertEqual(emp.salary, Decimal('8500.00'))
        self.assertTrue(emp.employee_code.startswith('AMMS-'))

        # Salary Structure
        ss = emp.salary_structures.first()
        self.assertIsNotNone(ss)
        self.assertEqual(ss.basic, Decimal('5000.00'))
        self.assertEqual(ss.housing_allowance, Decimal('2000.00'))
        self.assertEqual(ss.transport_allowance, Decimal('1000.00'))
        self.assertEqual(ss.other_allowances, Decimal('500.00'))
        self.assertEqual(ss.total, Decimal('8500.00'))

        # Appears in employee list
        list_resp = self.client.get(reverse('hr_employee_list'))
        self.assertContains(list_resp, 'Ahmed')
        self.assertContains(list_resp, emp.employee_code)

    def test_add_employee_admin_save_and_add_another(self):
        self.client.login(username='admin_user', password='pass1234')
        url = reverse('create-employee')

        post_data = {
            'first_name': 'Fatima',
            'last_name': 'Zahra',
            'personal_email': 'fatima@example.com',
            'phone_number': '+971502223344',
            'rank': 'HR Officer',
            'employment_status': 'PROBATION',
            'work_type': 'OFFICE',
            'basic': '6000.00',
            'housing_allowance': '0.00',
            'transport_allowance': '0.00',
            'other_allowances': '0.00',
            'username': 'fatima_z',
            'role': 'hr',
            'password': 'Password123!',
            'password2': 'Password123!',
            'is_active': 'on',
            'save_and_add_another': '1',
        }

        response = self.client.post(url, post_data)
        # Should redirect back to create form
        self.assertRedirects(response, reverse('create-employee'))

        # Follow redirect and check empty form and success message
        follow_resp = self.client.get(reverse('create-employee'))
        self.assertEqual(follow_resp.status_code, 200)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any('Employee created successfully' in m.message for m in messages))

        # Check DB
        emp = Employee.objects.get(user__username='fatima_z')
        self.assertTrue(emp.is_hr)
        self.assertEqual(emp.salary, Decimal('6000.00'))

        # Appears in admin employee list
        admin_list = self.client.get(reverse('employee_list'))
        self.assertContains(admin_list, 'Fatima')
        self.assertContains(admin_list, emp.employee_code)

    def test_add_employee_account_inactive_cannot_login(self):
        self.client.login(username='admin_user', password='pass1234')
        url = reverse('create-employee')

        post_data = {
            'first_name': 'Inactive',
            'last_name': 'User',
            'personal_email': 'inactive@example.com',
            'rank': 'Trainee',
            'basic': '3000.00',
            'username': 'inactive_user',
            'role': 'employee',
            'password': 'Pass1234Secret!',
            'password2': 'Pass1234Secret!',
            'employment_status': 'ACTIVE',
            'work_type': 'OFFICE',
            # Note: is_active is omitted (unticked checkbox)
        }

        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        emp = Employee.objects.get(user__username='inactive_user')
        self.assertFalse(emp.user.is_active)

        # Login attempt must fail
        self.client.logout()
        login_success = self.client.login(username='inactive_user', password='Pass1234Secret!')
        self.assertFalse(login_success)


class HRPhase1SalaryAndHistoryTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.hr_user = User.objects.create_user('hr_admin', 'hr_adm@amms.ae', 'pass1234', is_staff=True)
        self.hr_emp = Employee.objects.create(user=self.hr_user, is_hr=True, salary=Decimal('8000.00'))

    def test_salary_structure_create_and_edit_with_closing_date(self):
        """
        - Salary: create with basic 3000 + housing 1000 + transport 500 -> total 4500 and employee.salary 4500
        - Edit to basic 3500 from 2026-12-01 -> old structure closed 2026-11-30, new one current from 2026-12-01,
          employee.salary updated; history rows written.
        """
        self.client.login(username='hr_admin', password='pass1234')

        # 1. Create employee
        create_resp = self.client.post(reverse('hr_create_employee'), {
            'first_name': 'Carlos',
            'last_name': 'Silva',
            'personal_email': 'carlos@amms.ae',
            'rank': 'Marine Engineer',
            'basic': '3000.00',
            'housing_allowance': '1000.00',
            'transport_allowance': '500.00',
            'other_allowances': '0.00',
            'username': 'carlos_s',
            'role': 'employee',
            'password': 'SecretPassword123!',
            'password2': 'SecretPassword123!',
            'is_active': 'on',
            'date_of_join': '2026-06-01',
            'employment_status': 'ACTIVE',
            'work_type': 'OFFICE_VESSEL',
        })
        self.assertEqual(create_resp.status_code, 302)

        emp = Employee.objects.get(user__username='carlos_s')
        self.assertEqual(emp.salary, Decimal('4500.00'))

        s1 = emp.salary_structures.first()
        self.assertEqual(s1.basic, Decimal('3000.00'))
        self.assertEqual(s1.housing_allowance, Decimal('1000.00'))
        self.assertEqual(s1.transport_allowance, Decimal('500.00'))
        self.assertEqual(s1.total, Decimal('4500.00'))
        self.assertEqual(s1.effective_from, datetime.date(2026, 6, 1))
        self.assertIsNone(s1.effective_to)

        # 2. Edit salary to basic 3500 from today
        today = timezone.localdate()
        edit_url = reverse('hr_edit_employee', args=[emp.id])
        edit_resp = self.client.post(edit_url, {
            'username': 'carlos_s',
            'first_name': 'Carlos',
            'last_name': 'Silva',
            'personal_email': 'carlos@amms.ae',
            'rank': 'Senior Marine Engineer',
            'role': 'employee',
            'is_active': 'on',
            'employment_status': 'ACTIVE',
            'work_type': 'OFFICE_VESSEL',
            'basic': '3500.00',
            'housing_allowance': '1000.00',
            'transport_allowance': '500.00',
            'other_allowances': '0.00',
            'effective_from': today.strftime('%Y-%m-%d'),
        })
        self.assertEqual(edit_resp.status_code, 302)

        emp.refresh_from_db()
        # Effective today -> employee.salary updated immediately to 5000.00
        self.assertEqual(emp.salary, Decimal('5000.00'))

        # Check old structure closed on day before today
        s1.refresh_from_db()
        self.assertEqual(s1.effective_to, today - datetime.timedelta(days=1))

        # Check new structure active from today
        s2 = emp.salary_structures.order_by('-created_at').first()
        self.assertNotEqual(s1.id, s2.id)
        self.assertEqual(s2.basic, Decimal('3500.00'))
        self.assertEqual(s2.total, Decimal('5000.00'))
        self.assertEqual(s2.effective_from, today)
        self.assertIsNone(s2.effective_to)

        # Check change logs were recorded
        logs = EmployeeChangeLog.objects.filter(employee=emp)
        self.assertTrue(logs.filter(field='salary', old_value='4500.00', new_value='5000.00').exists())
        self.assertTrue(logs.filter(field='rank', old_value='Marine Engineer', new_value='Senior Marine Engineer').exists())


class HRPhase1SalaryVisibilityTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Target employee with salary 7000
        self.target_user = User.objects.create_user('target_emp', 'target@amms.ae', 'pass1234')
        self.target_emp = Employee.objects.create(
            user=self.target_user,
            is_employee=True,
            rank='Electrician',
            salary=Decimal('7000.00'),
            date_of_join=datetime.date(2025, 1, 1)
        )
        SalaryStructure.objects.create(
            employee=self.target_emp,
            basic=Decimal('5000.00'),
            housing_allowance=Decimal('1500.00'),
            transport_allowance=Decimal('500.00'),
            total=Decimal('7000.00'),
            effective_from=datetime.date(2025, 1, 1)
        )

        # Superuser
        self.admin_user = User.objects.create_superuser('super_adm', 'super@amms.ae', 'pass1234')
        self.admin_emp = Employee.objects.create(user=self.admin_user, is_manager=True)

        # HR User
        self.hr_user = User.objects.create_user('hr_usr', 'hr@amms.ae', 'pass1234', is_staff=True)
        self.hr_emp = Employee.objects.create(user=self.hr_user, is_hr=True)

        # Accountant User
        self.acc_user = User.objects.create_user('acc_usr', 'acc@amms.ae', 'pass1234', is_staff=True)
        self.acc_emp = Employee.objects.create(user=self.acc_user, is_accountant=True)

        # Manager User (NOT HR, NOT Accountant, NOT superuser)
        self.mgr_user = User.objects.create_user('mgr_usr', 'mgr@amms.ae', 'pass1234', is_staff=True)
        self.mgr_emp = Employee.objects.create(user=self.mgr_user, is_manager=True)

        # Plain Employee User
        self.plain_user = User.objects.create_user('plain_usr', 'plain@amms.ae', 'pass1234')
        self.plain_emp = Employee.objects.create(user=self.plain_user, is_employee=True)

    def test_permission_helper(self):
        self.assertTrue(can_view_salary(self.admin_user))
        self.assertTrue(can_view_salary(self.hr_user))
        self.assertTrue(can_view_salary(self.acc_user))
        self.assertFalse(can_view_salary(self.mgr_user))
        self.assertFalse(can_view_salary(self.plain_user))
        self.assertFalse(can_view_salary(None))

    def test_manager_cannot_see_salary_in_profile_and_edit_page(self):
        self.client.login(username='mgr_usr', password='pass1234')

        # View profile
        resp = self.client.get(reverse('employee_profile', args=[self.target_emp.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, '7000')
        self.assertNotContains(resp, 'Salary')

        # Form initialized for manager has salary fields dropped
        form = ManagerEmployeeUpdateForm(instance=self.target_emp, user=self.mgr_user)
        self.assertFalse(form.can_edit_salary)
        self.assertNotIn('basic', form.fields)
        self.assertNotIn('housing_allowance', form.fields)
        self.assertNotIn('transport_allowance', form.fields)
        self.assertNotIn('other_allowances', form.fields)
        self.assertNotIn('salary', form.fields)

    def test_unauthorized_posted_salary_is_ignored(self):
        # A non-salary user posting salary values must have them ignored
        form = ManagerEmployeeUpdateForm(
            data={
                'username': self.target_user.username,
                'first_name': 'Target',
                'last_name': 'Updated',
                'rank': 'Electrician',
                'role': 'employee',
                'is_active': True,
                'basic': Decimal('15000.00'),  # Tampering attempt
                'housing_allowance': Decimal('5000.00'),
                'salary': Decimal('20000.00'),
            },
            instance=self.target_emp,
            user=self.mgr_user
        )
        self.assertFalse(form.can_edit_salary)
        self.assertNotIn('basic', form.fields)
        self.assertNotIn('salary', form.fields)
        self.assertTrue(form.is_valid())
        form.save()

        self.target_emp.refresh_from_db()
        # Salary must stay 7000.00, untouched
        self.assertEqual(self.target_emp.salary, Decimal('7000.00'))

    def test_authorized_users_see_and_edit_salary(self):
        # HR sees salary in profile
        self.client.login(username='hr_usr', password='pass1234')
        resp = self.client.get(reverse('hr_employee_profile', args=[self.target_emp.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '7000')

        # Superuser sees salary in Admin profile
        self.client.login(username='super_adm', password='pass1234')
        resp_admin = self.client.get(reverse('employee_profile', args=[self.target_emp.id]))
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, '7000')


class HRPhase1RoleAndReportingManagerTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.hr_user = User.objects.create_user('hr_test', 'hr@amms.ae', 'pass1234', is_staff=True)
        self.hr_emp = Employee.objects.create(user=self.hr_user, is_hr=True, salary=Decimal('8000.00'))

        self.test_user = User.objects.create_user('role_test_usr', 'rt@amms.ae', 'pass1234')
        self.test_emp = Employee.objects.create(
            user=self.test_user,
            is_employee=True,
            rank='Junior Tech',
            salary=Decimal('3000.00')
        )

    def test_hr_edit_saves_role(self):
        """Regression test: hr_edit_employee previously never saved the role."""
        self.client.login(username='hr_test', password='pass1234')
        url = reverse('hr_edit_employee', args=[self.test_emp.id])

        self.assertTrue(self.test_emp.is_employee)
        self.assertFalse(self.test_emp.is_manager)

        # Update role to manager
        resp = self.client.post(url, {
            'username': self.test_emp.user.username,
            'first_name': 'Role',
            'last_name': 'Tester',
            'personal_email': 'rt@amms.ae',
            'rank': 'Field Manager',
            'role': 'manager',
            'is_active': 'on',
            'employment_status': 'ACTIVE',
            'work_type': 'OFFICE',
            'basic': '3000.00',
        })
        self.assertEqual(resp.status_code, 302)

        self.test_emp.refresh_from_db()
        self.test_emp.user.refresh_from_db()

        self.assertFalse(self.test_emp.is_employee)
        self.assertTrue(self.test_emp.is_manager)
        self.assertTrue(self.test_emp.user.is_staff)

    def test_reporting_manager_cannot_be_self(self):
        form = ManagerEmployeeUpdateForm(
            data={
                'first_name': 'Self',
                'last_name': 'Manager',
                'rank': 'Manager',
                'role': 'manager',
                'reporting_manager': self.test_emp.id,  # setting to self
                'is_active': True,
            },
            instance=self.test_emp,
            user=self.hr_user
        )
        self.assertFalse(form.is_valid())
        self.assertIn('reporting_manager', form.errors)
        self.assertIn('cannot be their own reporting manager', str(form.errors['reporting_manager']))


class HRPhase1DepartmentsAndBranchesTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.hr_user = User.objects.create_user('hr_master', 'hrm@amms.ae', 'pass1234', is_staff=True)
        self.hr_emp = Employee.objects.create(user=self.hr_user, is_hr=True)

        self.mgr_user = User.objects.create_user('mgr_master', 'mgrm@amms.ae', 'pass1234', is_staff=True)
        self.mgr_emp = Employee.objects.create(user=self.mgr_user, is_manager=True)

    def test_hr_can_add_edit_and_deactivate_departments_and_branches(self):
        self.client.login(username='hr_master', password='pass1234')

        # Add Department
        dep_resp = self.client.post(reverse('hr_department_create'), {
            'name': 'Survey & Inspection',
            'is_active': True,
            'sort_order': 1
        })
        self.assertEqual(dep_resp.status_code, 302)
        dept = Department.objects.get(name='Survey & Inspection')
        self.assertTrue(dept.is_active)

        # Edit Department
        edit_dep_resp = self.client.post(reverse('hr_department_edit', args=[dept.id]), {
            'name': 'Marine Survey & Inspection',
            'is_active': True,
            'sort_order': 2
        })
        self.assertEqual(edit_dep_resp.status_code, 302)
        dept.refresh_from_db()
        self.assertEqual(dept.name, 'Marine Survey & Inspection')
        self.assertEqual(dept.sort_order, 2)

        # Toggle Deactivate Department
        toggle_resp = self.client.post(reverse('hr_department_toggle_active', args=[dept.id]))
        self.assertEqual(toggle_resp.status_code, 302)
        dept.refresh_from_db()
        self.assertFalse(dept.is_active)

        # Inactive department must not be offered in EmployeeCreationForm
        form = EmployeeCreationForm(user=self.hr_user)
        self.assertNotIn(dept, form.fields['department'].queryset)

        # Add Branch
        br_resp = self.client.post(reverse('hr_branch_create'), {
            'name': 'Abu Dhabi Office',
            'is_active': True,
            'sort_order': 1
        })
        self.assertEqual(br_resp.status_code, 302)
        branch = Branch.objects.get(name='Abu Dhabi Office')

        # Toggle Branch
        self.client.post(reverse('hr_branch_toggle_active', args=[branch.id]))
        branch.refresh_from_db()
        self.assertFalse(branch.is_active)

        # Inactive branch not in queryset
        form = EmployeeCreationForm(user=self.hr_user)
        self.assertNotIn(branch, form.fields['branch'].queryset)

    def test_non_hr_gets_403_on_hr_settings(self):
        self.client.login(username='mgr_master', password='pass1234')
        resp = self.client.get(reverse('hr_departments'))
        self.assertEqual(resp.status_code, 403)

        resp2 = self.client.get(reverse('hr_branches'))
        self.assertEqual(resp2.status_code, 403)

        resp3 = self.client.post(reverse('hr_department_create'), {'name': 'Hack Dept'})
        self.assertEqual(resp3.status_code, 403)

    def test_cannot_hard_delete_department_or_branch_in_use(self):
        self.client.login(username='hr_master', password='pass1234')
        dept = Department.objects.create(name='Operations', is_active=True)
        branch = Branch.objects.create(name='Main Port', is_active=True)

        user = User.objects.create_user('dep_emp', 'de@amms.ae', 'pass')
        emp = Employee.objects.create(user=user, department=dept, branch=branch, rank='Officer')

        # Attempt to delete department
        del_dep_resp = self.client.post(reverse('hr_department_delete', args=[dept.id]))
        self.assertEqual(del_dep_resp.status_code, 302)
        self.assertTrue(Department.objects.filter(id=dept.id).exists())

        # Attempt to delete branch
        del_br_resp = self.client.post(reverse('hr_branch_delete', args=[branch.id]))
        self.assertEqual(del_br_resp.status_code, 302)
        self.assertTrue(Branch.objects.filter(id=branch.id).exists())


class HRPhase1NoCalculationChangesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin_calc', 'adm_c@amms.ae', 'pass')
        self.admin_emp = Employee.objects.create(user=self.admin, salary=Decimal('15000.00'))

        self.user = User.objects.create_user('worker_calc', 'worker@amms.ae', 'pass')
        self.emp = Employee.objects.create(
            user=self.user,
            is_employee=True,
            salary=Decimal('2600.00'),
            date_of_join=datetime.date(2026, 1, 1),
            rank='Technician'
        )
        # Create salary structure with basic 2000, allowances 600 -> total 2600
        SalaryStructure.objects.create(
            employee=self.emp,
            basic=Decimal('2000.00'),
            housing_allowance=Decimal('400.00'),
            transport_allowance=Decimal('200.00'),
            total=Decimal('2600.00'),
            effective_from=datetime.date(2026, 1, 1)
        )

        self.project = Project.objects.create(
            name='Test Vessel Project',
            status='ONGOING',
            manager=self.admin_emp,
        )

    def test_payroll_and_absence_calculation_identical(self):
        """
        Tests payroll calculation: September 2026 (26 working days).
        Employee salary = 2600. Daily rate = 100.
        2 worked days -> 24 unpaid days -> absence deduction = 2400. Net pay = 200.
        """
        sep = datetime.date(2026, 9, 1)
        # 2 worked days
        EmployeeServiceLog.objects.create(
            employee=self.emp, project=self.project, date=datetime.date(2026, 9, 1),
            vessel_name='Vessel A', start_time=datetime.time(8, 0), end_time=datetime.time(16, 0),
        )
        EmployeeServiceLog.objects.create(
            employee=self.emp, project=self.project, date=datetime.date(2026, 9, 2),
            vessel_name='Vessel A', start_time=datetime.time(8, 0), end_time=datetime.time(16, 0),
        )

        generate_payroll(sep, self.admin)
        p = Payroll.objects.get(employee=self.emp, month=sep)
        self.assertEqual(p.basic_salary, Decimal('2600.00'))
        self.assertEqual(p.absent_days_unpaid, Decimal('24'))
        self.assertEqual(p.absence_deduction, Decimal('2400.00'))
        self.assertEqual(p.net_pay, Decimal('200.00'))

    def test_overtime_calculation_identical(self):
        """
        Employee with salary 2600. Hourly rate = 2600 / 26 / 8 = 12.50.
        12 hours on weekday -> 2h OT x 12.50 x 1.25 = 31.25.
        """
        log = EmployeeServiceLog.objects.create(
            employee=self.emp,
            project=self.project,
            date=datetime.date(2026, 9, 7),  # Monday
            vessel_name='Vessel B',
            start_time=datetime.time(8, 0),
            end_time=datetime.time(20, 0),
        )
        self.assertEqual(log.total_hours, Decimal('12.00'))
        self.assertEqual(log.ot_hours, Decimal('2.00'))
        self.assertEqual(log.ot_type, 'WEEKDAY')
        self.assertEqual(log.ot_cost, Decimal('31.25'))

    def test_attendance_register_rows_identical(self):
        """Attendance register reflects presence without issue."""
        test_date = datetime.date(2026, 9, 1)
        EmployeeServiceLog.objects.create(
            employee=self.emp,
            project=self.project,
            date=test_date,
            vessel_name='Vessel C',
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
        )
        rows = build_attendance_register(test_date, test_date, search=self.user.username)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['employee'].id, self.emp.id)
        self.assertEqual(row['total_hours'], Decimal('8.00'))
        self.assertEqual(row['status_code'], 'VESSEL')


class HRPhase1VerificationFixesTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser('admin_vfix', 'admin_vfix@amms.ae', 'pass1234')
        self.admin_emp = Employee.objects.create(user=self.admin_user, is_manager=True, salary=Decimal('10000.00'))

    def test_admin_edit_manager_legacy_post_preserves_role_and_active(self):
        """
        Posting exactly the fields the old edit_manager page sends
        keeps manager active and is_manager=True.
        """
        mgr_user = User.objects.create_user('old_mgr_user', 'oldmgr@amms.ae', 'pass123', is_staff=True)
        mgr_emp = Employee.objects.create(
            user=mgr_user,
            is_manager=True,
            is_employee=False,
            is_administration=False,
            is_hr=False,
            is_accountant=False,
            salary=Decimal('6000.00'),
            phone_number='1234567890',
            rank='Operations Manager'
        )
        self.assertTrue(mgr_user.is_active)
        self.assertTrue(mgr_emp.is_manager)

        client = Client()
        client.force_login(self.admin_user)

        # Fields sent by old edit_manager template (no role, no is_active)
        old_post_data = {
            'first_name': 'UpdatedFirst',
            'last_name': 'UpdatedLast',
            'email': 'updated_oldmgr@amms.ae',
            'phone_number': '9876543210',
            'rank': 'Senior Operations Manager',
            'salary': '6000.00',
            'date_of_birth': '1985-05-15',
            'date_of_join': '2021-03-01',
            'work_days': '26',
            'overseas_days': '2',
            'address': 'Office 202',
        }
        resp = client.post(reverse('admin_edit_manager', args=[mgr_emp.id]), old_post_data)
        self.assertEqual(resp.status_code, 302)

        mgr_emp.refresh_from_db()
        mgr_user.refresh_from_db()
        self.assertTrue(mgr_user.is_active)
        self.assertTrue(mgr_emp.is_manager)
        self.assertFalse(mgr_emp.is_employee)
        self.assertEqual(mgr_emp.get_role(), 'Manager')
        self.assertEqual(mgr_emp.rank, 'Senior Operations Manager')

    def test_self_profile_email_update_syncs_personal_email(self):
        """
        Employee changes email in My Profile -> user.email and personal_email
        both equal the new value.
        """
        u = User.objects.create_user('emp_self_test', 'initial_email@amms.ae', 'pass123')
        emp = Employee.objects.create(user=u, personal_email='initial_email@amms.ae', phone_number='12345')

        client = Client()
        client.force_login(u)

        resp = client.post(reverse('update_profile'), {
            'first_name': 'SelfFirst',
            'last_name': 'SelfLast',
            'email': 'updated_personal@amms.ae',
            'phone_number': '12345',
            'password': '',
            'confirm_password': '',
        })
        self.assertEqual(resp.status_code, 302)

        u.refresh_from_db()
        emp.refresh_from_db()
        self.assertEqual(u.email, 'updated_personal@amms.ae')
        self.assertEqual(emp.personal_email, 'updated_personal@amms.ae')

    def test_future_dated_salary_and_apply_salary_structures(self):
        """
        Future salary structure does not change employee.salary immediately.
        apply_salary_structures command sets employee.salary when structure becomes current.
        Upcoming banner is displayed on profile.
        """
        today = timezone.localdate()
        u = User.objects.create_user('future_sal_user', 'futuresal@amms.ae', 'pass')
        emp = Employee.objects.create(user=u, salary=Decimal('5000.00'), date_of_join=today)
        SalaryStructure.objects.create(
            employee=emp,
            basic=Decimal('5000.00'),
            total=Decimal('5000.00'),
            effective_from=today - datetime.timedelta(days=30)
        )

        future_date = today + datetime.timedelta(days=15)
        future_struct = update_employee_salary_structure(
            employee=emp,
            basic=Decimal('7000.00'),
            housing_allowance=Decimal('1000.00'),
            effective_from=future_date,
            user=self.admin_user,
            notes="Promotion next month"
        )

        emp.refresh_from_db()
        # Salary must remain unchanged
        self.assertEqual(emp.salary, Decimal('5000.00'))

        # Upcoming banner
        self.assertIsNotNone(emp.upcoming_salary_structure)
        self.assertEqual(emp.upcoming_salary_structure.total, Decimal('8000.00'))
        self.assertEqual(emp.upcoming_salary_structure.effective_from, future_date)

        client = Client()
        client.force_login(self.admin_user)
        profile_resp = client.get(reverse('employee_profile', args=[emp.id]))
        self.assertEqual(profile_resp.status_code, 200)
        expected_upcoming = f"Upcoming: AED 8000.00 from {future_date.strftime('%d/%m/%Y')}"
        self.assertContains(profile_resp, expected_upcoming)

        # Dry run of management command
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('apply_salary_structures', '--dry-run', stdout=out)
        emp.refresh_from_db()
        self.assertEqual(emp.salary, Decimal('5000.00'))

        # Advance effective_from to today without model save() (simulating date arriving naturally)
        SalaryStructure.objects.filter(pk=future_struct.pk).update(effective_from=today)

        emp.refresh_from_db()
        self.assertEqual(emp.current_salary_structure.total, Decimal('8000.00'))
        self.assertEqual(emp.salary, Decimal('5000.00'))

        # Run command for real
        call_command('apply_salary_structures', stdout=out)
        emp.refresh_from_db()
        self.assertEqual(emp.salary, Decimal('8000.00'))

        # Verify change log entry
        log = EmployeeChangeLog.objects.filter(employee=emp, source='Scheduled salary change').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.field, 'salary')
        self.assertEqual(log.old_value, '5000.00')
        self.assertEqual(log.new_value, '8000.00')

        # Idempotency check: second run should make no changes
        call_command('apply_salary_structures', stdout=out)
        self.assertEqual(EmployeeChangeLog.objects.filter(employee=emp, source='Scheduled salary change').count(), 1)

    def test_backdated_salary_rejected_and_effective_to_validation(self):
        """
        Backdated salary earlier than current open structure's effective_from is rejected.
        Never save a structure whose effective_to < effective_from.
        """
        today = timezone.localdate()
        u = User.objects.create_user('backdated_user', 'backdated@amms.ae', 'pass')
        emp = Employee.objects.create(user=u, salary=Decimal('4000.00'), date_of_join=today - datetime.timedelta(days=60))
        struct = SalaryStructure.objects.create(
            employee=emp,
            basic=Decimal('4000.00'),
            total=Decimal('4000.00'),
            effective_from=datetime.date(2026, 5, 1)
        )

        form_data = {
            'username': u.username,
            'first_name': 'Back',
            'last_name': 'Dated',
            'personal_email': 'backdated@amms.ae',
            'role': 'employee',
            'is_active': 'on',
            'basic': '5000.00',
            'housing_allowance': '0.00',
            'transport_allowance': '0.00',
            'other_allowances': '0.00',
            'effective_from': '2026-04-15',  # Earlier than 2026-05-01
        }
        form = ManagerEmployeeUpdateForm(data=form_data, instance=emp, user=self.admin_user)
        self.assertFalse(form.is_valid())
        self.assertIn('effective_from', form.errors)
        self.assertIn("Start date must be on or after 01/05/2026, the start of the current salary.", form.errors['effective_from'])

        # Saving directly with effective_to < effective_from raises ValueError
        with self.assertRaises(ValueError):
            SalaryStructure.objects.create(
                employee=emp,
                basic=Decimal('3000.00'),
                total=Decimal('3000.00'),
                effective_from=datetime.date(2026, 6, 1),
                effective_to=datetime.date(2026, 5, 31)
            )


class HRPhase1AccountActiveRegressionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser('admin_act_test', 'admin_act@amms.ae', 'pass1234')
        self.admin_emp = Employee.objects.create(user=self.admin_user, is_manager=True, salary=Decimal('10000.00'))
        self.client.force_login(self.admin_user)

    def _create_employee(self, username, is_manager=False, is_active=True):
        u = User.objects.create_user(username, f'{username}@amms.ae', 'pass123', is_active=is_active)
        emp = Employee.objects.create(
            user=u,
            is_manager=is_manager,
            is_employee=not is_manager,
            personal_email=f'{username}@amms.ae',
            phone_number='12345678',
            rank='Manager' if is_manager else 'Technician',
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2025, 1, 1),
            deactivated_at=timezone.now() if not is_active else None
        )
        return emp

    def test_box_unchecked_deactivates_and_closes_sessions_across_all_three_pages(self):
        """
        1. Admin Edit Employee, HR Edit Employee, Admin Edit Manager:
        box unchecked -> user.is_active False, deactivate_employee called
        (deactivated_at set, open sessions closed at midnight).
        """
        pages = [
            ('admin_edit_employee', False),
            ('hr_edit_employee', False),
            ('admin_edit_manager', True),
        ]
        for idx, (url_name, is_mgr) in enumerate(pages):
            emp = self._create_employee(f'act_uncheck_{idx}', is_manager=is_mgr, is_active=True)
            # Create open attendance session
            login_time = timezone.now() - datetime.timedelta(hours=3)
            att = Attendance.objects.create(
                employee=emp,
                login_time=login_time,
                log_out_time=None
            )
            self.assertIsNone(att.log_out_time)

            url = reverse(url_name, args=[emp.id])
            # Render edit page
            get_resp = self.client.get(url)
            self.assertEqual(get_resp.status_code, 200)
            self.assertContains(get_resp, 'name="is_active_present" value="1"')

            # Post fields with checkbox unchecked (is_active_present=1, no is_active)
            post_data = {
                'username': emp.user.username,
                'first_name': emp.user.first_name or 'Test',
                'last_name': emp.user.last_name or 'User',
                'personal_email': emp.personal_email,
                'role': 'manager' if is_mgr else 'employee',
                'employment_status': 'ACTIVE',
                'work_type': 'OFFICE',
                'is_active_present': '1',
            }
            post_resp = self.client.post(url, post_data)
            self.assertEqual(post_resp.status_code, 302)

            emp.refresh_from_db()
            emp.user.refresh_from_db()
            self.assertFalse(emp.user.is_active)
            self.assertIsNotNone(emp.deactivated_at)

            att.refresh_from_db()
            self.assertIsNotNone(att.log_out_time)

    def test_box_checked_reactivates_inactive_user_across_all_three_pages(self):
        """
        2. Same pages, box checked on an inactive user -> reactivated.
        """
        pages = [
            ('admin_edit_employee', False),
            ('hr_edit_employee', False),
            ('admin_edit_manager', True),
        ]
        for idx, (url_name, is_mgr) in enumerate(pages):
            emp = self._create_employee(f'act_react_{idx}', is_manager=is_mgr, is_active=False)
            self.assertFalse(emp.user.is_active)
            self.assertIsNotNone(emp.deactivated_at)

            url = reverse(url_name, args=[emp.id])
            get_resp = self.client.get(url)
            self.assertEqual(get_resp.status_code, 200)
            self.assertContains(get_resp, 'name="is_active_present" value="1"')

            post_data = {
                'username': emp.user.username,
                'first_name': emp.user.first_name or 'Test',
                'last_name': emp.user.last_name or 'User',
                'personal_email': emp.personal_email,
                'role': 'manager' if is_mgr else 'employee',
                'employment_status': 'ACTIVE',
                'work_type': 'OFFICE',
                'is_active_present': '1',
                'is_active': 'on',
            }
            post_resp = self.client.post(url, post_data)
            self.assertEqual(post_resp.status_code, 302)

            emp.refresh_from_db()
            emp.user.refresh_from_db()
            self.assertTrue(emp.user.is_active)
            self.assertIsNone(emp.deactivated_at)

    def test_box_checked_on_active_user_stays_active_and_role_unchanged(self):
        """
        3. Box checked on an active user -> stays active, role unchanged.
        """
        pages = [
            ('admin_edit_employee', False),
            ('hr_edit_employee', False),
            ('admin_edit_manager', True),
        ]
        for idx, (url_name, is_mgr) in enumerate(pages):
            emp = self._create_employee(f'act_keep_{idx}', is_manager=is_mgr, is_active=True)
            url = reverse(url_name, args=[emp.id])

            post_data = {
                'username': emp.user.username,
                'first_name': 'UpdatedFirst',
                'last_name': 'UpdatedLast',
                'personal_email': emp.personal_email,
                'role': 'manager' if is_mgr else 'employee',
                'employment_status': 'ACTIVE',
                'work_type': 'OFFICE',
                'is_active_present': '1',
                'is_active': 'on',
            }
            post_resp = self.client.post(url, post_data)
            self.assertEqual(post_resp.status_code, 302)

            emp.refresh_from_db()
            emp.user.refresh_from_db()
            self.assertTrue(emp.user.is_active)
            self.assertIsNone(emp.deactivated_at)
            if is_mgr:
                self.assertTrue(emp.is_manager)
                self.assertEqual(emp.get_role(), 'Manager')
            else:
                self.assertTrue(emp.is_employee)
                self.assertEqual(emp.get_role(), 'Employee')

    def test_legacy_post_with_neither_field_leaves_account_unchanged(self):
        """
        4. Legacy post with neither is_active nor is_active_present -> unchanged.
        """
        pages = [
            ('admin_edit_employee', False),
            ('hr_edit_employee', False),
            ('admin_edit_manager', True),
        ]
        for idx, (url_name, is_mgr) in enumerate(pages):
            # Active user stays active
            emp_act = self._create_employee(f'legacy_a_{idx}', is_manager=is_mgr, is_active=True)
            url_act = reverse(url_name, args=[emp_act.id])
            legacy_data_act = {
                'first_name': 'LegacyFirst',
                'last_name': 'LegacyLast',
                'email': emp_act.personal_email,
                'phone_number': '98765432',
                'rank': 'Senior ' + emp_act.rank,
                'salary': '5000.00',
            }
            resp_act = self.client.post(url_act, legacy_data_act)
            self.assertEqual(resp_act.status_code, 302)

            emp_act.refresh_from_db()
            emp_act.user.refresh_from_db()
            self.assertTrue(emp_act.user.is_active)
            self.assertIsNone(emp_act.deactivated_at)
            if is_mgr:
                self.assertTrue(emp_act.is_manager)
            else:
                self.assertTrue(emp_act.is_employee)

            # Inactive user stays inactive
            emp_inact = self._create_employee(f'legacy_i_{idx}', is_manager=is_mgr, is_active=False)
            url_inact = reverse(url_name, args=[emp_inact.id])
            legacy_data_inact = {
                'first_name': 'LegacyInactFirst',
                'last_name': 'LegacyInactLast',
                'email': emp_inact.personal_email,
                'phone_number': '98765432',
                'rank': emp_inact.rank,
                'salary': '5000.00',
            }
            resp_inact = self.client.post(url_inact, legacy_data_inact)
            self.assertEqual(resp_inact.status_code, 302)

            emp_inact.refresh_from_db()
            emp_inact.user.refresh_from_db()
            self.assertFalse(emp_inact.user.is_active)
            self.assertIsNotNone(emp_inact.deactivated_at)
