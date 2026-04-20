from __future__ import annotations

import asyncio
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx


class GFWError(Exception):
    """Base exception for Global Forest Watch Data API client errors."""

    pass


class GFWRequestError(GFWError):
    """Raised when a request cannot be completed due to a transport/network error."""

    pass


class GFWResponseError(GFWError):
    """Raised when a response cannot be parsed or is otherwise invalid."""

    pass


class GFWHTTPError(GFWError):
    """Raised when the API returns an error HTTP status code."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        url: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        response_text: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.details = details
        self.response_text = response_text


class GFWUnauthorizedError(GFWHTTPError):
    """401 Unauthorized."""

    pass


class GFWForbiddenError(GFWHTTPError):
    """403 Forbidden."""

    pass


class GFWNotFoundError(GFWHTTPError):
    """404 Not Found."""

    pass


class GFWRateLimitError(GFWHTTPError):
    """429 Too Many Requests."""

    pass


class GFWServerError(GFWHTTPError):
    """5xx Server error."""

    pass


@dataclass(frozen=True)
class RetryConfig:
    """Retry configuration for transient failures."""

    max_retries: int = 5
    backoff_initial: float = 0.5
    backoff_max: float = 20.0
    backoff_multiplier: float = 2.0
    jitter: float = 0.25  # fraction of sleep time to jitter by (+/-)


@dataclass(frozen=True)
class RateLimitConfig:
    """Client-side rate limiting configuration.

    If both are provided, the stricter (longer minimum interval) is enforced.
    """

    requests_per_second: Optional[float] = None
    requests_per_minute: Optional[float] = None


class _SyncRateLimiter:
    def __init__(self, config: RateLimitConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def _min_interval(self) -> float:
        intervals: list[float] = []
        if self._config.requests_per_second and self._config.requests_per_second > 0:
            intervals.append(1.0 / self._config.requests_per_second)
        if self._config.requests_per_minute and self._config.requests_per_minute > 0:
            intervals.append(60.0 / self._config.requests_per_minute)
        return max(intervals) if intervals else 0.0

    def acquire(self) -> None:
        interval = self._min_interval()
        if interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
            self._next_allowed = max(self._next_allowed, time.monotonic()) + interval


class _AsyncRateLimiter:
    def __init__(self, config: RateLimitConfig) -> None:
        self._config = config
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    def _min_interval(self) -> float:
        intervals: list[float] = []
        if self._config.requests_per_second and self._config.requests_per_second > 0:
            intervals.append(1.0 / self._config.requests_per_second)
        if self._config.requests_per_minute and self._config.requests_per_minute > 0:
            intervals.append(60.0 / self._config.requests_per_minute)
        return max(intervals) if intervals else 0.0

    async def acquire(self) -> None:
        interval = self._min_interval()
        if interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_allowed = max(self._next_allowed, time.monotonic()) + interval


class GFWDataAPIClient:
    """Production-quality client for the Global Forest Watch Data API.

    Supports sync and async requests via httpx with:
      - API key header authentication (x-api-key)
      - retry with exponential backoff for 429/5xx
      - optional client-side rate limiting
      - typed methods returning parsed JSON dictionaries
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://data-api.globalforestwatch.org",
        timeout: float = 30.0,
        retry: Optional[RetryConfig] = None,
        rate_limit: Optional[RateLimitConfig] = None,
        user_agent: str = "gfw-data-api-python-client/1.0",
        verify: bool = True,
        proxies: Optional[str] = None,
    ) -> None:
        """Create a new client.

        Args:
            api_key: GFW API key to be sent as `x-api-key` header.
            base_url: Base URL for the API.
            timeout: Request timeout in seconds.
            retry: Retry configuration (429/5xx).
            rate_limit: Optional client-side rate limiting settings.
            user_agent: User-Agent header value.
            verify: TLS verification setting passed to httpx.
            proxies: Proxy URL (e.g., "http://localhost:8080") passed to httpx.
        """
        if not api_key:
            raise ValueError("api_key is required")

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retry = retry or RetryConfig()
        self._rate_limit = rate_limit or RateLimitConfig()

        headers = {
            "x-api-key": api_key,
            "accept": "application/json",
            "user-agent": user_agent,
        }

        self._client = httpx.Client(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout,
            verify=verify,
            proxies=proxies,
        )
        self._aclient = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout,
            verify=verify,
            proxies=proxies,
        )

        self._sync_limiter = _SyncRateLimiter(self._rate_limit)
        self._async_limiter = _AsyncRateLimiter(self._rate_limit)

    def close(self) -> None:
        """Close the underlying synchronous httpx client."""
        self._client.close()

    async def aclose(self) -> None:
        """Close the underlying asynchronous httpx client."""
        await self._aclient.aclose()

    def __enter__(self) -> "GFWDataAPIClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    async def __aenter__(self) -> "GFWDataAPIClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    def get_openapi(self) -> dict[str, Any]:
        """Retrieve the OpenAPI specification (JSON) from `/openapi.json`."""
        return self.request_json("GET", "/openapi.json")

    async def aget_openapi(self) -> dict[str, Any]:
        """Asynchronously retrieve the OpenAPI specification (JSON) from `/openapi.json`."""
        return await self.arequest_json("GET", "/openapi.json")

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Make a synchronous request and return parsed JSON.

        Args:
            method: HTTP method (GET, POST, ...).
            path: API path, e.g. "/openapi.json".
            params: Query parameters.
            json: JSON request body (if applicable).
            headers: Additional headers to merge into the request.

        Returns:
            Parsed JSON response as a dictionary.

        Raises:
            GFWHTTPError: For HTTP error responses.
            GFWRequestError: For network/transport errors.
            GFWResponseError: For invalid/malformed JSON responses.
        """
        if not path.startswith("/"):
            path = "/" + path

        retries = self._retry.max_retries
        attempt = 0

        while True:
            self._sync_limiter.acquire()
            try:
                resp = self._client.request(
                    method=method,
                    url=path,
                    params=params,
                    json=json,
                    headers=headers,
                )
            except httpx.RequestError as e:
                if attempt >= retries:
                    raise GFWRequestError(f"Request failed: {e!r}") from e
                self._sleep_backoff(attempt)
                attempt += 1
                continue

            if self._should_retry(resp.status_code):
                if attempt >= retries:
                    raise self._http_error_from_response(resp)
                sleep_s = self._retry_after_seconds(resp) or self._compute_backoff(attempt)
                self._sleep(sleep_s)
                attempt += 1
                continue

            if resp.status_code >= 400:
                raise self._http_error_from_response(resp)

            return self._parse_json(resp)

    async def arequest_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Make an asynchronous request and return parsed JSON.

        Args:
            method: HTTP method (GET, POST, ...).
            path: API path, e.g. "/openapi.json".
            params: Query parameters.
            json: JSON request body (if applicable).
            headers: Additional headers to merge into the request.

        Returns:
            Parsed JSON response as a dictionary.

        Raises:
            GFWHTTPError: For HTTP error responses.
            GFWRequestError: For network/transport errors.
            GFWResponseError: For invalid/malformed JSON responses.
        """
        if not path.startswith("/"):
            path = "/" + path

        retries = self._retry.max_retries
        attempt = 0

        while True:
            await self._async_limiter.acquire()
            try:
                resp = await self._aclient.request(
                    method=method,
                    url=path,
                    params=params,
                    json=json,
                    headers=headers,
                )
            except httpx.RequestError as e:
                if attempt >= retries:
                    raise GFWRequestError(f"Request failed: {e!r}") from e
                await self._asleep_backoff(attempt)
                attempt += 1
                continue

            if self._should_retry(resp.status_code):
                if attempt >= retries:
                    raise self._http_error_from_response(resp)
                sleep_s = self._retry_after_seconds(resp) or self._compute_backoff(attempt)
                await self._asleep(sleep_s)
                attempt += 1
                continue

            if resp.status_code >= 400:
                raise self._http_error_from_response(resp)

            return self._parse_json(resp)

    def _parse_json(self, resp: httpx.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError as e:
            text = None
            try:
                text = resp.text
            except Exception:
                text = None
            raise GFWResponseError(
                f"Response was not valid JSON (status={resp.status_code}, url={resp.request.url!s}). "
                f"Body excerpt: {((text or '')[:500])!r}"
            ) from e

        if isinstance(data, dict):
            return data
        # Some endpoints may return arrays; requirement says dict. Wrap to preserve data.
        return {"data": data}

    def _should_retry(self, status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code <= 599

    def _retry_after_seconds(self, resp: httpx.Response) -> Optional[float]:
        ra = resp.headers.get("retry-after")
        if not ra:
            return None
        ra = ra.strip()
        try:
            return max(0.0, float(ra))
        except ValueError:
            # Could be HTTP date, ignore for simplicity (fallback to exponential backoff)
            return None

    def _compute_backoff(self, attempt: int) -> float:
        base = self._retry.backoff_initial * (self._retry.backoff_multiplier ** attempt)
        base = min(base, self._retry.backoff_max)
        if base <= 0:
            return 0.0
        jitter_fraction = max(0.0, self._retry.jitter)
        jitter = base * jitter_fraction
        return max(0.0, base + random.uniform(-jitter, jitter))

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    async def _asleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)

    def _sleep_backoff(self, attempt: int) -> None:
        self._sleep(self._compute_backoff(attempt))

    async def _asleep_backoff(self, attempt: int) -> None:
        await self._asleep(self._compute_backoff(attempt))

    def _http_error_from_response(self, resp: httpx.Response) -> GFWHTTPError:
        status = resp.status_code
        url = str(resp.request.url)

        details: Optional[dict[str, Any]] = None
        response_text: Optional[str] = None

        # Try to parse JSON error details if present.
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                details = parsed
            else:
                details = {"data": parsed}
        except Exception:
            try:
                response_text = resp.text
            except Exception:
                response_text = None

        message = self._error_message(status, details, response_text)

        if status == 401:
            return GFWUnauthorizedError(status, message, url=url, details=details, response_text=response_text)
        if status == 403:
            return GFWForbiddenError(status, message, url=url, details=details, response_text=response_text)
        if status == 404:
            return GFWNotFoundError(status, message, url=url, details=details, response_text=response_text)
        if status == 429:
            return GFWRateLimitError(status, message, url=url, details=details, response_text=response_text)
        if 500 <= status <= 599:
            return GFWServerError(status, message, url=url, details=details, response_text=response_text)
        return GFWHTTPError(status, message, url=url, details=details, response_text=response_text)

    def _error_message(
        self,
        status: int,
        details: Optional[dict[str, Any]],
        response_text: Optional[str],
    ) -> str:
        base = f"HTTP {status} error from GFW Data API"
        if details:
            for key in ("message", "error", "errors", "detail", "title"):
                if key in details and isinstance(details[key], (str, int, float, bool)):
                    return f"{base}: {details[key]}"
            return f"{base}: {details}"
        if response_text:
            excerpt = response_text[:500]
            return f"{base}: {excerpt!r}"
        return base