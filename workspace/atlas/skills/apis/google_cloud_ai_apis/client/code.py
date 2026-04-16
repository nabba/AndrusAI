import httpx
import time
import threading
import logging
from typing import Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GoogleCloudAIError(Exception):
    """Base exception for Google Cloud AI API errors."""
    
    def __init__(self, message: str, status_code: int | None = None, response_body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class AuthenticationError(GoogleCloudAIError):
    """Raised when authentication fails (401)."""
    pass


class ForbiddenError(GoogleCloudAIError):
    """Raised when access is forbidden (403)."""
    pass


class RateLimitError(GoogleCloudAIError):
    """Raised when rate limit is exceeded (429)."""
    pass


class ServerError(GoogleCloudAIError):
    """Raised when server returns 5xx error."""
    pass


class AuthOAuth2ClientCredentials:
    """OAuth2 Client Credentials flow with automatic token refresh."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str,
        scopes: list[str] | None = None,
        audience: str = ""
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._scopes = scopes or []
        self._audience = audience
        self._access_token: str = ""
        self._expires_at: float = 0
        self._lock = threading.Lock()

    def get_token(self) -> str:
        """Get a valid access token, refreshing if necessary."""
        with self._lock:
            if self._access_token and time.time() < self._expires_at - 60:
                return self._access_token
            return self._refresh_token()

    def _refresh_token(self) -> str:
        """Refresh the access token using client credentials."""
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._scopes:
            data["scope"] = " ".join(self._scopes)
        if self._audience:
            data["audience"] = self._audience

        resp = httpx.post(self._token_url, data=data, timeout=30)
        resp.raise_for_status()
        body = resp.json()

        self._access_token = body["access_token"]
        self._expires_at = time.time() + body.get("expires_in", 3600)
        return self._access_token

    def get_headers(self) -> dict[str, str]:
        """Get authorization headers with Bearer token."""
        return {"Authorization": f"Bearer {self.get_token()}"}


class RateLimiter:
    """Token bucket rate limiter for API requests."""

    def __init__(self, requests_per_second: float = 10.0):
        self._rate = requests_per_second
        self._tokens: float = requests_per_second
        self._max_tokens: float = requests_per_second
        self._last_update: float = time.time()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        """Acquire tokens, waiting if necessary."""
        with self._lock:
            while True:
                self._refill_tokens()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                sleep_time = (tokens - self._tokens) / self._rate
                time.sleep(sleep_time)

    def _refill_tokens(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self._last_update
        self._tokens = min(self._max_tokens, self._tokens + elapsed * self._rate)
        self._last_update = now


class GoogleCloudAIClient:
    """Client for Google Cloud AI APIs.
    
    Provides methods to interact with Google Cloud AI services including
    Application Integration and Cloud Endpoints APIs.
    
    Args:
        client_id: OAuth2 client ID
        client_secret: OAuth2 client secret
        token_url: OAuth2 token endpoint URL
        scopes: List of OAuth2 scopes to request
        base_url: Base URL for API requests (defaults to Google Cloud AI docs)
        requests_per_second: Rate limit for requests (default 10)
        max_retries: Maximum number of retry attempts (default 3)
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str = "https://oauth2.googleapis.com/token",
        scopes: list[str] | None = None,
        base_url: str = "https://cloud.google.com",
        requests_per_second: float = 10.0,
        max_retries: int = 3
    ):
        self._auth = AuthOAuth2ClientCredentials(
            client_id=client_id,
            client_secret=client_secret,
            token_url=token_url,
            scopes=scopes or [
                "https://www.googleapis.com/auth/developerprofiles",
                "https://www.googleapis.com/auth/developerprofiles.award",
                "https://www.googleapis.com/auth/devprofiles.full_control.firstparty"
            ]
        )
        self._base_url = base_url
        self._rate_limiter = RateLimiter(requests_per_second=requests_per_second)
        self._max_retries = max_retries
        self._client = httpx.Client(timeout=30.0)

    def _request_with_retry(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        retry_count: int = 0
    ) -> dict[str, Any]:
        """Make HTTP request with retry logic and exponential backoff."""
        self._rate_limiter.acquire()
        
        try:
            response = self._client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_data
            )
            
            if response.status_code == 200:
                if "application/json" in response.headers.get("content-type", ""):
                    return response.json()
                return {"content": response.text}
            elif response.status_code == 401:
                raise AuthenticationError(
                    "Authentication failed. Check your credentials.",
                    status_code=401,
                    response_body=response.text
                )
            elif response.status_code == 403:
                raise ForbiddenError(
                    "Access forbidden. Check your permissions.",
                    status_code=403,
                    response_body=response.text
                )
            elif response.status_code == 429:
                if retry_count < self._max_retries:
                    wait_time = 2 ** retry_count
                    logger.warning(f"Rate limited. Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                    return self._request_with_retry(
                        method, url, headers, params, json_data, retry_count + 1
                    )
                raise RateLimitError(
                    "Rate limit exceeded.",
                    status_code=429,
                    response_body=response.text
                )
            elif 500 <= response.status_code < 600:
                if retry_count < self._max_retries:
                    wait_time = 2 ** retry_count
                    logger.warning(f"Server error {response.status_code}. Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                    return self._request_with_retry(
                        method, url, headers, params, json_data, retry_count + 1
                    )
                raise ServerError(
                    f"Server error: {response.status_code}",
                    status_code=response.status_code,
                    response_body=response.text
                )
            else:
                raise GoogleCloudAIError(
                    f"Request failed with status {response.status_code}",
                    status_code=response.status_code,
                    response_body=response.text
                )
                
        except httpx.RequestError as e:
            if retry_count < self._max_retries:
                wait_time = 2 ** retry_count
                logger.warning(f"Request error: {e}. Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
                return self._request_with_retry(
                    method, url, headers, params, json_data, retry_count + 1
                )
            raise GoogleCloudAIError(f"Request failed: {str(e)}")

    def get_application_integration_openapi_spec(self) -> dict[str, Any]:
        """View OpenAPI specification for Application Integration.
        
        Returns:
            dict: OpenAPI specification in HTML format
            
        Raises:
            AuthenticationError: If authentication fails
            ForbiddenError: If access is forbidden
            RateLimitError: If rate limit is exceeded
            ServerError: If server returns an error
        """
        url = f"{self._base_url}/application-integration/docs/view-openapi-spec"
        headers = self._auth.get_headers()
        headers["Accept"] = "text/html,application/json"
        
        return self._request_with_retry("GET", url, headers=headers)

    def get_cloud_endpoints_openapi_overview(self) -> dict[str, Any]:
        """Get overview of OpenAPI support in Cloud Endpoints.
        
        Returns:
            dict: OpenAPI overview documentation in HTML format
            
        Raises:
            AuthenticationError: If authentication fails
            ForbiddenError: If access is forbidden
            RateLimitError: If rate limit is exceeded
            ServerError: If server returns an error
        """
        url = f"{self._base_url}/endpoints/docs/openapi/openapi-overview"
        headers = self._auth.get_headers()
        headers["Accept"] = "text/html,application/json"
        
        return self._request_with_retry("GET", url, headers=headers)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a generic GET request to the API.
        
        Args:
            path: API endpoint path (e.g., '/application-integration/docs/view-openapi-spec')
            params: Optional query parameters
            
        Returns:
            dict: Parsed JSON response
            
        Raises:
            AuthenticationError: If authentication fails
            ForbiddenError: If access is forbidden
            RateLimitError: If rate limit is exceeded
            ServerError: If server returns an error
        """
        url = f"{self._base_url}{path}"
        headers = self._auth.get_headers()
        
        return self._request_with_retry("GET", url, headers=headers, params=params)

    def close(self) -> None:
        """Close the HTTP client and release resources."""
        self._client.close()

    def __enter__(self) -> "GoogleCloudAIClient":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()