"""Create/refresh the admin superuser from env vars. Run once at container start
(see scripts/server_entrypoint.sh) — not from wsgi.py, since gunicorn workers
each import the WSGI app independently and would race on first boot.
"""

from __future__ import annotations

import auth
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create or update the admin superuser from ADMIN_USERNAME/ADMIN_PASSWORD."

    def handle(self, *args, **options):
        user = auth.bootstrap_admin()
        self.stdout.write(self.style.SUCCESS(f"Admin account ready: {user.username}"))
