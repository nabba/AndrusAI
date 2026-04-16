import httpx
import time
import threading
from typing import Any


class GoogleCalendarError(Exception):
    """Base exception for Google Calendar API errors."""
    pass


class GoogleCalendarAuthError(GoogleCalendarError):
    """Raised when authentication fails."""
    pass


class GoogleCalendarRateLimitError(GoogleCalendarError):
    """Raised when rate limit is exceeded."""
    pass


class GoogleCalendarNotFoundError(GoogleCalendarError):
    """Raised when resource is not found."""
    pass


class GoogleCalendarBadRequestError(GoogleCalendarError):
    """Raised when request is invalid."""
    pass


class GoogleCalendarForbiddenError(GoogleCalendarError):
    """Raised when access is forbidden."""
    pass


class RateLimiter:
    """Token bucket rate limiter for API requests."""

    def __init__(self, requests_per_second: float = 10.0):
        self._rate = requests_per_second
        self._tokens = requests_per_second
        self._last_update = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Acquire a token, blocking if necessary."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            self._last_update = now

            if self._tokens < 1.0:
                sleep_time = (1.0 - self._tokens) / self._rate
                time.sleep(sleep_time)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


class GoogleCalendarClient:
    """Client for Google Calendar API."""

    BASE_URL = "https://www.googleapis.com/calendar/v3"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str = "https://accounts.google.com/o/oauth2/token",
        scopes: list[str] | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        requests_per_second: float = 10.0,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._scopes = scopes or [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events",
        ]
        self._timeout = timeout
        self._max_retries = max_retries
        self._rate_limiter = RateLimiter(requests_per_second)

        self._access_token: str = ""
        self._expires_at: float = 0
        self._token_lock = threading.Lock()

    def _get_token(self) -> str:
        """Get a valid access token, refreshing if necessary."""
        with self._token_lock:
            if self._access_token and time.time() < self._expires_at - 60:
                return self._access_token
            return self._refresh_token()

    def _refresh_token(self) -> str:
        """Refresh the OAuth2 access token."""
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": " ".join(self._scopes),
        }

        resp = httpx.post(self._token_url, data=data, timeout=self._timeout)
        resp.raise_for_status()
        body = resp.json()

        self._access_token = body["access_token"]
        self._expires_at = time.time() + body.get("expires_in", 3600)
        return self._access_token

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers with authorization."""
        return {"Authorization": f"Bearer {self._get_token()}"}

    def _request_with_retry(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request with retry logic and rate limiting."""
        self._rate_limiter.acquire()

        url = f"{self.BASE_URL}{path}"
        headers = self._get_headers()

        for attempt in range(self._max_retries):
            try:
                resp = httpx.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_data,
                    timeout=self._timeout,
                )

                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < self._max_retries - 1:
                        wait_time = (2 ** attempt) + 0.1
                        time.sleep(wait_time)
                        continue
                    else:
                        raise GoogleCalendarRateLimitError(
                            f"Rate limit exceeded after {self._max_retries} attempts"
                        )

                if resp.status_code == 400:
                    raise GoogleCalendarBadRequestError(resp.text)
                if resp.status_code == 401:
                    raise GoogleCalendarAuthError("Authentication failed")
                if resp.status_code == 403:
                    raise GoogleCalendarForbiddenError("Access forbidden")
                if resp.status_code == 404:
                    raise GoogleCalendarNotFoundError(f"Resource not found: {path}")

                resp.raise_for_status()
                return resp.json()

            except httpx.HTTPStatusError:
                raise
            except httpx.HTTPError as e:
                if attempt < self._max_retries - 1:
                    wait_time = (2 ** attempt) + 0.1
                    time.sleep(wait_time)
                else:
                    raise GoogleCalendarError(f"Request failed: {e}") from e

        raise GoogleCalendarError("Max retries exceeded")

    def list_events(
        self,
        calendar_id: str,
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        """
        Return events on the specified calendar.

        Args:
            calendar_id: The calendar identifier.
            time_min: Lower bound (exclusive) for an event's start time.
            time_max: Upper bound (exclusive) for an event's start time.
            max_results: Maximum number of events returned on one result page.

        Returns:
            Dictionary containing event items and pagination token.
        """
        params: dict[str, Any] = {}
        if time_min is not None:
            params["timeMin"] = time_min
        if time_max is not None:
            params["timeMax"] = time_max
        if max_results is not None:
            params["maxResults"] = max_results

        return self._request_with_retry(
            "GET",
            f"/calendars/{calendar_id}/events",
            params=params,
        )

    def create_event(
        self,
        calendar_id: str,
        summary: str,
        description: str | None = None,
        start_datetime: str | None = None,
        end_datetime: str | None = None,
        start_timezone: str | None = None,
        end_timezone: str | None = None,
    ) -> dict[str, Any]:
        """
        Create an event on the specified calendar.

        Args:
            calendar_id: The calendar identifier.
            summary: Title of the event.
            description: Description of the event.
            start_datetime: Start time in RFC3339 format.
            end_datetime: End time in RFC3339 format.
            start_timezone: Timezone for the start time.
            end_timezone: Timezone for the end time.

        Returns:
            Dictionary containing created event details.
        """
        body: dict[str, Any] = {"summary": summary}

        if description is not None:
            body["description"] = description

        if start_datetime is not None:
            start: dict[str, Any] = {"dateTime": start_datetime}
            if start_timezone is not None:
                start["timeZone"] = start_timezone
            body["start"] = start

        if end_datetime is not None:
            end: dict[str, Any] = {"dateTime": end_datetime}
            if end_timezone is not None:
                end["timeZone"] = end_timezone
            body["end"] = end

        return self._request_with_retry(
            "POST",
            f"/calendars/{calendar_id}/events",
            json_data=body,
        )

    def get_event(
        self,
        calendar_id: str,
        event_id: str,
    ) -> dict[str, Any]:
        """
        Return a specific event from the calendar.

        Args:
            calendar_id: The calendar identifier.
            event_id: The event identifier.

        Returns:
            Dictionary containing event details.
        """
        return self._request_with_retry(
            "GET",
            f"/calendars/{calendar_id}/events/{event_id}",
        )
</content>
</tool_code>
</tool_code>
</connect>
</code>