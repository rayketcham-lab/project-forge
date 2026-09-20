"""Tests that structlog redaction masks sensitive values in log output."""

import io
import logging

import pytest

from project_forge.logging_config import configure_logging


@pytest.fixture(autouse=True)
def _reset_structlog(monkeypatch):
    """Force structlog to reconfigure on every test.

    structlog caches the global configuration on first use, so tests that call
    configure_logging() without resetting the cache see stale output.  This
    fixture monkey-patches the cache dict between tests so that reconfigure()
    always takes effect.
    """
    import structlog

    monkeypatch.setattr(structlog._config._global_cache, "_c", {})


class TestApiKeyRedaction:
    """Sensitive env vars (ANTHROPIC_API_KEY, API tokens) must NOT appear in
    logged output, regardless of value length or prefix."""

    def test_real_anthropic_api_key_not_in_log(self, caplog):
        """A 100+ char ANTHROPIC_API_KEY value must not leak into log output."""
        key = (
            "sk-ant-api03-0123456789012345678901234567890123456789012345"
            "678901234567890123456789012345678901234567890123456789012"
            "345678901234567890123456789012345678901234567890"
        )

        stream = io.StringIO()
        configure_logging(stream=stream, level=logging.DEBUG)

        log = __import__("structlog").get_logger("test_redaction")
        log.info("loading config", api_key=key)

        output = stream.getvalue()

        assert key not in output, (
            f"API key '{key}' must not appear in log output"
        )
        # The key label itself should still be visible for debugging
        assert "api_key" in output

    def test_short_api_token_not_in_log(self, caplog):
        """A short mock token must not appear in log output either."""
        token = "tk_test_abc123"

        stream = io.StringIO()
        configure_logging(stream=stream, level=logging.DEBUG)

        log = __import__("structlog").get_logger("test_redaction")
        log.info("auth check", token=token)

        output = stream.getvalue()

        assert token not in output, (
            f"Short token '{token}' must not appear in log output"
        )
        assert "token" in output

    def test_settings_api_key_masked(self, caplog):
        """settings.anthropic_api_key value must not leak."""
        settings_key = "ak_live_0a1b2c3d4e5f6g7h"

        stream = io.StringIO()
        configure_logging(stream=stream, level=logging.DEBUG)

        log = __import__("structlog").get_logger("test_redaction")
        log.info("reading settings", settings_anthropic_api_key=settings_key)

        output = stream.getvalue()

        assert settings_key not in output, (
            f"settings.anthropic_api_key '{settings_key}' must not appear"
        )


class TestNonSensitiveRedaction:
    """Non-sensitive information must still be present in log output — redaction
    must not be over-aggressive."""

    def test_timestamp_present(self, caplog):
        """Timestamps should appear in the structured log."""
        stream = io.StringIO()
        configure_logging(stream=stream, level=logging.DEBUG)

        log = __import__("structlog").get_logger("test_redaction")
        log.info("startup")

        output = stream.getvalue()

        assert "ts" in output or "time" in output, (
            "Timestamp field must be present in log output"
        )

    def test_error_message_present(self, caplog):
        """Error messages and event names must not be redacted."""
        stream = io.StringIO()
        configure_logging(stream=stream, level=logging.DEBUG)

        log = __import__("structlog").get_logger("test_redaction")
        log.error("connection refused to database", code=500)

        output = stream.getvalue()

        assert "connection refused to database" in output, (
            "Error message must appear in log output"
        )
        assert "connection" in output
        assert "refused" in output

    def test_module_name_present(self, caplog):
        """Logger name (module) should still be visible."""
        stream = io.StringIO()
        configure_logging(stream=stream, level=logging.DEBUG)

        log = __import__("structlog").get_logger("test_redaction")
        log.info("health_check", status="ok", module="project_forge.storage.db")

        output = stream.getvalue()

        assert "health_check" in output, "Event name must be present"
        assert "status" in output, "Field names must be present"
        assert '"ok"' in output or "'ok'" in output, "Non-sensitive value must be present"

    def test_log_level_present(self, caplog):
        """Log level (info, error, warning) must still appear."""
        stream = io.StringIO()
        configure_logging(stream=stream, level=logging.DEBUG)

        log = __import__("structlog").get_logger("test_redaction")
        log.warning("deprecated config", field="value")

        output = stream.getvalue()

        assert "warning" in output.lower(), "Log level must appear in output"
        assert "deprecated config" in output


class TestSecretInExceptionMessages:
    """Sensitive values must not appear when logged via exception handling."""

    def test_exception_message_does_not_leak_secret(self, caplog):
        """Exception messages containing API keys must not leak the key."""
        secret = "sk-ant-secret-12345abcde"

        stream = io.StringIO()
        configure_logging(stream=stream, level=logging.DEBUG)

        log = __import__("structlog").get_logger("test_redaction")

        try:
            raise ValueError(f"Failed to authenticate with key {secret}")
        except ValueError as exc:
            log.error("auth failure", exc_info=exc)

        output = stream.getvalue()

        assert secret not in output, (
            f"Secret '{secret}' must not appear in exception log output"
        )
        # The error context should still be visible
        assert "auth failure" in output

    def test_exception_message_short_secret_not_leaked(self, caplog):
        """Even short secrets in exception messages must not leak."""
        short_secret = "abc"

        stream = io.StringIO()
        configure_logging(stream=stream, level=logging.DEBUG)

        log = __import__("structlog").get_logger("test_redaction")

        try:
            raise RuntimeError("invalid token: abc")
        except RuntimeError as exc:
            log.error("token validation failed", exc_info=exc)

        output = stream.getvalue()

        assert short_secret not in output, (
            f"Short secret '{short_secret}' must not appear in exception log output"
        )


class TestAuthMiddlewareSecrets:
    """Verify that auth middleware does not log sensitive values."""

    def test_bearer_token_not_logged(self, caplog):
        """Bearer token value from Authorization header must not appear in logs."""
        import logging

        with caplog.at_level(logging.WARNING, logger="project_forge.web.auth"):
            from fastapi import FastAPI
            from fastapi.testclient import TestClient as FastAPIClient

            from project_forge.web.auth import BearerTokenMiddleware

            app = FastAPI()

            @app.get("/health")
            def health():
                return {"ok": True}

            app.add_middleware(BearerTokenMiddleware)

            client = FastAPIClient(app)
            # Send a POST (write method) with a known bad token — triggers 401
            resp = client.post(
                "/health",
                headers={"Authorization": "Bearer super-secret-wrong-token"},
            )

        assert resp.status_code == 401
        assert "super-secret-wrong-token" not in caplog.text, (
            "Bearer token value must not appear in auth middleware logs"
        )

    def test_unauthorized_log_has_no_token(self, caplog):
        """The 401 response path must not log the attempted token."""
        import logging

        with caplog.at_level(logging.INFO, logger="project_forge.web.auth"):
            from fastapi import FastAPI

            from project_forge.web.auth import BearerTokenMiddleware

            app = FastAPI()

            @app.get("/health")
            def health():
                return {"ok": True}

            app.add_middleware(BearerTokenMiddleware)

            from fastapi.testclient import TestClient as FastAPIClient
            client = FastAPIClient(app)
            resp = client.post(
                "/health",
                headers={"Authorization": "Bearer should-not-be-in-logs"},
            )

        assert resp.status_code == 401
        assert "should-not-be-in-logs" not in caplog.text, (
            "Attempted token value must not appear in auth middleware logs"
        )


class TestOverRedaction:
    """Ensure that legitimate sensitive-but-needed fields are still visible."""

    def test_non_secret_values_passed_through(self, caplog):
        """Values that are not on a known secret pattern should pass through."""
        stream = io.StringIO()
        configure_logging(stream=stream, level=logging.DEBUG)

        log = __import__("structlog").get_logger("test_redaction")
        log.info(
            "user login",
            username="alice@example.com",
            ip="192.168.1.1",
            method="password",
        )

        output = stream.getvalue()

        assert "alice@example.com" in output, "Emails should not be redacted"
        assert "192.168.1.1" in output, "IP addresses should not be redacted"
        assert "password" in output, "Generic field values should not be redacted"

    def test_config_labels_visible_for_debugging(self, caplog):
        """Log labels and keys that identify the context must remain."""
        stream = io.StringIO()
        configure_logging(stream=stream, level=logging.DEBUG)

        log = __import__("structlog").get_logger("test_redaction")
        log.debug(
            "config loaded",
            db_path="data/forge.db",
            log_level="INFO",
            port=55443,
        )

        output = stream.getvalue()

        assert "config loaded" in output
        assert "db_path" in output
        assert "data/forge.db" in output
