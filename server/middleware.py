"""Local-mode CSRF bypass.

In ``DEPLOYMENT_MODE=local`` every request auto-authenticates as the admin
user and there is no login screen (see ``auth.is_local``) — CSRF protection
exists to guard cookie-based sessions against cross-site requests, which
isn't a meaningful threat model for the single-user local mode. Setting
``_dont_enforce_csrf_checks`` is the same hook Django's own test client uses
for ``enforce_csrf_checks=False``; ``CsrfViewMiddleware`` checks it directly.
"""

from __future__ import annotations

import auth


class LocalModeCsrfBypassMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if auth.is_local():
            request._dont_enforce_csrf_checks = True
        return self.get_response(request)
