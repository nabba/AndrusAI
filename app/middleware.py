"""
middleware.py — Security headers and CORS middleware.

Extracted from main.py to reduce gravity-well coupling.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from fastapi.middleware.cors import CORSMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add standard security headers to every response.

    The /cp/ dashboard needs scripts, styles, images, and API connections,
    so it gets a permissive CSP. All other routes (API, webhooks) keep the
    strict default-src 'none' policy.
    """

    # CSP for the React dashboard: allow self-hosted assets + API calls
    _DASHBOARD_CSP = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "frame-ancestors 'none'"
    )
    # Strict CSP for API routes: block everything
    _API_CSP = "default-src 'none'; frame-ancestors 'none'"

    async def dispatch(self, request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        path = request.url.path
        if path.startswith("/cp"):
            response.headers["Content-Security-Policy"] = self._DASHBOARD_CSP
            # Allow browser caching for static assets
            if "/assets/" in path:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = self._API_CSP
        return response


def add_middleware(app, settings):
    """Configure all middleware on the FastAPI app."""
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://127.0.0.1:{settings.gateway_port}",
            f"http://localhost:{settings.gateway_port}",
            "https://botarmy-ba0c9.web.app",
            "https://botarmy-ba0c9.firebaseapp.com",
        ],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
        max_age=3600,
    )
