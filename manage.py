#!/usr/bin/env python
"""Django management entrypoint for ORM migrations (makemigrations/migrate)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "orm.settings")

if __name__ == "__main__":
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
