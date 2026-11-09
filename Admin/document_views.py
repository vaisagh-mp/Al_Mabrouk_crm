import io
import os
import json
from datetime import datetime, date, timedelta
from decimal import Decimal
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden, Http404, FileResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone

from Admin.models import (
    Employee, Department, Branch,
    DocumentType, EmployeeDocument, CompanyDocument, DocumentReminderLog
)
from Admin.permissions import can_view_salary, can_manage_hr_settings, can_view_document
from Admin.document_helpers import (
    validate_document_file, get_document_status,
    get_document_expiry_counts, get_employee_overall_doc_badge
)


def documents_list_view(request):
    """
    Documents & Expiry register page.
    Tabs: Employee Documents and Company Documents.
    Status summary cards: Expired, <=30, <=60, <=90, Valid.
    Filters: status bucket, doc type, dept, branch, employee search, expiry range.
    Excel export for both tabs.
    """
    user = request.user
    if not user.is_authenticated:
        return redirect('login')

    profile = getattr(user, 'employee_profile', None)
    is_hr_or_admin = user.is_superuser or (profile and profile.is_hr)

    if not is_hr_or_admin:
        return HttpResponseForbidden("You are not authorized to view the Document Register.")

    base_template = 'hr/hrbase.html' if (profile and profile.is_hr and not user.is_superuser) else 'Admin/adminbase.html'

    category = request.GET.get('tab', 'employee').lower()
    if category not in ('employee', 'company'):
        category = 'employee'

    today = timezone.localdate()
    counts = get_document_expiry_counts(as_of=today, category=category)

    # Base querysets
    if category == 'employee':
        qs = EmployeeDocument.objects.filter(is_current=True).select_related(
            'employee__user', 'employee__department', 'employee__branch', 'doc_type', 'created_by'
        )
    else:
        qs = CompanyDocument.objects.filter(is_current=True).select_related('doc_type', 'created_by')

    # Filter Bar
    status_filter = request.GET.get('status', 'all')
    doc_type_id = request.GET.get('doc_type')
    expiry_from = request.GET.get('expiry_from')
    expiry_to = request.GET.get('expiry_to')

    if doc_type_id:
        qs = qs.filter(doc_type_id=doc_type_id)

    if expiry_from:
        qs = qs.filter(expiry_date__gte=expiry_from)

    if expiry_to:
        qs = qs.filter(expiry_date__lte=expiry_to)

    if category == 'employee':
        dept_id = request.GET.get('department')
        branch_id = request.GET.get('branch')
        q = request.GET.get('q', '').strip()

        if dept_id:
            qs = qs.filter(employee__department_id=dept_id)
        if branch_id:
            qs = qs.filter(employee__branch_id=branch_id)
        if q:
            qs = qs.filter(
                Q(employee__user__first_name__icontains=q) |
                Q(employee__user__last_name__icontains=q) |
                Q(employee__employee_code__icontains=q) |
                Q(document_number__icontains=q)
            )
    else:
        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(title__icontains=q) |
                Q(document_number__icontains=q)
            )

    # Materialize and apply status bucket filter
    all_docs = list(qs.order_by('expiry_date', '-id'))
    for doc in all_docs:
        doc.status_info = get_document_status(doc, as_of=today)

    if status_filter != 'all':
        filtered_docs = [d for d in all_docs if d.status_info.get('bucket') == status_filter]
    else:
        filtered_docs = all_docs

    # Excel export
    if request.GET.get('export') == 'excel':
        return _export_documents_excel(category, filtered_docs)

    # Pagination: 25 / page
    paginator = Paginator(filtered_docs, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    doc_types = DocumentType.objects.filter(
        category=category.upper(), is_active=True
    ).order_by('sort_order', 'name')

    context = {
        'base_template': base_template,
        'category': category,
        'page_obj': page_obj,
        'counts': counts,
        'doc_types': doc_types,
        'departments': Department.objects.filter(is_active=True),
        'branches': Branch.objects.filter(is_active=True),
        'status_filter': status_filter,
        'selected_doc_type': doc_type_id or '',
        'selected_dept': request.GET.get('department', ''),
        'selected_branch': request.GET.get('branch', ''),
        'expiry_from': expiry_from or '',
        'expiry_to': expiry_to or '',
        'q': request.GET.get('q', ''),
    }
    return render(request, 'documents/document_list.html', context)


def download_private_document_view(request, category, doc_id):
    """
    Authenticated view to download or preview private documents.
    Strict permission checks:
      - HR, Admin, Accountant: all documents.
      - Employee: own documents only.
      - Manager: direct reports' documents only.
      - Others: 403 Forbidden.
    Direct access under /media/ 404s because files reside in PRIVATE_MEDIA_ROOT.
    """
    if not request.user.is_authenticated:
        return HttpResponseForbidden("Authentication required.")

    if category == 'employee':
        doc = get_object_or_404(
            EmployeeDocument.objects.select_related('employee__user', 'employee__reporting_manager'),
            pk=doc_id
        )
    elif category == 'company':
        doc = get_object_or_404(CompanyDocument, pk=doc_id)
    else:
        raise Http404("Invalid document category.")

    if not can_view_document(request.user, doc):
        return HttpResponseForbidden("You are not authorized to view or download this document.")

    if not doc.file:
        raise Http404("Document file does not exist.")

    file_path = doc.file.path
    if not os.path.exists(file_path):
        raise Http404("File not found on storage.")

    # Determine content type
    ext = os.path.splitext(file_path)[1].lower()
    content_type_map = {
        '.pdf': 'application/pdf',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
    }
    content_type = content_type_map.get(ext, 'application/octet-stream')

    is_preview = (request.GET.get('preview') == '1')
    disposition = 'inline' if is_preview else 'attachment'

    filename = os.path.basename(file_path)
    response = FileResponse(open(file_path, 'rb'), content_type=content_type)
    response['Content-Disposition'] = f'{disposition}; filename="{filename}"'
    return response


def upload_document_view(request, category, employee_id=None):
    """
    Upload a new employee or company document.
    """
    user = request.user
    profile = getattr(user, 'employee_profile', None)
    if not (user.is_superuser or (profile and profile.is_hr)):
        return HttpResponseForbidden("You are not authorized to upload documents.")

    base_template = 'hr/hrbase.html' if (profile and profile.is_hr and not user.is_superuser) else 'Admin/adminbase.html'

    employee = None
    if category == 'employee':
        if employee_id:
            employee = get_object_or_404(Employee, pk=employee_id)

    doc_types = DocumentType.objects.filter(
        category=category.upper(), is_active=True
    ).order_by('sort_order', 'name')

    if request.method == 'POST':
        doc_type_id = request.POST.get('doc_type')
        doc_type = get_object_or_404(DocumentType, pk=doc_type_id)

        document_number = request.POST.get('document_number', '').strip()
        issue_date = request.POST.get('issue_date') or None
        expiry_date = request.POST.get('expiry_date') or None
        issuing_authority = request.POST.get('issuing_authority', '').strip()
        notes = request.POST.get('notes', '').strip()
        uploaded_file = request.FILES.get('file')

        if doc_type.has_expiry and not expiry_date:
            messages.error(request, f"Expiry date is required for {doc_type.name}.")
            return render(request, 'documents/upload_document.html', {
                'base_template': base_template, 'category': category,
                'employee': employee, 'doc_types': doc_types
            })

        if uploaded_file:
            try:
                validate_document_file(uploaded_file)
            except ValidationError as e:
                messages.error(request, str(e))
                return render(request, 'documents/upload_document.html', {
                    'base_template': base_template, 'category': category,
                    'employee': employee, 'doc_types': doc_types
                })

        with transaction.atomic():
            if category == 'employee':
                emp_target = employee or get_object_or_404(Employee, pk=request.POST.get('employee'))
                existing = EmployeeDocument.objects.filter(
                    employee=emp_target, doc_type=doc_type, is_current=True
                ).first()

                new_version = 1
                previous = None
                if existing:
                    existing.is_current = False
                    existing.save(update_fields=['is_current'])
                    new_version = existing.version + 1
                    previous = existing

                EmployeeDocument.objects.create(
                    employee=emp_target,
                    doc_type=doc_type,
                    document_number=document_number,
                    issue_date=issue_date,
                    expiry_date=expiry_date,
                    issuing_authority=issuing_authority,
                    notes=notes,
                    file=uploaded_file,
                    is_current=True,
                    version=new_version,
                    previous=previous,
                    created_by=user,
                )
                messages.success(request, f"Document '{doc_type.name}' uploaded successfully.")
                if employee:
                    return redirect(f"{_get_profile_url(employee.id, user)}?tab=documents")
                return redirect('admin_documents_list' if user.is_superuser else 'hr_documents_list')

            else:
                title = request.POST.get('title', '').strip() or doc_type.name
                existing = CompanyDocument.objects.filter(
                    doc_type=doc_type, title=title, is_current=True
                ).first()

                new_version = 1
                previous = None
                if existing:
                    existing.is_current = False
                    existing.save(update_fields=['is_current'])
                    new_version = existing.version + 1
                    previous = existing

                CompanyDocument.objects.create(
                    title=title,
                    doc_type=doc_type,
                    document_number=document_number,
                    issue_date=issue_date,
                    expiry_date=expiry_date,
                    issuing_authority=issuing_authority,
                    notes=notes,
                    file=uploaded_file,
                    is_current=True,
                    version=new_version,
                    previous=previous,
                    created_by=user,
                )
                messages.success(request, f"Company document '{title}' uploaded successfully.")
                url = 'admin_documents_list' if user.is_superuser else 'hr_documents_list'
                return redirect(f"{redirect(url).url}?tab=company")

    employees = Employee.objects.filter(user__is_active=True).select_related('user')
    return render(request, 'documents/upload_document.html', {
        'base_template': base_template,
        'category': category,
        'employee': employee,
        'employees': employees,
        'doc_types': doc_types,
    })


def renew_document_view(request, category, doc_id):
    """
    Renew action:
    Marks old document is_current=False and creates a new document version
    with version=old.version + 1 and previous=old. Reminders automatically reset.
    """
    user = request.user
    profile = getattr(user, 'employee_profile', None)
    if not (user.is_superuser or (profile and profile.is_hr)):
        return HttpResponseForbidden("You are not authorized to renew documents.")

    base_template = 'hr/hrbase.html' if (profile and profile.is_hr and not user.is_superuser) else 'Admin/adminbase.html'

    if category == 'employee':
        doc = get_object_or_404(
            EmployeeDocument.objects.select_related('employee__user', 'doc_type'),
            pk=doc_id
        )
    else:
        doc = get_object_or_404(CompanyDocument.objects.select_related('doc_type'), pk=doc_id)

    if request.method == 'POST':
        document_number = request.POST.get('document_number', '').strip()
        issue_date = request.POST.get('issue_date') or None
        expiry_date = request.POST.get('expiry_date') or None
        issuing_authority = request.POST.get('issuing_authority', '').strip()
        notes = request.POST.get('notes', '').strip()
        uploaded_file = request.FILES.get('file')

        if doc.doc_type.has_expiry and not expiry_date:
            messages.error(request, "Expiry date is required for this document.")
            return render(request, 'documents/renew_document.html', {
                'base_template': base_template, 'category': category, 'doc': doc
            })

        if uploaded_file:
            try:
                validate_document_file(uploaded_file)
            except ValidationError as e:
                messages.error(request, str(e))
                return render(request, 'documents/renew_document.html', {
                    'base_template': base_template, 'category': category, 'doc': doc
                })

        with transaction.atomic():
            doc.is_current = False
            doc.save(update_fields=['is_current'])

            if category == 'employee':
                EmployeeDocument.objects.create(
                    employee=doc.employee,
                    doc_type=doc.doc_type,
                    document_number=document_number or doc.document_number,
                    issue_date=issue_date,
                    expiry_date=expiry_date,
                    issuing_authority=issuing_authority or doc.issuing_authority,
                    notes=notes,
                    file=uploaded_file,
                    is_current=True,
                    version=doc.version + 1,
                    previous=doc,
                    created_by=user,
                )
                messages.success(request, f"Document '{doc.doc_type.name}' renewed (v{doc.version + 1}).")
                return redirect(f"{_get_profile_url(doc.employee.id, user)}?tab=documents")
            else:
                CompanyDocument.objects.create(
                    title=doc.title,
                    doc_type=doc.doc_type,
                    document_number=document_number or doc.document_number,
                    issue_date=issue_date,
                    expiry_date=expiry_date,
                    issuing_authority=issuing_authority or doc.issuing_authority,
                    notes=notes,
                    file=uploaded_file,
                    is_current=True,
                    version=doc.version + 1,
                    previous=doc,
                    created_by=user,
                )
                messages.success(request, f"Company document '{doc.title}' renewed (v{doc.version + 1}).")
                url = 'admin_documents_list' if user.is_superuser else 'hr_documents_list'
                return redirect(f"{redirect(url).url}?tab=company")

    return render(request, 'documents/renew_document.html', {
        'base_template': base_template,
        'category': category,
        'doc': doc,
    })


def edit_document_view(request, category, doc_id):
    """
    Edit existing document metadata and optional file replacement.
    """
    user = request.user
    profile = getattr(user, 'employee_profile', None)
    if not (user.is_superuser or (profile and profile.is_hr)):
        return HttpResponseForbidden("You are not authorized to edit documents.")

    base_template = 'hr/hrbase.html' if (profile and profile.is_hr and not user.is_superuser) else 'Admin/adminbase.html'

    if category == 'employee':
        doc = get_object_or_404(
            EmployeeDocument.objects.select_related('employee__user', 'doc_type'),
            pk=doc_id
        )
    else:
        doc = get_object_or_404(CompanyDocument.objects.select_related('doc_type'), pk=doc_id)

    if request.method == 'POST':
        doc.document_number = request.POST.get('document_number', '').strip()
        doc.issue_date = request.POST.get('issue_date') or None
        doc.expiry_date = request.POST.get('expiry_date') or None
        doc.issuing_authority = request.POST.get('issuing_authority', '').strip()
        doc.notes = request.POST.get('notes', '').strip()

        if category == 'company':
            doc.title = request.POST.get('title', '').strip() or doc.title

        uploaded_file = request.FILES.get('file')
        if uploaded_file:
            try:
                validate_document_file(uploaded_file)
                doc.file = uploaded_file
            except ValidationError as e:
                messages.error(request, str(e))
                return render(request, 'documents/edit_document.html', {
                    'base_template': base_template, 'category': category, 'doc': doc
                })

        doc.save()
        messages.success(request, "Document updated successfully.")
        if category == 'employee':
            return redirect(f"{_get_profile_url(doc.employee.id, user)}?tab=documents")
        url = 'admin_documents_list' if user.is_superuser else 'hr_documents_list'
        return redirect(f"{redirect(url).url}?tab=company")

    return render(request, 'documents/edit_document.html', {
        'base_template': base_template,
        'category': category,
        'doc': doc,
    })


def document_import_view(request):
    """
    Document register bulk import from Excel (D20):
    - ?action=template downloads Excel template
    - POST file shows preview with validation errors and renewal detection
    - POST confirm creates documents atomically (with file=None, "File missing")
    """
    user = request.user
    profile = getattr(user, 'employee_profile', None)
    if not (user.is_superuser or (profile and profile.is_hr)):
        return HttpResponseForbidden("You are not authorized to import documents.")

    base_template = 'hr/hrbase.html' if (profile and profile.is_hr and not user.is_superuser) else 'Admin/adminbase.html'

    # 1. Download Template
    if request.GET.get('action') == 'template':
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Document Import"

        headers = [
            "Employee Code", "Document Type", "Document Number",
            "Issue Date (YYYY-MM-DD)", "Expiry Date (YYYY-MM-DD)",
            "Issuing Authority", "Notes"
        ]
        ws.append(headers)

        header_fill = PatternFill(start_color="1F3A52", end_color="1F3A52", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font

        # Sample row
        ws.append([
            "AMMS-0001", "Passport", "N12345678",
            "2023-01-15", "2033-01-14", "Embassy of UAE", "Renewed in Dubai"
        ])

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        response = HttpResponse(
            output.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response['Content-Disposition'] = 'attachment; filename="document_import_template.xlsx"'
        return response

    # 2. Confirm Import
    if request.method == 'POST' and request.POST.get('action') == 'confirm':
        raw_rows_json = request.POST.get('rows_json')
        if not raw_rows_json:
            messages.error(request, "No preview data to confirm.")
            return redirect('document_import')

        try:
            rows_data = json.loads(raw_rows_json)
        except json.JSONDecodeError:
            messages.error(request, "Invalid preview data.")
            return redirect('document_import')

        imported_count = 0
        with transaction.atomic():
            for r in rows_data:
                emp = Employee.objects.filter(employee_code=r['emp_code']).first()
                if not emp:
                    continue
                dt = DocumentType.objects.filter(name__iexact=r['doc_type_name'], category='EMPLOYEE').first()
                if not dt:
                    continue

                existing = EmployeeDocument.objects.filter(employee=emp, doc_type=dt, is_current=True).first()
                new_version = 1
                previous = None
                if existing:
                    existing.is_current = False
                    existing.save(update_fields=['is_current'])
                    new_version = existing.version + 1
                    previous = existing

                EmployeeDocument.objects.create(
                    employee=emp,
                    doc_type=dt,
                    document_number=r.get('document_number') or '',
                    issue_date=r.get('issue_date') or None,
                    expiry_date=r.get('expiry_date') or None,
                    issuing_authority=r.get('issuing_authority') or '',
                    notes=r.get('notes') or '',
                    file=None,  # File missing until uploaded
                    is_current=True,
                    version=new_version,
                    previous=previous,
                    created_by=user,
                )
                imported_count += 1

        messages.success(request, f"Successfully imported {imported_count} document records.")
        url = 'admin_documents_list' if user.is_superuser else 'hr_documents_list'
        return redirect(url)

    # 3. Upload & Preview
    if request.method == 'POST' and request.FILES.get('file'):
        excel_file = request.FILES['file']
        try:
            wb = openpyxl.load_workbook(excel_file, data_only=True)
            ws = wb.active
        except Exception as e:
            messages.error(request, f"Could not read Excel file: {e}")
            return render(request, 'documents/document_import.html', {'base_template': base_template})

        preview_rows = []
        has_errors = False

        rows = list(ws.iter_rows(values_only=True))
        if len(rows) > 1:
            for row_idx, row in enumerate(rows[1:], start=2):
                if not any(row):
                    continue

                emp_code = str(row[0] or '').strip()
                doc_type_name = str(row[1] or '').strip()
                doc_number = str(row[2] or '').strip()
                issue_val = row[3] if len(row) > 3 else None
                expiry_val = row[4] if len(row) > 4 else None
                issuing_auth = str(row[5] or '').strip() if len(row) > 5 else ''
                notes_val = str(row[6] or '').strip() if len(row) > 6 else ''

                errors = []
                emp = Employee.objects.filter(employee_code=emp_code).select_related('user').first()
                if not emp:
                    errors.append(f"Employee code '{emp_code}' not found.")

                dt = DocumentType.objects.filter(name__iexact=doc_type_name, category='EMPLOYEE', is_active=True).first()
                if not dt:
                    errors.append(f"Document type '{doc_type_name}' not found or inactive.")

                issue_date_str = _parse_date_cell(issue_val)
                expiry_date_str = _parse_date_cell(expiry_val)

                if dt and dt.has_expiry and not expiry_date_str:
                    errors.append("Expiry date is required for this document type.")

                if issue_date_str and expiry_date_str and expiry_date_str < issue_date_str:
                    errors.append("Expiry date cannot be earlier than issue date.")

                action = "New Document"
                if emp and dt:
                    existing = EmployeeDocument.objects.filter(employee=emp, doc_type=dt, is_current=True).first()
                    if existing:
                        action = f"Renew (v{existing.version + 1})"

                if errors:
                    has_errors = True

                preview_rows.append({
                    'row_idx': row_idx,
                    'emp_code': emp_code,
                    'emp_name': emp.user.get_full_name() if emp else "Unknown",
                    'doc_type_name': dt.name if dt else doc_type_name,
                    'document_number': doc_number,
                    'issue_date': issue_date_str,
                    'expiry_date': expiry_date_str,
                    'issuing_authority': issuing_auth,
                    'notes': notes_val,
                    'action': action,
                    'errors': errors,
                })

        return render(request, 'documents/document_import_preview.html', {
            'base_template': base_template,
            'preview_rows': preview_rows,
            'has_errors': has_errors,
            'rows_json': json.dumps(preview_rows),
        })

    return render(request, 'documents/document_import.html', {
        'base_template': base_template,
    })


def document_types_view(request):
    """
    HR master data settings for Document Types.
    """
    user = request.user
    if not can_manage_hr_settings(user):
        return HttpResponseForbidden("You are not authorized to manage document types.")

    profile = getattr(user, 'employee_profile', None)
    base_template = 'hr/hrbase.html' if (profile and profile.is_hr and not user.is_superuser) else 'Admin/adminbase.html'

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create':
            name = request.POST.get('name', '').strip()
            category = request.POST.get('category', 'EMPLOYEE')
            has_expiry = (request.POST.get('has_expiry') == '1')
            is_required = (request.POST.get('is_required') == '1')
            reminder_days = request.POST.get('reminder_days', '90,60,30,0').strip()
            sort_order = int(request.POST.get('sort_order') or 0)

            if not name:
                messages.error(request, "Document type name is required.")
            elif DocumentType.objects.filter(name__iexact=name, category=category).exists():
                messages.error(request, f"Document type '{name}' already exists in {category}.")
            else:
                DocumentType.objects.create(
                    name=name, category=category, has_expiry=has_expiry,
                    is_required=is_required, reminder_days=reminder_days, sort_order=sort_order
                )
                messages.success(request, f"Document type '{name}' created.")

        elif action == 'edit':
            dt_id = request.POST.get('doc_type_id')
            dt = get_object_or_404(DocumentType, pk=dt_id)
            dt.name = request.POST.get('name', dt.name).strip()
            dt.has_expiry = (request.POST.get('has_expiry') == '1')
            dt.is_required = (request.POST.get('is_required') == '1')
            dt.reminder_days = request.POST.get('reminder_days', dt.reminder_days).strip()
            dt.sort_order = int(request.POST.get('sort_order') or dt.sort_order)
            dt.is_active = (request.POST.get('is_active') == '1')
            dt.save()
            messages.success(request, f"Document type '{dt.name}' updated.")

        elif action == 'delete':
            dt_id = request.POST.get('doc_type_id')
            dt = get_object_or_404(DocumentType, pk=dt_id)
            if EmployeeDocument.objects.filter(doc_type=dt).exists() or CompanyDocument.objects.filter(doc_type=dt).exists():
                messages.error(request, f"Cannot delete '{dt.name}' because existing documents reference it.")
            else:
                dt.delete()
                messages.success(request, f"Document type '{dt.name}' deleted.")

        return redirect('hr_document_types')

    doc_types = DocumentType.objects.all().order_by('category', 'sort_order', 'name')
    return render(request, 'documents/document_types.html', {
        'base_template': base_template,
        'doc_types': doc_types,
    })


def _parse_date_cell(val):
    if not val:
        return None
    if isinstance(val, (date, datetime)):
        return val.strftime('%Y-%m-%d')
    s = str(val).strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, fmt).date().strftime('%Y-%m-%d')
        except ValueError:
            pass
    return None


def _get_profile_url(employee_id, user):
    profile = getattr(user, 'employee_profile', None)
    if profile and profile.is_hr and not user.is_superuser:
        return f"/hr/employee/{employee_id}/"
    return f"/employee_profile/{employee_id}/"


def _export_documents_excel(category, docs):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Documents Register"

    header_fill = PatternFill(start_color="1F3A52", end_color="1F3A52", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

    if category == 'employee':
        headers = [
            "Employee Code", "Employee Name", "Department", "Branch",
            "Document Type", "Document Number", "Issue Date", "Expiry Date",
            "Issuing Authority", "Status", "Days Remaining", "File Attached"
        ]
        ws.append(headers)
        for doc in docs:
            st = doc.status_info
            ws.append([
                doc.employee.employee_code or f"AMMS-{doc.employee.id:04d}",
                doc.employee.user.get_full_name() or doc.employee.user.username,
                doc.employee.department.name if doc.employee.department else "",
                doc.employee.branch.name if doc.employee.branch else "",
                doc.doc_type.name,
                doc.document_number or "",
                doc.issue_date.strftime('%Y-%m-%d') if doc.issue_date else "",
                doc.expiry_date.strftime('%Y-%m-%d') if doc.expiry_date else "",
                doc.issuing_authority or "",
                st.get('label', ''),
                st.get('days') if st.get('days') is not None else "N/A",
                "Yes" if doc.file else "Missing",
            ])
    else:
        headers = [
            "Title", "Document Type", "Document Number",
            "Issue Date", "Expiry Date", "Issuing Authority",
            "Status", "Days Remaining", "File Attached"
        ]
        ws.append(headers)
        for doc in docs:
            st = doc.status_info
            ws.append([
                doc.title,
                doc.doc_type.name,
                doc.document_number or "",
                doc.issue_date.strftime('%Y-%m-%d') if doc.issue_date else "",
                doc.expiry_date.strftime('%Y-%m-%d') if doc.expiry_date else "",
                doc.issuing_authority or "",
                st.get('label', ''),
                st.get('days') if st.get('days') is not None else "N/A",
                "Yes" if doc.file else "Missing",
            ])

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"{category}_documents_{timezone.localdate().strftime('%Y%m%d')}.xlsx"
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

