import httpx
import time
import asyncio
from typing import Any, Dict, List, Optional, Union


class FirecrawlError(Exception):
    """Base exception for Firecrawl client errors."""
    pass


class FirecrawlAuthError(FirecrawlError):
    """Exception raised for authentication errors (401, 403)."""
    pass


class FirecrawlRateLimitError(FirecrawlError):
    """Exception raised when rate limits are hit (429)."""
    pass


class FirecrawlAPIError(FirecrawlError):
    """Exception raised for general API errors (4xx, 5xx)."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"API Error {status_code}: {message}")


class AuthApiKeyHeader:
    """API Key authentication via request header."""

    def __init__(self, api_key: str, header_name: str = "Authorization", prefix: str = "Bearer"):
        self._api_key = api_key
        self._header_name = header_name
        self._prefix = prefix

    def get_headers(self) -> Dict[str, str]:
        if self._prefix:
            return {self._header_name: f"{self._prefix} {self._api_key}"}
        return {self._header_name: self._api_key}

    def apply(self, client: httpx.Client) -> httpx.Client:
        client.headers.update(self.get_headers())
        return client

    def apply_async(self, client: httpx.AsyncClient) -> httpx.AsyncClient:
        client.headers.update(self.get_headers())
        return client


class FirecrawlClient:
    """
    Synchronous client for the Firecrawl API.
    Provides methods to scrape, crawl, and map websites.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.firecrawl.dev/v1",
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5
    ):
        """
        Initialize the Firecrawl client.

        :param api_key: The API key for authentication.
        :param base_url: The base URL for the Firecrawl API.
        :param timeout: Request timeout in seconds.
        :param max_retries: Maximum number of retries for failed requests.
        :param backoff_factor: Backoff factor for exponential backoff.
        """
        self.base_url = base_url.rstrip('/')
        self.auth = AuthApiKeyHeader(api_key)
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Content-Type": "application/json"}
        )
        self.auth.apply(self.client)

    def _handle_response(self, response: httpx.Response) -> Dict[str, Any]:
        """Handle HTTP response and raise appropriate exceptions."""
        if response.status_code == 401 or response.status_code == 403:
            raise FirecrawlAuthError(f"Authentication failed: {response.text}")
        if response.status_code == 429:
            raise FirecrawlRateLimitError(f"Rate limit exceeded: {response.text}")
        if 400 <= response.status_code < 500:
            raise FirecrawlAPIError(response.status_code, response.text)
        if response.status_code >= 500:
            raise FirecrawlAPIError(response.status_code, f"Server error: {response.text}")

        return response.json()

    def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        """Make a synchronous HTTP request with retry logic."""
        retries = 0
        last_exception = None

        while retries <= self.max_retries:
            try:
                response = self.client.request(method, path, **kwargs)

                # Handle Rate Limiting specifically for retries
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", self.backoff_factor * (2 ** retries)))
                    if retries < self.max_retries:
                        time.sleep(retry_after)
                        retries += 1
                        continue
                    else:
                        raise FirecrawlRateLimitError(f"Rate limit exceeded after retries: {response.text}")

                # Handle Server Errors (5xx) for retries
                if response.status_code >= 500:
                    if retries < self.max_retries:
                        sleep_time = self.backoff_factor * (2 ** retries)
                        time.sleep(sleep_time)
                        retries += 1
                        continue

                return self._handle_response(response)

            except httpx.RequestError as e:
                last_exception = e
                if retries < self.max_retries:
                    sleep_time = self.backoff_factor * (2 ** retries)
                    time.sleep(sleep_time)
                    retries += 1
                else:
                    raise FirecrawlAPIError(0, f"Network error: {str(e)}")

        raise FirecrawlAPIError(0, f"Max retries exceeded: {str(last_exception)}")

    def scrape(
        self,
        url: str,
        formats: Optional[List[str]] = None,
        only_main_content: bool = False
    ) -> Dict[str, Any]:
        """
        Scrape a specific URL and return its content.

        :param url: The URL to scrape.
        :param formats: List of output formats (e.g., ['markdown', 'html', 'rawHtml']).
        :param only_main_content: If True, extract only main content.
        :return: Dictionary containing scraped data.
        """
        payload = {
            "url": url,
            "formats": formats,
            "onlyMainContent": only_main_content
        }
        # Filter None values
        payload = {k: v for k, v in payload.items() if v is not None}
        return self._request("POST", "/scrape", json=payload)

    def crawl(
        self,
        url: str,
        limit: Optional[int] = None,
        max_depth: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Initiate a crawl job starting from a URL.

        :param url: The base URL to start crawling from.
        :param limit: Maximum number of pages to crawl.
        :param max_depth: Maximum depth to crawl relative to the URL.
        :return: Dictionary containing job ID and status URL.
        """
        payload = {
            "url": url,
            "limit": limit,
            "maxDepth": max_depth
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return self._request("POST", "/crawl", json=payload)

    def check_crawl_status(self, job_id: str) -> Dict[str, Any]:
        """
        Check the status of a running crawl job.

        :param job_id: The ID of the crawl job.
        :return: Dictionary containing status and results (if completed).
        """
        path = f"/crawl/{job_id}"
        return self._request("GET", path)

    def map(
        self,
        url: str
    ) -> Dict[str, Any]:
        """
        Map a website to retrieve a list of links.

        :param url: The URL to map.
        :return: Dictionary containing list of links.
        """
        payload = {"url": url}
        return self._request("POST", "/map", json=payload)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class AsyncFirecrawlClient:
    """
    Asynchronous client for the Firecrawl API.
    Provides methods to scrape, crawl, and map websites.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str