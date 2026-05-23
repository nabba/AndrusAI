"""
google_workspace.auth — OAuth installed-app credentials for Google Workspace.

The bootstrap CLI runs the consent dance once and writes a refresh token
to ``workspace/google_token.json`` (chmod 600). Every tool call afterwards
reads the saved credentials, lets ``google.oauth2.credentials.Credentials``
refresh the access token automatically when it expires, and re-saves the
file when the refresh token rotates.

Account: ``andrus@raudsalu.com`` (per the user's setup); the bootstrap
will warn if a different account is consented through the browser.

Scopes are deliberately narrowed to the per-API minimum needed for the
five tool families:

    Gmail    gmail.modify           (read + send + label changes)
    Calendar calendar               (read + write + manage events)
    Docs     documents              (read + write document content)
    Sheets   spreadsheets           (read + write cells)
    Slides   presentations          (read + write slides)
    Drive    drive.file             (only files this app creates — Docs,
                                     Sheets, Slides decks the agent makes)

The user can re-run bootstrap to widen scopes; the file is rewritten
in-place.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Iterable

from app.config import get_google_oauth_client
from app.paths import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive.file",
)

TOKEN_PATH: Path = WORKSPACE_ROOT / "google_token.json"

_lock = threading.Lock()
_cached = None  # google.oauth2.credentials.Credentials | None


def is_configured() -> bool:
    """True when the operator has run the bootstrap and a refresh token exists."""
    return TOKEN_PATH.exists()


def get_credentials():
    """Load + refresh credentials. Returns None when not configured.

    The first call reads the JSON file from disk and constructs a
    ``Credentials`` object. Subsequent calls reuse the cached instance and
    let the google-auth library handle access-token refresh on demand.
    """
    global _cached
    if _cached is not None:
        return _cached
    with _lock:
        if _cached is not None:
            return _cached
        creds = _load_from_disk()
        if creds is None:
            return None
        _maybe_refresh(creds)
        _cached = creds
        return creds


def _load_from_disk():
    """Construct a ``Credentials`` object from the saved JSON, or None."""
    if not TOKEN_PATH.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
    except ImportError:
        logger.debug("google-auth not installed — Google Workspace disabled")
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text())
        creds = Credentials.from_authorized_user_info(data, list(SCOPES))
    except Exception as exc:
        logger.warning(f"google_workspace: failed to load credentials: {exc}")
        return None
    return creds


def _maybe_refresh(creds) -> None:
    """Refresh expired credentials and persist the rotated token."""
    if creds.valid:
        return
    if not creds.expired or not creds.refresh_token:
        return
    try:
        from google.auth.transport.requests import Request
    except ImportError:
        return
    try:
        creds.refresh(Request())
        save_credentials(creds)
        logger.info("google_workspace: refreshed access token")
    except Exception as exc:
        logger.warning(f"google_workspace: token refresh failed: {exc}")


def save_credentials(creds) -> None:
    """Persist credentials to disk with restrictive permissions."""
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(creds.to_json())
    tmp = TOKEN_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(TOKEN_PATH)
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass


def run_bootstrap_flow(scopes: Iterable[str] = SCOPES) -> bool:
    """Drive the installed-app consent flow. Returns True if a token was saved.

    Reads the OAuth client id/secret from settings (env-set by the operator)
    and opens the browser for consent. Refresh token gets persisted to
    ``TOKEN_PATH`` for all future calls.
    """
    client_id, client_secret = get_google_oauth_client()
    if not (client_id and client_secret):
        logger.error(
            "google_workspace bootstrap: GOOGLE_OAUTH_CLIENT_ID / "
            "GOOGLE_OAUTH_CLIENT_SECRET not set in .env"
        )
        return False

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        logger.error(
            "google_workspace bootstrap: google-auth-oauthlib not installed"
        )
        return False

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, list(scopes))
    # Try the browser-driven flow first; falls back to a copy-paste
    # path when the process is running headless (e.g. inside the
    # gateway container — no DISPLAY, no webbrowser, no localhost
    # port reachable from where the operator's browser actually is).
    creds = None
    try:
        creds = flow.run_local_server(
            port=0, prompt="consent", access_type="offline"
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "browser" in msg or "display" in msg or "could not locate" in msg:
            logger.info(
                "google_workspace bootstrap: no local browser — "
                "falling back to manual copy-paste flow"
            )
            creds = _run_manual_flow(flow)
        else:
            raise
    if creds is None:
        return False
    save_credentials(creds)
    # Reset the in-memory cache so the next get_credentials() picks up the
    # freshly-saved token without restarting the process.
    global _cached
    with _lock:
        _cached = None
    logger.info(f"google_workspace bootstrap: token saved to {TOKEN_PATH}")
    return True


def _run_manual_flow(flow):
    """Headless OAuth flow — operator opens the URL on any device,
    copies the redirected URL back. Replaces the deprecated OOB flow
    Google retired in 2022."""
    # Redirect to a localhost URL that the operator's browser will
    # fail to load — but the failed URL contains the auth code in its
    # query string, so we can recover it by reading the full URL.
    flow.redirect_uri = "http://localhost:8080/"
    auth_url, _state = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true",
    )
    print()
    print("─" * 72)
    print("HEADLESS OAUTH FLOW — no browser available in this container.")
    print("─" * 72)
    print()
    print("1. Open this URL on ANY device with a browser:")
    print()
    print(f"   {auth_url}")
    print()
    print("2. Consent as the expected Google account.")
    print()
    print("3. Your browser will redirect to a localhost URL that fails to")
    print("   load — that is expected. Copy the FULL URL from the address")
    print("   bar (it starts with http://localhost:8080/?state=...&code=...)")
    print("   and paste it below.")
    print()
    redirect_url = input("Paste the full redirect URL here: ").strip()
    if not redirect_url:
        print("Empty input — bootstrap aborted.")
        return None
    # oauthlib rejects HTTP redirects by default — but our redirect_uri
    # is localhost, which the browser never actually reaches over the
    # network (the URL fails to load; we extract the code from the
    # address bar). No traffic transits HTTP. The relaxation is scoped
    # to this single fetch_token call.
    prior = os.environ.get("OAUTHLIB_INSECURE_TRANSPORT")
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    try:
        flow.fetch_token(authorization_response=redirect_url)
    except Exception as exc:
        print(f"Bootstrap failed while exchanging the code: {exc}")
        return None
    finally:
        if prior is None:
            os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
        else:
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = prior
    return flow.credentials
