from decimal import Decimal

import pytest

from app.llm.pricing import list_price_usd
from app.llm.router import LLMOutputError, LLMRouter, Tier, text_part
from app.schemas.extraction import ExtractionOutput
from tests.conftest import FakeOpenAI, as_json, sample_extraction


async def _call(fake: FakeOpenAI):
    return await LLMRouter(client=fake).structured(
        tier=Tier.vision, system="sys", user_content=[text_part("x")], schema=ExtractionOutput
    )


async def test_valid_reply_parsed_and_recorded():
    fake = FakeOpenAI(as_json(sample_extraction()))
    result = await _call(fake)

    assert result.output.invoice.total == Decimal("1180")
    assert result.model == "fake/vision-model"  # from x-litellm-model-name, not the tier
    assert result.cost_usd == 0
    assert result.list_price_usd == Decimal("0.001")
    assert fake.requests[0]["model"] == "vision"
    assert fake.requests[0]["response_format"] == {"type": "json_object"}


async def test_fenced_json_is_accepted():
    fake = FakeOpenAI("Here you go:\n```json\n" + as_json(sample_extraction()) + "\n```")
    result = await _call(fake)
    assert result.output.invoice.invoice_number == "TV/26-27/001"


async def test_invalid_reply_gets_one_correction_round():
    bad = sample_extraction()
    del bad["invoice"]["total"]
    fake = FakeOpenAI(as_json(bad), as_json(sample_extraction()))

    result = await _call(fake)

    assert len(result.calls) == 2
    retry_msgs = fake.requests[1]["messages"]
    assert retry_msgs[-1]["role"] == "user"
    assert "did not match" in retry_msgs[-1]["content"]


async def test_gives_up_after_max_attempts_and_keeps_call_records():
    fake = FakeOpenAI("not json", "still not json")
    with pytest.raises(LLMOutputError) as exc:
        await _call(fake)
    assert len(exc.value.calls) == 2


def test_list_price_lookup():
    assert list_price_usd("gemini/gemini-flash-latest", 1_000_000, 0) == Decimal("0.30")
    assert list_price_usd("unknown/model", 1000, 1000) == 0
