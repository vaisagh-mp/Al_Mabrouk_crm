from unittest.mock import patch
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse

from Admin.models import Employee, Project, ProjectAttachment, Team, TeamMemberStatus
from Admin.upload_helpers import (
    PROJECT_ATTACHMENT_EXTENSIONS,
    PROJECT_ATTACHMENT_ACCEPT,
    MAX_PROJECT_ATTACHMENT_SIZE,
    validate_project_attachment,
    get_file_type_info,
)


class ProjectAttachmentUploadTests(TestCase):
    def setUp(self):
        # Admin user
        self.admin_user = User.objects.create_superuser('admin_att', 'admin_att@example.com', 'pass123')

        # Manager user
        self.mgr_user = User.objects.create_user('mgr_att', 'mgr_att@example.com', 'pass123')
        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user,
            is_manager=True,
        )

        # Employee user
        self.emp_user = User.objects.create_user('emp_att', 'emp_att@example.com', 'pass123')
        self.emp = Employee.objects.create(
            user=self.emp_user,
            is_employee=True,
        )

        # Project
        self.project = Project.objects.create(
            name="Attachment Test Project",
            manager=self.mgr_emp
        )

        # Team & TeamMemberStatus for employee
        self.team = Team.objects.create(project=self.project, name="Test Team", manager=self.mgr_emp)
        self.tms = TeamMemberStatus.objects.create(
            team=self.team,
            employee=self.emp,
            status='PENDING'
        )

    def test_validate_project_attachment_allowed_extensions(self):
        for ext in ['.xlsx', '.xls', '.docx', '.doc', '.jpeg', '.jpg', '.pdf', '.png', '.gif', '.webp', '.heic', '.csv', '.zip']:
            f = SimpleUploadedFile(f"test_file{ext}", b"dummy content", content_type="application/octet-stream")
            err = validate_project_attachment(f)
            self.assertIsNone(err, f"Expected {ext} to be allowed, got error: {err}")

        # Case-insensitive
        f_upper = SimpleUploadedFile("TEST_UPPER.XLSX", b"dummy content")
        self.assertIsNone(validate_project_attachment(f_upper))
        f_mixed = SimpleUploadedFile("TEST_MIXED.PdF", b"dummy content")
        self.assertIsNone(validate_project_attachment(f_mixed))

    def test_validate_project_attachment_disallowed_extensions(self):
        for ext in ['.exe', '.py', '.html', '.bat', '.sh']:
            f = SimpleUploadedFile(f"danger{ext}", b"bad content")
            err = validate_project_attachment(f)
            expected = f"danger{ext}: file type not allowed. Allowed: PDF, images, Word, Excel, CSV, ZIP."
            self.assertEqual(err, expected)

    def test_validate_project_attachment_oversized(self):
        # 50 MB simulated
        f = SimpleUploadedFile("huge.pdf", b"small buffer")
        f.size = 50 * 1024 * 1024
        err = validate_project_attachment(f)
        self.assertEqual(err, "File huge.pdf exceeds the 49MB limit.")

    def test_admin_upload_valid_files(self):
        client = Client()
        client.force_login(self.admin_user)

        files = [
            SimpleUploadedFile("report.xlsx", b"xlsx content"),
            SimpleUploadedFile("legacy.xls", b"xls content"),
            SimpleUploadedFile("doc.docx", b"docx content"),
            SimpleUploadedFile("photo.jpeg", b"jpeg content"),
            SimpleUploadedFile("manual.pdf", b"pdf content"),
        ]

        resp = client.post(
            reverse('project-summary', args=[self.project.id]),
            {'attachments': files}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ProjectAttachment.objects.filter(project=self.project).count(), 5)

    def test_admin_upload_disallowed_files_rejected(self):
        client = Client()
        client.force_login(self.admin_user)

        bad_files = [
            SimpleUploadedFile("malware.exe", b"exe content"),
            SimpleUploadedFile("script.py", b"py content"),
            SimpleUploadedFile("page.html", b"html content"),
        ]

        resp = client.post(
            reverse('project-summary', args=[self.project.id]),
            {'attachments': bad_files}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ProjectAttachment.objects.filter(project=self.project).count(), 0)

        # Check messages
        messages_list = list(get_messages(resp.wsgi_request))
        msg_texts = [str(m) for m in messages_list]
        self.assertTrue(any("malware.exe: file type not allowed." in t for t in msg_texts))
        self.assertTrue(any("script.py: file type not allowed." in t for t in msg_texts))
        self.assertTrue(any("page.html: file type not allowed." in t for t in msg_texts))

    def test_admin_upload_mixed_batch(self):
        client = Client()
        client.force_login(self.admin_user)

        mixed_files = [
            SimpleUploadedFile("valid1.xlsx", b"valid 1"),
            SimpleUploadedFile("bad.exe", b"bad exe"),
            SimpleUploadedFile("valid2.pdf", b"valid 2"),
        ]

        resp = client.post(
            reverse('project-summary', args=[self.project.id]),
            {'attachments': mixed_files}
        )
        self.assertEqual(resp.status_code, 200)

        # Only valid1.xlsx and valid2.pdf saved
        saved = list(ProjectAttachment.objects.filter(project=self.project).order_by('id'))
        self.assertEqual(len(saved), 2)
        self.assertIn("valid1", saved[0].file.name)
        self.assertTrue(saved[0].file.name.endswith(".xlsx"))
        self.assertIn("valid2", saved[1].file.name)
        self.assertTrue(saved[1].file.name.endswith(".pdf"))

        # Message for bad.exe
        messages_list = list(get_messages(resp.wsgi_request))
        self.assertTrue(any("bad.exe: file type not allowed." in str(m) for m in messages_list))

    @patch('Admin.views.validate_project_attachment')
    def test_admin_upload_oversized_file_rejected(self, mock_validate):
        mock_validate.return_value = "File big_spread.xlsx exceeds the 49MB limit."
        client = Client()
        client.force_login(self.admin_user)

        f_huge = SimpleUploadedFile("big_spread.xlsx", b"content")

        resp = client.post(
            reverse('project-summary', args=[self.project.id]),
            {'attachments': [f_huge]}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ProjectAttachment.objects.filter(project=self.project).count(), 0)
        messages_list = list(get_messages(resp.wsgi_request))
        self.assertTrue(any("exceeds the 49MB limit" in str(m) for m in messages_list))

    def test_manager_upload_attachments(self):
        client = Client()
        client.force_login(self.mgr_user)

        mixed_files = [
            SimpleUploadedFile("mgr_doc.docx", b"word doc"),
            SimpleUploadedFile("danger.sh", b"bash script"),
        ]

        resp = client.post(
            reverse('project-summary-view', args=[self.project.id]),
            {'attachments': mixed_files}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ProjectAttachment.objects.filter(project=self.project).count(), 1)
        att = ProjectAttachment.objects.filter(project=self.project).first()
        self.assertIn("mgr_doc", att.file.name)
        self.assertTrue(att.file.name.endswith(".docx"))

    def test_employee_upload_attachments(self):
        client = Client()
        client.force_login(self.emp_user)

        mixed_files = [
            SimpleUploadedFile("emp_sheet.csv", b"csv data"),
            SimpleUploadedFile("hack.bat", b"bat data"),
        ]

        resp = client.post(
            reverse('project_details', args=[self.project.id]),
            {'attachments': mixed_files}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ProjectAttachment.objects.filter(project=self.project).count(), 1)
        att = ProjectAttachment.objects.filter(project=self.project).first()
        self.assertIn("emp_sheet", att.file.name)
        self.assertTrue(att.file.name.endswith(".csv"))

    def test_get_file_type_info_and_icons(self):
        self.assertEqual(get_file_type_info("plan.pdf")['category'], 'pdf')
        self.assertFalse(get_file_type_info("plan.pdf")['is_download'])
        self.assertIn("fa-file-pdf", get_file_type_info("plan.pdf")['icon_class'])

        self.assertEqual(get_file_type_info("site.jpg")['category'], 'image')
        self.assertFalse(get_file_type_info("site.jpg")['is_download'])
        self.assertIn("fa-file-image", get_file_type_info("site.jpg")['icon_class'])

        self.assertEqual(get_file_type_info("specs.docx")['category'], 'word')
        self.assertTrue(get_file_type_info("specs.docx")['is_download'])
        self.assertIn("fa-file-word", get_file_type_info("specs.docx")['icon_class'])

        self.assertEqual(get_file_type_info("budget.xlsx")['category'], 'excel')
        self.assertTrue(get_file_type_info("budget.xlsx")['is_download'])
        self.assertIn("fa-file-excel", get_file_type_info("budget.xlsx")['icon_class'])

        self.assertEqual(get_file_type_info("archive.zip")['category'], 'zip')
        self.assertTrue(get_file_type_info("archive.zip")['is_download'])
        self.assertIn("fa-file-archive", get_file_type_info("archive.zip")['icon_class'])
