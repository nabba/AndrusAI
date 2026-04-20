from __future__ import annotations

import asyncio
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

import httpx


class EarthEngineError(Exception):
    """Base exception for all client errors."""


class AuthenticationError(EarthEngineError):
    """Raised when authentication fails (401/403 or token fetch failure)."""


class RateLimitError(EarthEngineError):
    """Raised when the API rate limits the request (429) after retries."""


class APIError(EarthEngineError):
    """Raised for non-success API responses."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        details: Any = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.details = details
        self.request_id = request_id


class NetworkError(EarthEngineError):
    """Raised for network-level errors (timeouts, DNS, connection errors)."""


def _now() -> float:
    return time.monotonic()


def _parse_google_error(payload: Any) -> tuple[Optional[str], Optional[str], Any]:
    """
    Attempts to parse common Google JSON error formats.
    Returns: (message, status, details)
    """
    if not isinstance(payload, dict):
        return None, None, payload
    err = payload.get("error")
    if isinstance(err, dict):
        message = err.get("message")
        status = err.get("status") or err.get("code")
        details = err.get("details") or err
        return message, str(status) if status is not None else None, details
    # Sometimes APIs return { "message": "...", "status": "..." }
    message = payload.get("message")
    status = payload.get("status")
    return message, str(status) if status is not None else None, payload


@dataclass(frozen=True)
class RetryConfig:
    """Retry configuration for transient errors."""
    max_retries: int = 5
    backoff_initial: float = 0.5
    backoff_max: float = 20.0
    jitter_ratio: float = 0.2


class RateLimiter:
    """
    Simple blocking rate limiter enforcing optional per-second and per-minute limits.
    """

    def __init__(
        self,
        *,
        requests_per_second: Optional[float] = None,
        requests_per_minute: Optional[float] = None,
    ) -> None:
        self._rps = requests_per_second
        self._rpm = requests_per_minute
        self._lock = threading.Lock()
        self._sec_window_start = _now()
        self._sec_count = 0
        self._min_window_start = _now()
        self._min_count = 0

    def acquire(self) -> None:
        """Block until a request slot is available."""
        if self._rps is None and self._rpm is None:
            return

        while True:
            sleep_for = 0.0
            with self._lock:
                now = _now()
                # Reset windows if elapsed.
                if now - self._sec_window_start >= 1.0:
                    self._sec_window_start = now
                    self._sec_count = 0
                if now - self._min_window_start >= 60.0:
                    self._min_window_start = now
                    self._min_count = 0

                # Determine if allowed; if not, compute sleep time.
                if self._rps is not None and self._sec_count >= self._rps:
                    sleep_for = max(0.0, 1.0 - (now - self._sec_window_start))
                if self._rpm is not None and self._min_count >= self._rpm:
                    sleep_for = max(sleep_for, 60.0 - (now - self._min_window_start))

                if sleep_for == 0.0:
                    self._sec_count += 1
                    self._min_count += 1
                    return

            time.sleep(min(sleep_for, 1.0))


class AsyncRateLimiter:
    """
    Async rate limiter enforcing optional per-second and per-minute limits.
    """

    def __init__(
        self,
        *,
        requests_per_second: Optional[float] = None,
        requests_per_minute: Optional[float] = None,
    ) -> None:
        self._rps = requests_per_second
        self._rpm = requests_per_minute
        self._lock = asyncio.Lock()
        self._sec_window_start = _now()
        self._sec_count = 0
        self._min_window_start = _now()
        self._min_count = 0

    async def acquire(self) -> None:
        """Await until a request slot is available."""
        if self._rps is None and self._rpm is None:
            return

        while True:
            sleep_for = 0.0
            async with self._lock:
                now = _now()
                if now - self._sec_window_start >= 1.0:
                    self._sec_window_start = now
                    self._sec_count = 0
                if now - self._min_window_start >= 60.0:
                    self._min_window_start = now
                    self._min_count = 0

                if self._rps is not None and self._sec_count >= self._rps:
                    sleep_for = max(0.0, 1.0 - (now - self._sec_window_start))
                if self._rpm is not None and self._min_count >= self._rpm:
                    sleep_for = max(sleep_for, 60.0 - (now - self._min_window_start))

                if sleep_for == 0.0:
                    self._sec_count += 1
                    self._min_count += 1
                    return

            await asyncio.sleep(min(sleep_for, 1.0))


class AuthOAuth2ClientCredentials:
    """OAuth2 Client Credentials flow with automatic token refresh."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str,
        scopes: Optional[list[str]] = None,
        audience: str = "",
        timeout: float = 30.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._scopes = scopes or []
        self._audience = audience
        self._timeout = timeout
        self._access_token: str = ""
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def get_token(self) -> str:
        """Return a valid access token, refreshing automatically if needed."""
        with self._lock:
            if self._access_token and time.time() < self._expires_at - 60:
                return self._access_token
            return self._refresh_token()

    def _refresh_token(self) -> str:
        data: Dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._scopes:
            data["scope"] = " ".join(self._scopes)
        if self._audience:
            data["audience"] = self._audience

        try:
            resp = httpx.post(self._token_url, data=data, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise NetworkError(f"Token request timed out: {e}") from e
        except httpx.RequestError as e:
            raise NetworkError(f"Token request failed: {e}") from e

        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except Exception:
                payload = resp.text
            msg, status, details = _parse_google_error(payload)
            raise AuthenticationError(
                f"Token request failed ({resp.status_code}): {msg or resp.text}"
            ) from APIError(
                msg or "Token request failed",
                status_code=resp.status_code,
                error_code=status,
                details=details,
                request_id=resp.headers.get("x-request-id") or resp.headers.get("x-guploader-uploadid"),
            )

        body = resp.json()
        if "access_token" not in body:
            raise AuthenticationError(f"Token response missing access_token: {body!r}")
        self._access_token = str(body["access_token"])
        self._expires_at = time.time() + float(body.get("expires_in", 3600))
        return self._access_token

    def get_headers(self) -> Dict[str, str]:
        """Return authorization headers for API requests."""
        return {"Authorization": f"Bearer {self.get_token()}"}


class EarthEngineClient:
    """
    Synchronous Google Earth Engine REST API client (Discovery endpoints).

    Base URL: https://earthengine.googleapis.com
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        scopes: Optional[list[str]] = None,
        token_url: str = "https://oauth2.googleapis.com/token",
        base_url: str = "https://earthengine.googleapis.com",
        timeout: float = 30.0,
        retry: RetryConfig = RetryConfig(),
        requests_per_second: Optional[float] = None,
        requests_per_minute: Optional[float] = None,
        user_agent: str = "earthengine-python-client/1.0",
    ) -> None:
        """
        Create a new client.

        Args:
            client_id: OAuth2 client id.
            client_secret: OAuth2 client secret.
            scopes: OAuth2 scopes.
            token_url: OAuth2 token endpoint.
            base_url: API base URL.
            timeout: Request timeout in seconds.
            retry: Retry configuration for transient failures.
            requests_per_second: Optional client-side RPS limit.
            requests_per_minute: Optional client-side RPM limit.
            user_agent: User-Agent header.
        """
        self._auth = AuthOAuth2ClientCredentials(
            client_id=client_id,
            client_secret=client_secret,
            token_url=token_url,
            scopes=scopes
            or [
                "https://www.googleapis.com/auth/earthengine",
                "profile",
                "email",
            ],
            timeout=timeout,
        )
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retry = retry
        self._limiter = RateLimiter(
            requests_per_second=requests_per_second,
            requests_per_minute=requests_per_minute,
        )
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> "EarthEngineClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def get_discovery(self, version: str = "v1") -> Dict[str, Any]:
        """
        Fetch the discovery document for a given API version.

        Args:
            version: One of 'v1', 'v1beta', 'v1alpha'.

        Returns:
            Parsed JSON discovery document as a dict.
        """
        return self._request_json(
            "GET",
            "/$discovery/rest",
            params={"version": version},
        )

    def get_discovery_v1(self) -> Dict[str, Any]:
        """Fetch the v1 discovery document."""
        return self.get_discovery("v1")

    def get_discovery_v1beta(self) -> Dict[str, Any]:
        """Fetch the v1beta discovery document."""
        return self.get_discovery("v1beta")

    def get_discovery_v1alpha(self) -> Dict[str, Any]:
        """Fetch the v1alpha discovery document."""
        return self.get_discovery("v1alpha")

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Optional[Any] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        req_headers = dict(self._auth.get_headers())
        if headers:
            req_headers.update(headers)

        attempt = 0
        while True:
            self._limiter.acquire()
            try:
                resp = self._client.request(
                    method,
                    path,
                    params=dict(params) if params else None,
                    json=json,
                    headers=req_headers,
                )
            except httpx.TimeoutException as e:
                if attempt >= self._retry.max_retries:
                    raise NetworkError(f"Request timed out after retries: {e}") from e
                self._sleep_backoff(attempt)
                attempt += 1
                continue
            except httpx.RequestError as e:
                if attempt >= self._retry.max_retries:
                    raise NetworkError(f"Network error after retries: {e}") from e
                self._sleep_backoff(attempt)
                attempt += 1
                continue

            if resp.status_code == 401 or resp.status_code == 403:
                # Token might be expired or invalid; force refresh once.
                if attempt == 0:
                    # Refresh token and retry
                    try:
                        self._auth._refresh_token()
                    except EarthEngineError:
                        pass
                    attempt += 1
                    self._sleep_backoff(0)
                    continue
                raise AuthenticationError(self._format_error(resp))

            if resp.status_code == 429 or 500 <= resp.status_code <= 599:
                if attempt >= self._retry.max_retries:
                    if resp.status_code == 429:
                        raise RateLimitError(self._format_error(resp))
                    raise APIError(
                        self._format_error(resp),
                        status_code=resp.status_code,
                        error_code=_parse_google_error(self._safe_json(resp))[1],
                        details=_parse_google_error(self._safe_json(resp))[2],
                        request_id=resp.headers.get("x-request-id") or resp.headers.get("x-guploader-uploadid"),
                    )
                retry_after = self._get_retry_after_seconds(resp)
                self._sleep_backoff(attempt, retry_after=retry_after)
                attempt += 1
                continue

            if resp.status_code < 200 or resp.status_code >= 300:
                payload = self._safe_json(resp)
                msg, status, details = _parse_google_error(payload)
                raise APIError(
                    msg or f"HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    error_code=status,
                    details=details,
                    request_id=resp.headers.get("x-request-id") or resp.headers.get("x-guploader-uploadid"),
                )

            payload = self._safe_json(resp)
            if not isinstance(payload, dict):
                # Discovery should be JSON object; still return as dict-like wrapper if possible
                return {"data": payload}
            return payload

    def _safe_json(self, resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}

    def _format_error(self, resp: httpx.Response) -> str:
        payload = self._safe_json(resp)
        msg, status, _details = _parse_google_error(payload)
        parts = [f"HTTP {resp.status_code}"]
        if status:
            parts.append(f"status={status}")
        if msg:
            parts.append(msg)
        else:
            parts.append(resp.text.strip() or "Unknown error")
        return " - ".join(parts)

    def _get_retry_after_seconds(self, resp: httpx.Response) -> Optional[float]:
        ra = resp.headers.get("Retry-After")
        if not ra:
            return None
        try:
            return float(ra)
        except ValueError:
            return None

    def _sleep_backoff(self, attempt: int, *, retry_after: Optional[float] = None) -> None:
        base = min(self._retry.backoff_max, self._retry.backoff_initial * (2**attempt))
        jitter = base * self._retry.jitter_ratio * (2 * random.random() - 1)
        delay = max(0.0, base + jitter)
        if retry_after is not None:
            delay = max(delay, retry_after)
        time.sleep(delay)


class AsyncEarthEngineClient:
    """
    Asynchronous Google Earth Engine REST API client (Discovery endpoints).

    Base URL: https://earthengine.googleapis.com
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        scopes: Optional[list[str]] = None,
        token_url: str = "https://oauth2.googleapis.com/token",
        base_url: str = "https://earthengine.googleapis.com",
        timeout: float = 30.0,
        retry: RetryConfig = RetryConfig(),
        requests_per_second: Optional[float] = None,
        requests_per_minute: Optional[float] = None,
        user_agent: str = "earthengine-python-client/1.0",
    )