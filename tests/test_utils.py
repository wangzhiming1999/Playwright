"""
Tests for utils.py
- validate_url
- llm_call retry logic
"""

from unittest.mock import MagicMock, patch

import pytest
import httpx

from utils import validate_url, llm_call


def _make_status_error(cls, status_code=429, message="test error"):
    """Create an OpenAI APIStatusError subclass with correct constructor."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.headers = httpx.Headers()
    mock_resp.json.return_value = {"error": {"message": message, "type": "test"}}
    return cls(message=message, response=mock_resp, body={"error": {"message": message}})


def _make_connection_error(message="connection failed"):
    from openai import APIConnectionError
    mock_req = MagicMock(spec=httpx.Request)
    return APIConnectionError(message=message, request=mock_req)


# ── validate_url ──────────────────────────────────────────────────────────────

class TestValidateUrl:
    def test_valid_https(self):
        ok, err = validate_url("https://example.com")
        assert ok is True and err == ""

    def test_valid_http(self):
        ok, _ = validate_url("http://example.com/path?q=1")
        assert ok is True

    def test_empty_string(self):
        ok, err = validate_url("")
        assert ok is False and "空" in err

    def test_whitespace_only(self):
        ok, _ = validate_url("   ")
        assert ok is False

    def test_ftp_rejected(self):
        ok, err = validate_url("ftp://example.com")
        assert ok is False and "ftp" in err

    def test_file_rejected(self):
        ok, _ = validate_url("file:///etc/passwd")
        assert ok is False

    def test_no_scheme_rejected(self):
        ok, _ = validate_url("example.com")
        assert ok is False

    def test_localhost_rejected(self):
        ok, err = validate_url("http://localhost:8080")
        assert ok is False and "localhost" in err

    def test_127_rejected(self):
        ok, _ = validate_url("http://127.0.0.1:3000")
        assert ok is False

    def test_private_10_rejected(self):
        ok, _ = validate_url("http://10.0.0.1/admin")
        assert ok is False

    def test_private_192_rejected(self):
        ok, _ = validate_url("http://192.168.1.1")
        assert ok is False

    def test_private_172_rejected(self):
        ok, _ = validate_url("http://172.16.0.1")
        assert ok is False

    def test_local_domain_rejected(self):
        ok, _ = validate_url("http://myapp.local")
        assert ok is False

    def test_no_netloc_rejected(self):
        ok, _ = validate_url("https://")
        assert ok is False

    def test_valid_subdomain(self):
        ok, _ = validate_url("https://app.example.com/dashboard")
        assert ok is True

    def test_valid_with_port(self):
        ok, _ = validate_url("https://example.com:8443/api")
        assert ok is True

    def test_valid_with_path_and_query(self):
        ok, _ = validate_url("https://example.com/search?q=test&lang=zh")
        assert ok is True


# ── llm_call retry logic ──────────────────────────────────────────────────────

class TestLlmCall:
    def test_success_on_first_try(self):
        fn = MagicMock(return_value="result")
        assert llm_call(fn, "arg1", key="val") == "result"
        fn.assert_called_once_with("arg1", key="val")

    def test_retries_on_rate_limit(self):
        from openai import RateLimitError
        err = _make_status_error(RateLimitError, 429, "rate limit")
        fn = MagicMock(side_effect=[err, err, "success"])
        with patch("utils.time.sleep"):
            result = llm_call(fn, max_retries=3, base_delay=0.01)
        assert result == "success"
        assert fn.call_count == 3

    def test_retries_on_connection_error(self):
        err = _make_connection_error()
        fn = MagicMock(side_effect=[err, "ok"])
        with patch("utils.time.sleep"):
            assert llm_call(fn, max_retries=3, base_delay=0.01) == "ok"

    def test_raises_after_max_retries(self):
        err = _make_connection_error()
        fn = MagicMock(side_effect=err)
        with patch("utils.time.sleep"):
            with pytest.raises(RuntimeError, match="failed after"):
                llm_call(fn, max_retries=3, base_delay=0.01)
        assert fn.call_count == 3

    def test_no_retry_on_auth_error(self):
        from openai import AuthenticationError
        err = _make_status_error(AuthenticationError, 401, "invalid key")
        fn = MagicMock(side_effect=err)
        with pytest.raises(type(err)):
            llm_call(fn, max_retries=3, base_delay=0.01)
        fn.assert_called_once()

    def test_no_retry_on_bad_request(self):
        from openai import BadRequestError
        err = _make_status_error(BadRequestError, 400, "bad request")
        fn = MagicMock(side_effect=err)
        with pytest.raises(type(err)):
            llm_call(fn, max_retries=3, base_delay=0.01)
        fn.assert_called_once()

    def test_exponential_backoff_delays(self):
        err = _make_connection_error()
        fn = MagicMock(side_effect=[err, err, "ok"])
        sleep_calls = []
        with patch("utils.time.sleep", side_effect=lambda d: sleep_calls.append(d)):
            llm_call(fn, max_retries=3, base_delay=1.0)
        assert sleep_calls == [1.0, 2.0]

    def test_passes_args_and_kwargs(self):
        fn = MagicMock(return_value="ok")
        llm_call(fn, "a", "b", x=1, y=2)
        fn.assert_called_once_with("a", "b", x=1, y=2)

    def test_unknown_error_retries_once(self):
        fn = MagicMock(side_effect=[ValueError("unexpected"), "recovered"])
        with patch("utils.time.sleep"):
            assert llm_call(fn, max_retries=3, base_delay=0.01) == "recovered"

    def test_unknown_error_raises_after_second_attempt(self):
        fn = MagicMock(side_effect=ValueError("persistent"))
        with patch("utils.time.sleep"):
            with pytest.raises(ValueError):
                llm_call(fn, max_retries=3, base_delay=0.01)
