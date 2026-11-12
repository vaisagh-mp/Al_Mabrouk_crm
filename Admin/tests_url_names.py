"""Every redirect()/reverse()/{% url %} in the code must point at a URL name that exists."""
import os
import re
from django.conf import settings
from django.test import SimpleTestCase
from django.urls import get_resolver

SKIP_DIRS = ('venv', 'node_modules', 'staticfiles', '.git', 'migrations', '__pycache__', 'private_media', 'media')
PATTERNS = [
    re.compile(r"(?:redirect|reverse|reverse_lazy|resolve_url)\(\s*['\"]([\w:-]+)['\"]"),
    re.compile(r"\{%\s*url\s+['\"]([\w:-]+)['\"]"),
]


def _url_names():
    names = set()

    def walk(resolver, ns=''):
        for p in resolver.url_patterns:
            if hasattr(p, 'url_patterns'):
                walk(p, ns + (p.namespace + ':' if p.namespace else ''))
            elif p.name:
                names.add(ns + p.name)
    walk(get_resolver())
    return names


class UrlNamesExistTests(SimpleTestCase):
    def test_all_referenced_url_names_exist(self):
        names = _url_names()
        base = str(settings.BASE_DIR)
        missing = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if not f.endswith(('.py', '.html')):
                    continue
                path = os.path.join(root, f)
                with open(path, encoding='utf-8', errors='ignore') as fh:
                    src = fh.read()
                for pat in PATTERNS:
                    for m in pat.finditer(src):
                        name = m.group(1)
                        if name not in names:
                            line = src[:m.start()].count('\n') + 1
                            missing.append(f"{os.path.relpath(path, base)}:{line} -> '{name}'")
        self.assertEqual(missing, [], "Unknown URL names:\n" + "\n".join(missing))
