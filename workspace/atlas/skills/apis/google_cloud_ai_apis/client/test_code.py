import pytest
import httpx
import time
import respx
import threading
from unittest.mock import Mock, patch, MagicMock
from httpx import Response

import sys
from types import ModuleType

# Create mock for httpx to avoid real imports
httpx_mock = ModuleType('httpx_mock')
httpx_mock.post = Mock()
httpx_mock.HTTPStatusError = httpx.HTTPStatusError
sys.modules['httpx'] = httpx_mock

from client import (
    GoogleCloudAIError,
    AuthenticationError,
    ForbiddenError,
    RateLimitError,
    ServerError,
    AuthOAuth2ClientCredentials,
    RateLimiter,
)


class TestExceptions:
    def test_google_cloud_ai_error_basic(self):
        err = GoogleCloudAIError("Test error")
        assert str(err) == "Test error"
        assert err.status_code is None
        assert err.response_body is None

    def test_google_cloud_ai_error_with_status(self):
        err = GoogleCloudAIError("Error occurred", status_code=500, response_body="Server error")
        assert err.status_code == 500
        assert err.response_body == "Server error"

    def test_authentication_error(self):
        err = AuthenticationError("Auth failed", status_code=401)
        assert err.status_code == 401
        assert isinstance(err, GoogleCloudAIError)

    def test_forbidden_error(self):
        err = ForbiddenError("Access denied", status_code=403)
        assert err.status_code == 403
        assert isinstance(err, GoogleCloudAIError)

    def test_rate_limit_error(self):
        err = RateLimitError("Rate exceeded", status_code=429)
        assert err.status_code == 429
        assert isinstance(err, GoogleCloudAIError)

    def test_server_error(self):
        err = ServerError("Server error", status_code=500)
        assert err.status_code == 500
        assert isinstance(err, GoogleCloudAIError)


class TestAuthOAuth2ClientCredentials:
    @respx.mock
    def test_get_token_success(self):
        token_url = "https://oauth.example.com/token"
        mock_response = {
            "access_token": "test_token_123",
            "expires_in": 3600
        }
        
        route = respx.post(token_url).mock(return_value=Response(200, json=mock_response))
        
        client = AuthOAuth2ClientCredentials(
            client_id="test_client",
            client_secret="test_secret",
            token_url=token_url,
            scopes=["https://example.com/scope"]
        )
        
        token = client.get_token()
        
        assert token == "test_token_123"
        assert route.called
        assert route.call_count == 1

    @respx.mock
    def test_get_token_cached(self):
        token_url = "https://oauth.example.com/token"
        mock_response = {
            "access_token": "test_token_123",
            "expires_in": 3600
        }
        
        route = respx.post(token_url).mock(return_value=Response(200, json=mock_response))
        
        client = AuthOAuth2ClientCredentials(
            client_id="test_client",
            client_secret="test_secret",
            token_url=token_url
        )
        
        token1 = client.get_token()
        token2 = client.get_token()
        
        assert token1 == token2 == "test_token_123"
        assert route.call_count == 1

    @respx.mock
    def test_get_token_refresh_on_expiry(self):
        token_url = "https://oauth.example.com/token"
        
        client = AuthOAuth2ClientCredentials(
            client_id="test_client",
            client_secret="test_secret",
            token_url=token_url
        )
        
        with patch.object(time, 'time') as mock_time:
            mock_time.return_value = 1000
            
            mock_response = {
                "access_token": "token_v1",
                "expires_in": 3600
            }
            route = respx.post(token_url).mock(return_value=Response(200, json=mock_response))
            
            token1 = client.get_token()
            assert token1 == "token_v1"
            assert route.call_count == 1
            
            mock_time.return_value = 1000 + 3500
            
            mock_response2 = {
                "access_token": "token_v2",
                "expires_in": 3600
            }
            route = respx.post(token_url).mock(return_value=Response(200, json=mock_response2))
            
            token2 = client.get_token()
            assert token2 == "token_v2"
            assert route.call_count == 2

    @respx.mock
    def test_get_headers(self):
        token_url = "https://oauth.example.com/token"
        mock_response = {
            "access_token": "test_token",
            "expires_in": 3600
        }
        
        respx.post(token_url).mock(return_value=Response(200, json=mock_response))
        
        client = AuthOAuth2ClientCredentials(
            client_id="test_client",
            client_secret="test_secret",
            token_url=token_url
        )
        
        headers = client.get_headers()
        
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test_token"

    @respx.mock
    def test_refresh_token_request_payload(self):
        token_url = "https://oauth.example.com/token"
        
        route = respx.post(token_url).mock(return_value=Response(200, json={
            "access_token": "test",
            "expires_in": 3600
        }))
        
        client = AuthOAuth2ClientCredentials(
            client_id="my_client_id",
            client_secret="my_secret",
            token_url=token_url,
            scopes=["scope1", "scope2"],
            audience="audience_value"
        )
        
        client.get_token()
        
        assert route.called
        call_kwargs = route.calls[0].kwargs
        assert call_kwargs["data"]["grant_type"] == "client_credentials"
        assert call_kwargs["data"]["client_id"] == "my_client_id"
        assert call_kwargs["data"]["client_secret"] == "my_secret"
        assert call_kwargs["data"]["scope"] == "scope1 scope2"
        assert call_kwargs["data"]["audience"] == "audience_value"

    @respx.mock
    def test_refresh_token_raises_on_error(self):
        token_url = "https://oauth.example.com/token"
        
        respx.post(token_url).mock(return_value=Response(400, json={"error": "invalid_client"}))
        
        client = AuthOAuth2ClientCredentials(
            client_id="test_client",
            client_secret="test_secret",
            token_url=token_url
        )
        
        with pytest.raises(httpx.HTTPStatusError):
            client.get_token()

    def test_token_refresh_thread_safety(self):
        token_url = "https://oauth.example.com/token"
        
        client = AuthOAuth2ClientCredentials(
            client_id="test_client",
            client_secret="test_secret",
            token_url=token_url
        )
        
        results = []
        errors = []
        
        def get_token_task():
            try:
                with respx.mock:
                    respx.post(token_url).mock(return_value=Response(200, json={