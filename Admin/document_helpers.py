import os
from datetime import date, timedelta
from django.core.exceptions import ValidationError
from django.utils import timezone


def validate_document_file(file):
    """
    Validates uploaded document files:
    - Allowed extensions: PDF, JPG, JPEG, PNG
    - Max size: 10 MB
    """
    if not file:
        return
    valid_extensions = ['.pdf', '.jpg', '.jpeg', '.png']
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in valid_extensions:
        raise ValidationError("Attachment must be a PDF, JPG, JPEG, or PNG file.")
    if hasattr(file, 'size') and file.size > 10 * 1024 * 1024:
        raise ValidationError("Attachment file size cannot exceed 10 MB.")


def get_document_status(doc, as_of=None):
    """
    Calculates the status of an EmployeeDocument or CompanyDocument.
    Status buckets:
      - Expired (expiry < today)
      - Expiring <=30 (0 <= days <= 30)
      - Expiring <=60 (31 <= days <= 60)
      - Expiring <=90 (61 <= days <= 90)
      - Valid (days > 90)
      - No expiry (when doc_type.has_expiry is False or expiry_date is None)
    """
    today = as_of or timezone.localdate()
    doc_type = getattr(doc, 'doc_type', None)
    has_expiry = getattr(doc_type, 'has_expiry', True) if doc_type else True
    has_file = bool(doc.file) if getattr(doc, 'file', None) else False

    if not has_expiry or not doc.expiry_date:
        return {
            'code': 'NO_EXPIRY',
            'label': 'No Expiry',
            'badge_class': 'badge-secondary',
            'days': None,
            'bucket': 'valid',
            'has_file': has_file,
        }

    days = (doc.expiry_date - today).days

    if days < 0:
        return {
            'code': 'EXPIRED',
            'label': f'Expired ({abs(days)}d ago)' if abs(days) > 1 else 'Expired yesterday',
            'badge_class': 'badge-danger',
            'days': days,
            'bucket': 'expired',
            'has_file': has_file,
        }
    elif days <= 30:
        return {
            'code': 'EXPIRING_30',
            'label': f'Expiring in {days}d' if days > 0 else 'Expires today',
            'badge_class': 'badge-danger',
            'days': days,
            'bucket': '30',
            'has_file': has_file,
        }
    elif days <= 60:
        return {
            'code': 'EXPIRING_60',
            'label': f'Expiring in {days}d',
            'badge_class': 'badge-warning',
            'days': days,
            'bucket': '60',
            'has_file': has_file,
        }
    elif days <= 90:
        return {
            'code': 'EXPIRING_90',
            'label': f'Expiring in {days}d',
            'badge_class': 'badge-info',
            'days': days,
            'bucket': '90',
            'has_file': has_file,
        }
    else:
        return {
            'code': 'VALID',
            'label': 'Valid',
            'badge_class': 'badge-success',
            'days': days,
            'bucket': 'valid',
            'has_file': has_file,
        }


def get_employee_overall_doc_badge(employee, as_of=None, required_types=None, current_docs=None):
    """
    Returns the worst document status badge for an employee:
    Severity order:
      1. Expired (any current doc expired)
      2. Missing required (missing a required active document type)
      3. <=30 (any current doc expiring within 30 days)
      4. <=60 (any current doc expiring within 60 days)
      5. <=90 (any current doc expiring within 90 days)
      6. OK (all required present, all current valid)
    """
    from Admin.models import DocumentType, EmployeeDocument

    today = as_of or timezone.localdate()

    if required_types is None:
        required_types = list(DocumentType.objects.filter(
            category='EMPLOYEE', is_required=True, is_active=True
        ))

    if current_docs is None:
        current_docs = list(EmployeeDocument.objects.filter(
            employee=employee, is_current=True
        ).select_related('doc_type'))

    doc_by_type_id = {d.doc_type_id: d for d in current_docs}

    # 1. Check for Expired first
    has_expired = False
    has_30 = False
    has_60 = False
    has_90 = False

    for doc in current_docs:
        status_info = get_document_status(doc, as_of=today)
        code = status_info['code']
        if code == 'EXPIRED':
            has_expired = True
        elif code == 'EXPIRING_30':
            has_30 = True
        elif code == 'EXPIRING_60':
            has_60 = True
        elif code == 'EXPIRING_90':
            has_90 = True

    if has_expired:
        return {
            'code': 'EXPIRED',
            'label': 'Expired',
            'badge_class': 'badge-danger',
        }

    # 2. Check for missing required types
    missing_req = [rt for rt in required_types if rt.id not in doc_by_type_id]
    if missing_req:
        return {
            'code': 'MISSING_REQUIRED',
            'label': 'Missing required',
            'badge_class': 'badge-danger',
            'missing_types': [rt.name for rt in missing_req],
        }

    if has_30:
        return {
            'code': 'EXPIRING_30',
            'label': '≤30',
            'badge_class': 'badge-danger',
        }
    if has_60:
        return {
            'code': 'EXPIRING_60',
            'label': '≤60',
            'badge_class': 'badge-warning',
        }
    if has_90:
        return {
            'code': 'EXPIRING_90',
            'label': '≤90',
            'badge_class': 'badge-info',
        }

    return {
        'code': 'OK',
        'label': 'OK',
        'badge_class': 'badge-success',
    }


def get_document_expiry_counts(as_of=None, category='all'):
    """
    Returns counts of active current documents by expiry status bucket:
    expired, 30, 60, 90, valid, total.
    """
    today = as_of or timezone.localdate()
    from Admin.models import EmployeeDocument, CompanyDocument

    emp_docs = []
    comp_docs = []
    if category in ('all', 'employee'):
        emp_docs = list(EmployeeDocument.objects.filter(is_current=True, employee__user__is_active=True).select_related('doc_type'))
    if category in ('all', 'company'):
        comp_docs = list(CompanyDocument.objects.filter(is_current=True).select_related('doc_type'))

    counts = {
        'expired': 0,
        '30': 0,
        '60': 0,
        '90': 0,
        'valid': 0,
        'total': 0,
    }

    for doc in emp_docs + comp_docs:
        status_info = get_document_status(doc, as_of=today)
        bucket = status_info.get('bucket', 'valid')
        if bucket in counts:
            counts[bucket] += 1
        counts['total'] += 1

    return counts

