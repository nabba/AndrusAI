from __future__ import annotations

import asyncio
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple, Union, cast
from urllib.parse import quote

import httpx


JSONDict = Dict[str, Any]


class WorldBankClientError(Exception):
    """Base exception for all client errors."""


class WorldBankAPIError(WorldBankClientError):
    """Base exception for API errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        url: Optional[str] = None,
        response_text: Optional[str] = None,
        error_payload: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.response_text = response_text
        self.error_payload = error_payload


class WorldBankRateLimitError(WorldBankAPIError):
    """Raised when the API responds with HTTP 429."""


class WorldBankNotFoundError(WorldBankAPIError):
    """Raised when the requested resource is not found (HTTP 404)."""


class WorldBankInvalidRequestError(WorldBankAPIError):
    """Raised for invalid requests (HTTP 400)."""


class WorldBankServerError(WorldBankAPIError):
    """Raised for server-side errors (HTTP 5xx)."""


class WorldBankResponseParseError(WorldBankClientError):
    """Raised when the response cannot be parsed as expected."""


@dataclass(frozen=True)
class WorldBankCredentials:
    """
    Credentials placeholder for API compatibility.

    The World Bank Data API is generally unauthenticated. This structure exists to satisfy
    client construction requirements and to allow forward compatibility if auth is added.
    """

    token: Optional[str] = None
    api_key: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    extra: Optional[Mapping[str, str]] = None


class _SyncRateLimiter:
    def __init__(self, *, requests_per_second: Optional[float], requests_per_minute: Optional[float]) -> None:
        self._lock = threading.Lock()
        self._min_interval = self._compute_min_interval(requests_per_second, requests_per_minute)
        self._last_request_at: Optional[float] = None

    @staticmethod
    def _compute_min_interval(rps: Optional[float], rpm: Optional[float]) -> Optional[float]:
        intervals = []
        if rps and rps > 0:
            intervals.append(1.0 / float(rps))
        if rpm and rpm > 0:
            intervals.append(60.0 / float(rpm))
        return max(intervals) if intervals else None

    def wait(self) -> None:
        if self._min_interval is None:
            return
        with self._lock:
            now = time.monotonic()
            if self._last_request_at is None:
                self._last_request_at = now
                return
            elapsed = now - self._last_request_at
            to_sleep = self._min_interval - elapsed
            if to_sleep > 0:
                time.sleep(to_sleep)
                now = time.monotonic()
            self._last_request_at = now


class _AsyncRateLimiter:
    def __init__(self, *, requests_per_second: Optional[float], requests_per_minute: Optional[float]) -> None:
        self._lock = asyncio.Lock()
        self._min_interval = _SyncRateLimiter._compute_min_interval(requests_per_second, requests_per_minute)
        self._last_request_at: Optional[float] = None

    async def wait(self) -> None:
        if self._min_interval is None:
            return
        async with self._lock:
            now = time.monotonic()
            if self._last_request_at is None:
                self._last_request_at = now
                return
            elapsed = now - self._last_request_at
            to_sleep = self._min_interval - elapsed
            if to_sleep > 0:
                await asyncio.sleep(to_sleep)
                now = time.monotonic()
            self._last_request_at = now


class WorldBankClient:
    """
    Production-grade client for the World Bank Data API (v2).

    Provides both sync and async methods using httpx, with retries, backoff, and optional rate limiting.
    """

    def __init__(
        self,
        *,
        credentials: Optional[WorldBankCredentials] = None,
        base_url: str = "https://api.worldbank.org/v2",
        timeout: Union[float, httpx.Timeout] = 20.0,
        user_agent: str = "worldbank-python-client/1.0",
        max_retries: int = 4,
        backoff_factor: float = 0.5,
        max_backoff: float = 20.0,
        rate_limit_per_second: Optional[float] = None,
        rate_limit_per_minute: Optional[float] = None,
        httpx_limits: Optional[httpx.Limits] = None,
        transport: Optional[httpx.BaseTransport] = None,
        async_transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        """
        Initialize the client.

        Args:
            credentials: Credentials container (API is typically unauthenticated; kept for compatibility).
            base_url: API base URL.
            timeout: Request timeout.
            user_agent: User-Agent header value.
            max_retries: Maximum retries for 429 and 5xx responses, and transient network errors.
            backoff_factor: Base factor for exponential backoff.
            max_backoff: Maximum backoff delay in seconds.
            rate_limit_per_second: Optional client-side rate limit (requests per second).
            rate_limit_per_minute: Optional client-side rate limit (requests per minute).
            httpx_limits: Optional httpx connection pool limits.
            transport: Optional custom httpx transport for sync client.
            async_transport: Optional custom httpx transport for async client.
        """
        self._credentials = credentials or WorldBankCredentials()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._user_agent = user_agent
        self._max_retries = int(max_retries)
        self._backoff_factor = float(backoff_factor)
        self._max_backoff = float(max_backoff)

        self._sync_limiter = _SyncRateLimiter(
            requests_per_second=rate_limit_per_second,
            requests_per_minute=rate_limit_per_minute,
        )
        self._async_limiter = _AsyncRateLimiter(
            requests_per_second=rate_limit_per_second,
            requests_per_minute=rate_limit_per_minute,
        )

        headers = {
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }

        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            headers=headers,
            limits=httpx_limits,
            transport=transport,
        )
        self._async_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers=headers,
            limits=httpx_limits,
            transport=async_transport,
        )

    def close(self) -> None:
        """Close underlying HTTP resources for the synchronous client."""
        self._client.close()

    async def aclose(self) -> None:
        """Close underlying HTTP resources for the asynchronous client."""
        await self._async_client.aclose()

    def __enter__(self) -> "WorldBankClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    async def __aenter__(self) -> "WorldBankClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    @staticmethod
    def _wrap_worldbank_json(payload: Any) -> JSONDict:
        """
        World Bank API commonly returns a JSON list of [metadata, data].
        Normalize to {"meta": ..., "data": ...} to satisfy dict return type.
        """
        if isinstance(payload, list) and len(payload) == 2:
            return {"meta": payload[0], "data": payload[1]}
        if isinstance(payload, dict):
            return cast(JSONDict, payload)
        # Fallback: wrap anything else
        return {"data": payload}

    @staticmethod
    def _parse_retry_after(headers: httpx.Headers) -> Optional[float]:
        ra = headers.get("Retry-After")
        if not ra:
            return None
        ra = ra.strip()
        try:
            return float(ra)
        except ValueError:
            # HTTP-date not handled; ignore
            return None

    def _compute_backoff(self, attempt: int, *, retry_after: Optional[float]) -> float:
        if retry_after is not None and retry_after > 0:
            return min(retry_after, self._max_backoff)
        # exponential backoff with jitter
        base = self._backoff_factor * (2.0 ** max(0, attempt))
        jitter = random.uniform(0.0, min(1.0, base * 0.1))
        return min(base + jitter, self._max_backoff)

    def _raise_for_status(self, resp: httpx.Response, payload: Optional[Any]) -> None:
        status = resp.status_code
        url = str(resp.request.url)
        text = None
        try:
            text = resp.text
        except Exception:
            text = None

        if status == 429:
            raise WorldBankRateLimitError(
                "Rate limit exceeded (HTTP 429).",
                status_code=status,
                url=url,
                response_text=text,
                error_payload=payload,
            )
        if status == 404:
            raise WorldBankNotFoundError(
                "Resource not found (HTTP 404).",
                status_code=status,
                url=url,
                response_text=text,
                error_payload=payload,
            )
        if status == 400:
            raise WorldBankInvalidRequestError(
                "Bad request (HTTP 400).",
                status_code=status,
                url=url,
                response_text=text,
                error_payload=payload,
            )
        if 400 <= status < 500:
            raise WorldBankAPIError(
                f"Client error (HTTP {status}).",
                status_code=status,
                url=url,
                response_text=text,
                error_payload=payload,
            )
        if 500 <= status:
            raise WorldBankServerError(
                f"Server error (HTTP {status}).",
                status_code=status,
                url=url,
                response_text=text,
                error_payload=payload,
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
    ) -> JSONDict:
        self._sync_limiter.wait()

        merged_params: Dict[str, Any] = {}
        if params:
            merged_params.update(dict(params))
        merged_params.setdefault("format", "json")

        last_exc: Optional[BaseException] = None
        for attempt in range(0, self._max_retries + 1):
            try:
                resp = self._client.request(method, path, params=merged_params)
                retry_after = self._parse_retry_after(resp.headers)

                payload: Optional[Any] = None
                if resp.status_code < 400:
                    try:
                        payload = resp.json()
                    except Exception as e:
                        raise WorldBankResponseParseError(f"Failed to parse JSON response: {e}") from e
                    return self._wrap_worldbank_json(payload)

                # Error response: try parse JSON for diagnostic payload
                try:
                    payload = resp.json()
                except Exception:
                    payload = None

                if resp.status_code == 429 or 500 <= resp.status_code <= 599:
                    if attempt < self._max_retries:
                        time.sleep(self._compute_backoff(attempt, retry_after=retry_after))
                        self._sync_limiter.wait()
                        continue

                self._raise_for_status(resp, payload)
                raise WorldBankAPIError(
                    f"Unexpected HTTP status: {resp.status_code}",
                    status_code=resp.status_code,
                    url=str(resp.request.url),
                    response_text=resp.text,
                    error_payload=payload,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exc = e
                if attempt < self._max_retries:
                    time.sleep(self._compute_backoff(attempt, retry_after=None))
                    self._sync_limiter.wait()
                    continue
                raise WorldBankAPIError(f"Network error after retries: {e}") from e
            except WorldBankClientError:
                raise
            except Exception as e:
                last_exc = e
                raise WorldBankClientError(f"Unexpected error: {e}") from e

        raise WorldBankAPIError(f"Request failed after retries: {last_exc}")

    async def _arequest(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
    ) -> JSONDict:
        await self._async_limiter.wait()

        merged_params: Dict[str, Any] = {}
        if params:
            merged_params.update(dict(params))
        merged_params.setdefault("format", "json")

        last_exc: Optional[BaseException] = None
        for attempt in range(0, self._max_retries + 1):
            try:
                resp = await self._async_client.request(method, path, params=merged_params)
                retry_after = self._parse_retry_after(resp.headers)

                payload: Optional[Any] = None
                if resp.status_code < 400:
                    try:
                        payload = resp.json()
                    except Exception as e:
                        raise WorldBankResponseParseError(f"Failed to parse JSON response: {e}") from e
                    return self._wrap_worldbank_json(payload)

                try:
                    payload = resp.json()
                except Exception:
                    payload = None

                if resp.status_code == 429 or 500 <= resp.status_code <= 599:
                    if attempt < self._max_retries:
                        await asyncio.sleep(self._compute_backoff(attempt, retry_after=retry_after))
                        await self._async_limiter.wait()
                        continue

                self._raise_for_status(resp, payload)
                raise WorldBankAPIError(
                    f"Unexpected HTTP status: {resp.status_code}",
                    status_code=resp.status_code,
                    url=str(resp.request.url),
                    response_text=resp.text,
                    error_payload=payload,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exc = e
                if attempt < self._max_retries:
                    await asyncio.sleep(self._compute_backoff(attempt, retry_after=None))
                    await self._async_limiter.wait()
                    continue
                raise WorldBankAPIError(f"Network error after retries: {e}") from e
            except WorldBankClientError:
                raise
            except Exception as e:
                last_exc = e
                raise WorldBankClientError(f"Unexpected error: {e}") from e

        raise WorldBankAPIError(f"Request failed after retries: {last_exc}")

    @staticmethod
    def _encode_path_segment(value: str) -> str:
        return quote(value, safe="")

    def list_countries(
        self,
        *,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
        extra_params: Optional[Mapping[str, Any]] = None,
    ) -> JSONDict:
        """
        List countries.

        Args:
            page: Page number (World Bank API pagination).
            per_page: Items per page.
            extra_params: Additional query parameters supported by the API.

        Returns:
            Parsed JSON payload as a dict: {"meta": ..., "data": ...}
        """
        params: Dict[str, Any] = {}
        if page is not None:
            params["page"] = int(page)
        if per_page is not None:
            params["per_page"] = int(per_page)
        if extra_params