"""The HR/Admin register correction modal must only offer valid AttendanceCorrection locations."""
import re
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from Admin.models import Attendance, AttendanceCorrection


class RegisterCorrectionLocationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('adm', 'a@example.com', 'pass123')
        self.client.force_login(self.admin)

    def test_modal_options_are_valid_choices(self):
        resp = self.client.get(reverse('attendance_list_adminview'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        m = re.search(r'id="reg_corr_location"[^>]*>(.*?)</select>', html, re.S)
        self.assertIsNotNone(m)
        values = re.findall(r'value="([^"]+)"', m.group(1))
        valid = {v for v, _ in AttendanceCorrection._meta.get_field('location').choices}
        self.assertTrue(values)
        self.assertTrue(set(values) <= valid, set(values) - valid)
        self.assertNotIn('OFFICE', values)
        self.assertIn('BDO', values)
