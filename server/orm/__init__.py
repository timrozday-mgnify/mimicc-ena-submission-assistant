"""Django app package providing the ORM models (Postgres in production).

Django is used here purely as an ORM + auth/password library + migration tool;
FastAPI remains the HTTP layer. See ``dbsetup.ensure()`` for the one-time
``django.setup()`` bootstrap, and ``orm.models`` for the schema.
"""
