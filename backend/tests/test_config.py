import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["*"]', ["*"]),
        (
            '["https://a.vercel.app", "http://localhost:3000"]',
            ["https://a.vercel.app", "http://localhost:3000"],
        ),
        ("https://a.vercel.app", ["https://a.vercel.app"]),
        (
            "https://a.vercel.app, https://b.vercel.app",
            ["https://a.vercel.app", "https://b.vercel.app"],
        ),
        ("", ["http://localhost:3000"]),  # blank in a dashboard: default, not a crash
    ],
)
def test_cors_origins_accepts_common_formats(monkeypatch, raw, expected):
    """Regression: an empty CORS_ORIGINS crashed the Render deploy at startup."""
    monkeypatch.setenv("CORS_ORIGINS", raw)
    assert Settings().cors_origins == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("rediss://default:pw@h.upstash.io:6379", "rediss://default:pw@h.upstash.io:6379"),
        (
            "redis-cli --tls -u redis://default:pw@h.upstash.io:6379",
            "rediss://default:pw@h.upstash.io:6379",
        ),  # as Upstash shows it
        ('"redis://localhost:6379/0"', "redis://localhost:6379/0"),
    ],
)
def test_redis_url_accepts_url_or_pasted_cli_command(monkeypatch, raw, expected):
    monkeypatch.setenv("REDIS_URL", raw)
    assert Settings().redis_url == expected


@pytest.mark.parametrize("raw", ["https://secret-host.upstash.io", "AXyzSECRETTOKEN"])
def test_bad_redis_url_named_but_never_echoed(monkeypatch, raw):
    """Regression: the REST URL crashed the Render worker with 'invalid DSN scheme'. The
    error must name REDIS_URL without printing the value (it can hold a password)."""
    monkeypatch.setenv("REDIS_URL", raw)
    with pytest.raises(ValueError, match="REDIS_URL") as e:
        Settings()
    assert raw not in str(e.value)
