from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User
from Admin.models import Employee
from Admin.forms import EmployeeCreationForm, ManagerEmployeeUpdateForm, format_reporting_manager_label


class ReportingManagerChoicesTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser('admin_tester', 'admin@example.com', 'adminpass123')

        # 1. Active Manager
        self.mgr_user = User.objects.create_user(
            username='tariq_m',
            email='tariq@amms.ae',
            password='password123',
            first_name='Tariq',
            last_name='Manager',
            is_active=True
        )
        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user,
            is_manager=True,
            is_employee=False,
            employee_code='AMMS-M01',
            rank='Operations Manager',
            salary=Decimal('8000.00')
        )

        # 2. Normal Active Employee (not manager)
        self.staff_user = User.objects.create_user(
            username='salem_s',
            email='salem@amms.ae',
            password='password123',
            first_name='Salem',
            last_name='Staff',
            is_active=True
        )
        self.staff_emp = Employee.objects.create(
            user=self.staff_user,
            is_employee=True,
            is_manager=False,
            employee_code='AMMS-S01',
            rank='Technician',
            salary=Decimal('4000.00')
        )

        # 3. Active HR-only Employee (not manager)
        self.hr_user = User.objects.create_user(
            username='fatima_h',
            email='fatima@amms.ae',
            password='password123',
            first_name='Fatima',
            last_name='HR',
            is_active=True
        )
        self.hr_emp = Employee.objects.create(
            user=self.hr_user,
            is_hr=True,
            is_manager=False,
            employee_code='AMMS-H01',
            rank='HR Officer',
            salary=Decimal('5000.00')
        )

        # 4. Inactive Manager
        self.inact_mgr_user = User.objects.create_user(
            username='ibrahim_i',
            email='ibrahim@amms.ae',
            password='password123',
            first_name='Ibrahim',
            last_name='Inactive',
            is_active=False
        )
        self.inact_mgr_emp = Employee.objects.create(
            user=self.inact_mgr_user,
            is_manager=True,
            is_employee=False,
            employee_code='AMMS-M02',
            rank='Senior Manager',
            salary=Decimal('9000.00')
        )

    def test_create_dropdown_choices(self):
        """
        Create: dropdown contains a manager, does NOT contain a normal employee,
        an HR-only user, or an inactive manager.
        """
        form = EmployeeCreationForm(user=self.admin_user)
        qs = form.fields['reporting_manager'].queryset

        self.assertIn(self.mgr_emp, qs)
        self.assertNotIn(self.staff_emp, qs)
        self.assertNotIn(self.hr_emp, qs)
        self.assertNotIn(self.inact_mgr_emp, qs)

    def test_dropdown_label_formatting_and_fallback(self):
        """
        Dropdown label shows 'Full name (Employee code)' or falls back to username
        if the full name is empty.
        """
        form = EmployeeCreationForm(user=self.admin_user)
        label = form.fields['reporting_manager'].label_from_instance(self.mgr_emp)
        self.assertEqual(label, "Tariq Manager (AMMS-M01)")

        # Create manager with empty first_name and last_name
        u_noname = User.objects.create_user(
            username='noname_mgr',
            email='noname@amms.ae',
            password='password123',
            first_name='',
            last_name='',
            is_active=True
        )
        e_noname = Employee.objects.create(
            user=u_noname,
            is_manager=True,
            employee_code='AMMS-M03',
            rank='Manager'
        )
        label_noname = form.fields['reporting_manager'].label_from_instance(e_noname)
        self.assertEqual(label_noname, "noname_mgr (AMMS-M03)")

        # Manager with whitespace-only name
        u_spaces = User.objects.create_user(
            username='spaces_mgr',
            email='spaces@amms.ae',
            password='password123',
            first_name='   ',
            last_name='  ',
            is_active=True
        )
        e_spaces = Employee.objects.create(
            user=u_spaces,
            is_manager=True,
            employee_code='AMMS-M04',
            rank='Manager'
        )
        label_spaces = form.fields['reporting_manager'].label_from_instance(e_spaces)
        self.assertEqual(label_spaces, "spaces_mgr (AMMS-M04)")

    def test_edit_current_reporting_manager_not_manager_or_inactive_preserved(self):
        """
        Edit: employee whose current reporting manager is not a manager still sees
        that person selected, and saving the form keeps the value.
        """
        # Case 1: Current manager is a normal employee (not a manager)
        self.staff_emp.reporting_manager = self.hr_emp  # hr_emp is not a manager
        self.staff_emp.save()

        form = ManagerEmployeeUpdateForm(instance=self.staff_emp, user=self.admin_user)
        qs = form.fields['reporting_manager'].queryset
        self.assertIn(self.hr_emp, qs)
        self.assertIn(self.mgr_emp, qs)
        self.assertNotIn(self.inact_mgr_emp, qs)

        # Saving keeping current manager
        data = {
            'first_name': self.staff_user.first_name,
            'last_name': self.staff_user.last_name,
            'username': self.staff_user.username,
            'personal_email': 'salem@amms.ae',
            'reporting_manager': str(self.hr_emp.pk),
            'rank': self.staff_emp.rank,
            'basic': '4000.00',
            'role': 'employee',
            'employment_status': 'ACTIVE',
            'work_type': 'OFFICE',
        }
        form = ManagerEmployeeUpdateForm(data=data, instance=self.staff_emp, user=self.admin_user)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.reporting_manager, self.hr_emp)

        # Case 2: Current manager is inactive
        self.staff_emp.reporting_manager = self.inact_mgr_emp
        self.staff_emp.save()

        form = ManagerEmployeeUpdateForm(instance=self.staff_emp, user=self.admin_user)
        qs = form.fields['reporting_manager'].queryset
        self.assertIn(self.inact_mgr_emp, qs)

        data['reporting_manager'] = str(self.inact_mgr_emp.pk)
        form = ManagerEmployeeUpdateForm(data=data, instance=self.staff_emp, user=self.admin_user)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.reporting_manager, self.inact_mgr_emp)

    def test_edit_employee_excluded_from_own_list(self):
        """
        Edit: employee is not in their own list.
        """
        # Manager editing themselves
        form = ManagerEmployeeUpdateForm(instance=self.mgr_emp, user=self.admin_user)
        qs = form.fields['reporting_manager'].queryset
        self.assertNotIn(self.mgr_emp, qs)

        # Even if data previously had employee as their own manager
        self.mgr_emp.reporting_manager = self.mgr_emp
        self.mgr_emp.save()

        form = ManagerEmployeeUpdateForm(instance=self.mgr_emp, user=self.admin_user)
        qs = form.fields['reporting_manager'].queryset
        self.assertNotIn(self.mgr_emp, qs)

        # Posting employee as their own manager fails validation
        data = {
            'first_name': self.mgr_user.first_name,
            'last_name': self.mgr_user.last_name,
            'username': self.mgr_user.username,
            'personal_email': 'tariq@amms.ae',
            'reporting_manager': str(self.mgr_emp.pk),
            'rank': self.mgr_emp.rank,
            'basic': '8000.00',
            'role': 'manager',
            'employment_status': 'ACTIVE',
            'work_type': 'OFFICE',
        }
        form = ManagerEmployeeUpdateForm(data=data, instance=self.mgr_emp, user=self.admin_user)
        self.assertFalse(form.is_valid())
        self.assertIn('reporting_manager', form.errors)

    def test_post_non_manager_id_on_create_gives_form_error(self):
        """
        Posting a non-manager id on create gives a form error.
        """
        base_data = {
            'first_name': 'New',
            'last_name': 'Technician',
            'username': 'new_tech',
            'personal_email': 'new_tech@amms.ae',
            'password': 'ComplexPassword123!',
            'password2': 'ComplexPassword123!',
            'role': 'employee',
            'rank': 'Junior Tech',
            'basic': '3500.00',
            'employment_status': 'ACTIVE',
            'work_type': 'OFFICE',
        }

        # 1. Posting normal employee id
        data_normal = dict(base_data, reporting_manager=str(self.staff_emp.pk))
        form = EmployeeCreationForm(data=data_normal, user=self.admin_user)
        self.assertFalse(form.is_valid())
        self.assertIn('reporting_manager', form.errors)

        # 2. Posting HR employee id
        data_hr = dict(base_data, reporting_manager=str(self.hr_emp.pk))
        form = EmployeeCreationForm(data=data_hr, user=self.admin_user)
        self.assertFalse(form.is_valid())
        self.assertIn('reporting_manager', form.errors)

        # 3. Posting inactive manager id
        data_inact = dict(base_data, reporting_manager=str(self.inact_mgr_emp.pk))
        form = EmployeeCreationForm(data=data_inact, user=self.admin_user)
        self.assertFalse(form.is_valid())
        self.assertIn('reporting_manager', form.errors)

        # 4. Posting valid active manager id succeeds
        data_valid = dict(base_data, reporting_manager=str(self.mgr_emp.pk))
        form = EmployeeCreationForm(data=data_valid, user=self.admin_user)
        self.assertTrue(form.is_valid(), form.errors)
        created_user = form.save()
        self.assertEqual(created_user.employee_profile.reporting_manager, self.mgr_emp)

    def test_ordering_by_first_name_last_name(self):
        """
        Queryset is ordered by user first_name, last_name.
        """
        u_aaron = User.objects.create_user(
            username='aaron_m',
            email='aaron@amms.ae',
            password='password123',
            first_name='Aaron',
            last_name='Alpha',
            is_active=True
        )
        Employee.objects.create(
            user=u_aaron,
            is_manager=True,
            employee_code='AMMS-M05',
            rank='Manager'
        )

        u_zach = User.objects.create_user(
            username='zach_m',
            email='zach@amms.ae',
            password='password123',
            first_name='Zach',
            last_name='Zenith',
            is_active=True
        )
        Employee.objects.create(
            user=u_zach,
            is_manager=True,
            employee_code='AMMS-M06',
            rank='Manager'
        )

        form = EmployeeCreationForm(user=self.admin_user)
        qs = list(form.fields['reporting_manager'].queryset)
        names = [(e.user.first_name, e.user.last_name) for e in qs]
        self.assertEqual(names, sorted(names))
