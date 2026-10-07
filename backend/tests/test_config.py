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
