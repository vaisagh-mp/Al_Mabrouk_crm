"""Admin (superuser) keeps the sidebar of the portal they came from on My Payslips."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from Admin.models import Employee

PORTALS = {
    '/admin-panel/dashboard/': 'Admin/adminbase.html',
    '/hr/dashboard/': 'hr/hrbase.html',
    '/finance/': 'finance/financebase.html',
}


class SuperuserMyPayslipsSidebarTests(TestCase):
    def _check(self):
        for start, base in PORTALS.items():
            self.client.get(start)
            res = self.client.get('/finance/my-payslips/')
            self.assertEqual(res.status_code, 200, start)
            names = [t.name for t in res.templates]
            self.assertIn(base, names, f'{start} -> my-payslips rendered {names[:3]}')
            self.assertNotIn('employee/employeebase.html', names, start)

    def test_superuser_with_employee_record(self):
        su = User.objects.create_superuser('su_p', 'su_p@example.com', 'pw')
        Employee.objects.create(user=su, salary=Decimal('1000'), date_of_join=datetime.date(2026, 1, 1))
        self.client.force_login(su)
        self._check()

    def test_superuser_without_employee_record(self):
        su = User.objects.create_superuser('su_n', 'su_n@example.com', 'pw')
        self.client.force_login(su)
        self._check()
