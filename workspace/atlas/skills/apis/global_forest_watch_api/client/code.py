import httpx
import asyncio
import logging
import time
from typing import Dict, Any, Optional, Union, TypeVar, Callable

# Configure logging
logger = logging.getLogger("gfw_client")

T = TypeVar("T")

# --- Custom Exceptions ---

class GFWError(Exception):
    """Base exception for GFW API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Any] = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details

class GFWUnauthorizedError(GFWError):
    """401 Unauthorized - Missing or invalid API key."""
    pass

class GFWForbiddenError(GFWError):
    """403 Forbidden - Insufficient permissions for the dataset."""
    pass

class GFWNotFoundError(GFWError):
    """404 Not Found - The requested resource or dataset does not exist."""
    pass

class GFWRateLimitError(GFWError):
    """429 Too Many Requests - Rate limit exceeded."""
    pass

class GFWServerError(GFWError):
    """5xx Server Error."""
    pass

# --- Authentication ---

class AuthApiKeyHeader:
    """API Key authentication via request header."""

    def __init__(self, api_key: str, header_name: str = "x-api-key"):
        """
        Initialize the authentication handler.

        :param api_key: The API key for the Global Forest Watch API.
        :param header_name: The header name for the API key. Defaults to 'x-api-key'.
        """
        self._api_key = api_key
        self._header_name = header_name

    def get_headers(self) -> Dict[str, str]:
        """Return the headers required for authentication."""
        return {self._header_name: self._api_key}

# --- Rate Limiting ---

class RateLimiter:
    """Simple rate limiter to control request frequency."""
    
    def __init__(self, requests_per_minute: Optional[int] = None):
        self.requests_per_minute = requests_per_minute
        self.min_interval = (60.0 / requests_per_minute) if requests_per_minute else 0
        self._last_request_time = 0.0

    async def acquire(self)