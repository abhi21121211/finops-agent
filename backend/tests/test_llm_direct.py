"""Direct mode failover, with fake provider clients."""

from types import SimpleNamespace

import httpx
import openai
import pytest

from app.llm.direct import Deployment, DirectClient, NoDeploymentError

A = Deployment("gemini", "flash-lite", "https://a", "KEY_A")
B = Deployment("mistral", "medium", "https://b", "KEY_B")
C = Deployment("openrouter", "gemma", "https://c", "KEY_C")


def _status_error(code: int) -> openai.APIStatusError:
    resp = httpx.Response(code, request=httpx.Request("POST", "https://x"))
    return openai.APIStatusError(f"error {code}", response=resp, body=None)


class Fake:
    def __init__(self, outcome) -> None:
        self.outcome, self.calls = outcome, []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=self._create))
        )

    async def _create(self, **kw):
        self.calls.append(kw)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return SimpleNamespace(parse=lambda: self.outcome)


def make(outcomes: dict, env=None):
    fakes = {d: Fake(o) for d, o in outcomes.items()}
    env = env or {"KEY_A": "a", "KEY_B": "b", "KEY_C": "c"}
    client = DirectClient(
        5, tiers={"vision": tuple(outcomes)}, env=env, client_factory=lambda d: fakes[d]
    )
    return client, fakes


async def call(client):
    return await client.chat.completions.with_raw_response.create(model="vision", messages=[])


async def test_first_healthy_deployment_answers_and_is_reported():
    client, fakes = make({A: "ok-a", B: "ok-b"})
    raw = await call(client)
    assert raw.parse() == "ok-a" and raw.headers["x-litellm-model-name"] == "gemini/flash-lite"
    assert fakes[A].calls[0]["model"] == "flash-lite" and not fakes[B].calls


async def test_fails_over_on_rate_limit_and_cools_down():
    client, fakes = make({A: _status_error(429), B: "ok-b"})
    assert (await call(client)).headers["x-litellm-model-name"] == "mistral/medium"
    await call(client)
    # A is cooling down, so the second call goes straight to B.
    assert len(fakes[A].calls) == 1 and len(fakes[B].calls) == 2


async def test_quota_and_bad_key_fail_over_too():
    client, _ = make({A: _status_error(403), B: _status_error(401), C: "ok-c"})
    assert (await call(client)).parse() == "ok-c"


async def test_all_failing_raises_last_error_for_worker_retry():
    client, _ = make({A: _status_error(503), B: _status_error(429)})
    with pytest.raises(openai.APIStatusError) as e:
        await call(client)
    assert e.value.status_code == 429


async def test_deployments_without_keys_are_skipped():
    client, fakes = make({A: "ok-a", B: "ok-b"}, env={"KEY_B": "b"})
    assert client.configured("vision") == ["mistral/medium"]
    assert (await call(client)).parse() == "ok-b"
    with pytest.raises(NoDeploymentError):
        await client.chat.completions.with_raw_response.create(model="normal", messages=[])
