"""Django settings for the single-user, local-only HTTP layer (views_*.py, urls.py).

No database, no auth, no sessions, no CSRF: the app runs as one stateless local
process that serves the SPA, proxies ENA, and builds XML. All persistence lives
in the browser. Webin credentials arrive per-request as headers (see
``webin_creds``).
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Required by Django for request signing; no secrets persist anywhere.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "mimicc-ena-insecure-local-key")

DEBUG = (os.environ.get("DJANGO_DEBUG", "") or "").strip().lower() in ("1", "true", "yes")

ALLOWED_HOSTS = ["*"]  # local single-user; only ever bound to loopback.

INSTALLED_APPS: list[str] = []

MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

USE_TZ = True
