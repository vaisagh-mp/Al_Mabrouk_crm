import os

PROJECT_ATTACHMENT_EXTENSIONS = (
    '.pdf',
    '.jpg',
    '.jpeg',
    '.png',
    '.gif',
    '.webp',
    '.heic',
    '.doc',
    '.docx',
    '.xls',
    '.xlsx',
    '.csv',
    '.zip',
)

PROJECT_ATTACHMENT_ACCEPT = ",".join(PROJECT_ATTACHMENT_EXTENSIONS)
MAX_PROJECT_ATTACHMENT_SIZE = 49 * 1024 * 1024  # 49 MB


def validate_project_attachment(file):
    """
    Validates a project attachment file:
    - Extension (case-insensitive) must be in PROJECT_ATTACHMENT_EXTENSIONS.
    - Size must be <= 49 MB.
    Returns error message string if invalid, or None if valid.
    """
    if not file:
        return "No file provided."

    name = getattr(file, 'name', '') or ''
    ext = os.path.splitext(name)[1].lower()

    if ext not in PROJECT_ATTACHMENT_EXTENSIONS:
        return f"{name}: file type not allowed. Allowed: PDF, images, Word, Excel, CSV, ZIP."

    size = getattr(file, 'size', 0) or 0
    if size > MAX_PROJECT_ATTACHMENT_SIZE:
        return f"File {name} exceeds the 49MB limit."

    return None


def get_file_type_info(file_or_name):
    """
    Returns file-type category, FontAwesome icon class, and download flag.
    - PDF / image: opens in new tab (is_download=False)
    - Word / Excel / ZIP / other: downloads (is_download=True)
    """
    if hasattr(file_or_name, 'file') and hasattr(file_or_name.file, 'name'):
        name = file_or_name.file.name
    elif hasattr(file_or_name, 'name'):
        name = file_or_name.name
    else:
        name = str(file_or_name or '')

    ext = os.path.splitext(name)[1].lower()

    if ext == '.pdf':
        return {
            'category': 'pdf',
            'icon_class': 'fas fa-file-pdf text-danger',
            'is_download': False,
        }
    elif ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic'):
        return {
            'category': 'image',
            'icon_class': 'fas fa-file-image text-primary',
            'is_download': False,
        }
    elif ext in ('.doc', '.docx'):
        return {
            'category': 'word',
            'icon_class': 'fas fa-file-word text-info',
            'is_download': True,
        }
    elif ext in ('.xls', '.xlsx', '.csv'):
        return {
            'category': 'excel',
            'icon_class': 'fas fa-file-excel text-success',
            'is_download': True,
        }
    elif ext == '.zip':
        return {
            'category': 'zip',
            'icon_class': 'fas fa-file-archive text-warning',
            'is_download': True,
        }
    else:
        return {
            'category': 'other',
            'icon_class': 'fas fa-file-alt text-secondary',
            'is_download': True,
        }

