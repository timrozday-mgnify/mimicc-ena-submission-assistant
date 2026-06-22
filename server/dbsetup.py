"""One-time ``django.setup()`` bootstrap for using the Django ORM standalone.

Import this and call ``ensure()`` before touching ``orm.models`` or any other
Django machinery. It is idempotent and safe to call from multiple modules.
"""

from __future__ import annotations

import os

_done = False


def ensure() -> None:
    global _done
    if _done:
        return
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "orm.settings")
    django.setup()
    _done = True


def migrate() -> None:
    """Apply migrations (used by the entrypoint and tests)."""
    ensure()
    from django.core.management import call_command

    call_command("migrate", run_syncdb=True, verbosity=0)
