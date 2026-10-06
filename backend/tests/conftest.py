"""Test setup: a separate `finops_test` database, in-memory storage, a fake queue and a
fake LLM. No test makes a network call to an LLM provider."""

import os

# Must run before app modules create the engine.
_BASE = os.environ.get("DATABASE_URL", "postgresql+asyncpg://finops:finops@localhost:5433/finops")
os.environ["DATABASE_URL"] = _BASE.rsplit("/", 1)[0] + "/finops_test"

import io  # noqa: E402
import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.core.auth import create_token  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.session import engine  # noqa: E402


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def ensure_bucket(self) -> None:
        pass

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        return self.objects[key][0]

    def presigned_url(self, key: str, expires_s: int = 900) -> str:
        return f"http://storage.test/{key}"


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue_invoice(self, invoice_id: str) -> None:
        self.enqueued.append(invoice_id)


class FakeRaw:
    """Mimics openai's with_raw_response wrapper."""

    def __init__(self, content: str, model: str) -> None:
        self.headers = {"x-litellm-model-name": model, "x-litellm-response-cost": "0.001"}
        self._resp = SimpleNamespace(
            model="vision",
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200),
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        )

    def parse(self):
        return self._resp


class FakeOpenAI:
    """Returns the queued replies in order and records every request."""

    def __init__(self, *replies: str, model: str = "fake/vision-model") -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.model = model
        create = self._create
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
        )

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        return FakeRaw(self.replies.pop(0), self.model)


def sample_extraction(**overrides) -> dict:
    invoice = {
        "vendor_name": "Test Vendor LLP",
        "vendor_gstin": "27ABCCD1234E1Z5",
        "buyer_gstin": None,
        "invoice_number": "TV/26-27/001",
        "invoice_date": "2026-09-15",
        "due_date": "2026-10-15",
        "po_number": "PO-1",
        "currency": "INR",
        "line_items": [
            {
                "description": "Widget",
                "hsn_sac": "8471",
                "quantity": 2,
                "unit_price": 500,
                "tax_rate": 18,
                "amount": 1000,
            }
        ],
        "subtotal": 1000,
        "cgst": 90,
        "sgst": 90,
        "igst": 0,
        "total": 1180,
        "bank_account_last4": "1234",
        "bank_ifsc": "HDFC0001234",
    }
    invoice.update(overrides)
    return {"invoice": invoice, "confidence": {"vendor_name": 0.99, "total": 0.97}}


def make_pdf(text: str = "TAX INVOICE") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, text)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture(scope="session", autouse=True)
async def _database():
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    admin = await asyncpg.connect(dsn.rsplit("/", 1)[0] + "/postgres")
    if not await admin.fetchval("select 1 from pg_database where datname = 'finops_test'"):
        await admin.execute("create database finops_test")
    await admin.close()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    from app.seed import seed

    await seed()
    yield
    await engine.dispose()


@pytest.fixture
def storage(monkeypatch) -> MemoryStorage:
    mem = MemoryStorage()
    for module in ("app.agents.nodes.intake", "app.agents.nodes.extract", "app.main"):
        monkeypatch.setattr(f"{module}.get_storage", lambda: mem)
    return mem


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
async def client(storage, queue):
    from app.api.invoices import get_queue
    from app.main import app
    from app.storage import get_storage

    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_queue] = lambda: queue
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    from app.seed import DEMO_EMAIL, DEMO_TENANT_ID, DEMO_USER_ID

    token = create_token(DEMO_USER_ID, DEMO_TENANT_ID, DEMO_EMAIL, "admin")
    return {"Authorization": f"Bearer {token}"}


def as_json(d: dict) -> str:
    return json.dumps(d)
