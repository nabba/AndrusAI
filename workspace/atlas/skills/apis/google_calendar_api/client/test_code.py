import pytest
import httpx
import time
import threading
from unittest.mock import patch, MagicMock, AsyncMock
from typing import Any

from client import (
    GoogleCalendarClient,
    GoogleCalendarError,
    GoogleCalendarAuthError,
    GoogleCalendarRateLimitError,
    GoogleCalendarNotFoundError,
    GoogleCalendarBadRequestError,
    GoogleCalendarForbiddenError,
    RateLimiter,
)


class TestRateLimiter:
    def test_initial_tokens_equals_rate(self):
        limiter = RateLimiter(requests_per_second=10.0)
        assert limiter._tokens == 10.0

    def test_acquire_decrements_tokens(self):
        limiter = RateLimiter(requests_per_second=10.0)
        initial_tokens = limiter._tokens
        limiter.acquire()
        assert limiter._tokens < initial_tokens

    def test_acquire_blocks_when_tokens_exhausted(self):
        limiter = RateLimiter(requests_per_second=1.0)
        limiter._tokens = 0.0
        limiter._last_update = time.monotonic()
        
        start = time.monotonic()
        limiter.acquire()
        elapsed = time.monotonic() - start
        
        assert elapsed >= 0.9

    def test_tokens_replenish_over_time(self):
        limiter = RateLimiter(requests_per_second=10.0)
        limiter._tokens = 0.0
        old_last_update = limiter._last_update
        limiter._last_update = time.monotonic() - 1.0
        
        limiter.acquire()
        
        assert limiter._tokens > 0

    def test_tokens_capped_at_rate(self):
        limiter = RateLimiter(requests_per_second=10.0)
        limiter._tokens = 100.0
        limiter._last_update = time.monotonic()
        
        limiter.acquire()
        
        assert limiter._tokens <= 10.0


class TestGoogleCalendarClient:
    @pytest.fixture
    def client(self):
        return GoogleCalendarClient(
            client_id="test_client_id",
            client_secret="test_client_secret",
            token_url="https://accounts.google.com/o/oauth2/token",
            timeout=30.0,
            max_retries=3,
            requests_per_second=10.0,
        )

    def test_initialization(self, client):
        assert client._client_id == "test_client_id"
        assert client._client_secret == "test_client_secret"
        assert client._token_url == "https://accounts.google.com/o/oauth2/token"
        assert client._timeout == 30.0
        assert client._max_retries == 3
        assert isinstance(client._rate_limiter, RateLimiter)

    def test_default_scopes(self, client):
        assert "https://www.googleapis.com/auth/calendar" in client._scopes
        assert "https://www.googleapis.com/auth/calendar.events" in client._scopes

    def test_custom_scopes(self):
        client = GoogleCalendarClient(
            client_id="test_id",
            client_secret="test_secret",
            scopes=["https://example.com/custom_scope"],
        )
        assert client._scopes == ["https://example.com/custom_scope"]

    def test_get_token_returns_valid_token(self, client):
        client._access_token = "existing_token"
        client._expires_at = time.time() + 120
        
        token = client._get_token()
        
        assert token == "existing_token"

    def test_get_token_triggers_refresh_when_expired(self, client):
        client._access_token = "expired_token"
        client._expires_at = time.time() - 10
        
        with patch.object(client, '_refresh_token', return_value="new_token") as mock_refresh:
            token = client._get_token()
            
            assert token == "new_token"
            mock_refresh.assert_called_once()

    def test_get_token_triggers_refresh_when_expiring_soon(self, client):
        client._access_token = "old_token"
        client._expires_at = time.time() + 50
        
        with patch.object(client, '_refresh_token', return_value="new_token") as mock_refresh:
            token = client._get_token()
            
            mock_refresh.assert_called_once()

    @patch('client.httpx.Client')
    def test_refresh_token_success(self, mock_http_client, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "expires_in": 3600,
        }
        
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_http_client.return_value.__enter__.return_value = mock_client
        
        token = client._refresh_token()
        
        assert token == "new_access_token"
        assert client._access_token == "new_access_token"
        assert client._expires_at > time.time()


class TestGoogleCalendarClientAPI:
    @pytest.fixture
    def client(self):
        return GoogleCalendarClient(
            client_id="test_client_id",
            client_secret="test_client_secret",
        )

    @pytest.fixture
    def mock_token(self, client):
        client._access_token = "test_token"
        client._expires_at = time.time() + 3600
        return "test_token"

    @patch('client.httpx.Client')
    def test_list_events_success(self, mock_http_client, client, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "items": [{"id": "event1", "summary": "Test Event"}],
            "nextPageToken": None,
        }
        
        mock_client = MagicMock()
        mock_client.request.return_value = mock_response
        mock_http_client.return_value.__enter__.return_value = mock_client
        
        result = client.list_events("primary")
        
        assert result["items"][0]["id"] == "event1"
        mock_client.request.assert_called_once()

    @patch('client.httpx.Client')
    def test_list_events_with_params(self, mock_http_client, client, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": []}
        
        mock_client = MagicMock()
        mock_client.request.return_value = mock_response
        mock_http_client.return_value.__enter__.return_value = mock_client
        
        result = client.list_events(
            "primary",
            timeMin="2024-01-01T00:00:00Z",
            timeMax="2024-12-31T23:59:59Z",
            maxResults=10,
        )
        
        assert result["items"] == []

    @patch('client.httpx.Client')
    def test_create_event_success(self, mock_http_client, client, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "new_event_id",
            "status": "confirmed",
            "htmlLink": "https://calendar.google.com/event?eid=...",
        }
        
        mock_client = MagicMock()
        mock_client.request.return_value = mock_response
        mock_http_client.return_value.__enter__.return_value = mock_client
        
        event_data = {
            "summary": "Test Event",
            "description": "Test Description",
            "start": {"dateTime": "2024-06-01T10:00:00Z", "timeZone": "UTC"},
            "end": {"dateTime": "2024-06-01T11:00:00Z", "timeZone": "UTC"},
        }
        
        result = client.create_event("primary", event_data)
        
        assert result["id"] == "new_event_id"
        assert result["status"] == "confirmed"

    @patch('client.httpx.Client')
    def test_get_event_success(self, mock_http_client, client, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock