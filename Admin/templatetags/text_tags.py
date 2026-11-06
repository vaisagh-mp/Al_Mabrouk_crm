import html
import re
from django import template
from django.template.defaultfilters import linebreaksbr
from django.utils.html import escape, strip_tags
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name='plain_text')
def plain_text(value):
    """
    Sanitizes and formats plain text fields (job_instructions, job_scope, project_description):
      - None / "" / "None" -> "—"
      - convert <br>, <br/>, </p>, </div>, </li> to newlines; <li> to "• "
      - strip all other tags (django.utils.html.strip_tags)
      - unescape entities (html.unescape) so &nbsp; -> space, &amp; -> &
      - collapse 3+ blank lines to 1; trim
      - return escaped text with line breaks (linebreaksbr on escaped text), marked safe only AFTER escaping.
    """
    if value is None:
        return mark_safe("—")

    text = str(value)
    if not text.strip() or text.strip().lower() == "none":
        return mark_safe("—")

    # Normalize line breaks
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # Convert <br>, <br/>, </p>, </div>, </li> to newlines; <li> to "• "
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(?:p|div|li)\s*>', '\n', text)
    text = re.sub(r'(?i)<li\s*>', '• ', text)

    # Strip all other tags
    text = strip_tags(text)

    # Unescape entities so &nbsp; -> space, &amp; -> &
    text = html.unescape(text)
    text = text.replace('\xa0', ' ')

    # Collapse 3+ blank lines to 1; trim
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    if not text or text.lower() == "none":
        return mark_safe("—")

    # Return escaped text with line breaks
    escaped = escape(text)
    return linebreaksbr(escaped)

