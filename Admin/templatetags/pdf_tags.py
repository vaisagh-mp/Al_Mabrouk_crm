"""Shared company logo for PDF headers: {% load pdf_tags %}{% pdf_logo %} or {% pdf_logo height="48px" %}."""
import base64

from django import template
from django.contrib.staticfiles import finders
from django.utils.html import format_html

register = template.Library()

LOGO_STATIC_PATH = 'assets/images/reportlogo.png'
_cache = {}


def logo_base64():
    if 'b64' not in _cache:
        data = ''
        path = finders.find(LOGO_STATIC_PATH)
        if path:
            try:
                with open(path, 'rb') as fh:
                    raw = fh.read()
                data = base64.b64encode(raw).decode('ascii') if raw else ''
            except OSError:
                data = ''
        _cache['b64'] = data
    return _cache['b64']


@register.simple_tag
def pdf_logo(height='46px', css_class='pdf-logo'):
    data = logo_base64()
    if not data:
        return ''
    return format_html(
        '<img src="data:image/png;base64,{}" alt="Company logo" class="{}" style="height:{}; width:auto; display:block; margin-bottom:4px;">',
        data, css_class, height,
    )
