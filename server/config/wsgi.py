"""WSGI entrypoint: ``gunicorn config.wsgi:application``."""

from __future__ import annotations

import os

import dbsetup

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
dbsetup.ensure()

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
