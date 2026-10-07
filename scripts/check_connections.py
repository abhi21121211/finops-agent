"""Check every cloud connection the deployed app needs, one by one, with fix hints.

Never prints secret values: only hosts, names and results.

    cd backend && uv run python ../scripts/check_connections.py --env ../.env.cloud
"""

import argparse
import asyncio
import json
import os
import ssl
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
GREEN, RED, YELLOW, DIM, END = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
results: list[tuple[str, bool]] = []


def ok(name: str, detail: str = "") -> None:
    results.append((name, True))
    print(f"  {GREEN}✓{END} {name}" + (f"  {DIM}{detail}{END}" if detail else ""))


def fail(name: str, detail: str, hint: str = "") -> None:
    results.append((name, False))
    print(f"  {RED}✗ {name}{END}  {detail}")
    if hint:
        print(f"    {YELLOW}→ {hint}{END}")


def warn(text: str) -> None:
    print(f"  {YELLOW}! {text}{END}")


def short(e: Exception) -> str:
    return f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}"


# ───────────────────────────── 1. configuration ─────────────────────────────


def check_config(env: dict) -> bool:
    print("\n1. Configuration")
    good = True
    db = env.get("DATABASE_URL", "")
    if not db or "PASSWORD@" in db or "POOLER-HOST" in db:
        fail(
            "DATABASE_URL", "not filled in", "Copy the Session pooler URI from Supabase → Connect."
        )
        good = False
    else:
        u = urlsplit(db)
        problems = []
        if u.scheme != "postgresql+asyncpg":
            problems.append("must start with postgresql+asyncpg://")
        if u.hostname and u.hostname.startswith("db.") and u.hostname.endswith(".supabase.co"):
            problems.append(
                "direct host db.<ref>.supabase.co is IPv6-only; use the Session pooler host"
            )
        if u.username and "." not in u.username and "pooler" in (u.hostname or ""):
            problems.append("pooler user must be postgres.<project-ref>")
        if "ssl=require" not in (u.query or ""):
            problems.append("add ?ssl=require at the end")
        if u.port == 6543:
            problems.append("port 6543 is the Transaction pooler; use the Session pooler (5432)")
        if problems:
            fail("DATABASE_URL format", "; ".join(problems))
            good = False
        else:
            ok("DATABASE_URL format", f"user {u.username} @ {u.hostname}:{u.port}")

    redis = env.get("REDIS_URL", "")
    if not redis:
        fail("REDIS_URL", "not filled in", "Upstash → Connect → the rediss:// (TCP) URL.")
        good = False
    elif redis.startswith("https://"):
        fail("REDIS_URL", "this is the REST URL", "Use the TCP URL starting with rediss://")
        good = False
    elif not redis.startswith("rediss://"):
        fail("REDIS_URL", "must start with rediss:// (TLS)")
        good = False
    else:
        ok("REDIS_URL format", f"host {urlsplit(redis).hostname}")

    missing = [
        k for k in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET") if not env.get(k)
    ]
    if missing:
        fail(
            "Storage settings",
            f"missing {', '.join(missing)}",
            "Supabase → Project Settings → Storage → S3 Connection → New access key.",
        )
        good = False
    else:
        ok(
            "Storage settings",
            f"bucket '{env['S3_BUCKET']}' @ {urlsplit(env['S3_ENDPOINT']).hostname}",
        )

    keys = [
        k
        for k in ("GEMINI_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY")
        if env.get(k)
    ]
    if not keys:
        fail("LLM keys", "none set", "Add at least GEMINI_API_KEY (aistudio.google.com/apikey).")
        good = False
    else:
        ok("LLM keys", ", ".join(keys))

    try:
        origins = json.loads(env.get("CORS_ORIGINS", '["http://localhost:3000"]'))
        assert isinstance(origins, list)
        ok("CORS_ORIGINS", ", ".join(origins))
    except Exception:
        fail("CORS_ORIGINS", "must be a JSON list", 'Example: ["https://your-app.vercel.app"]')
        good = False
    return good


# ───────────────────────────── 2. Postgres ─────────────────────────────


async def check_postgres(env: dict) -> None:
    print("\n2. Supabase Postgres")
    import asyncpg

    u = urlsplit(env["DATABASE_URL"])
    try:
        conn = await asyncpg.connect(
            host=u.hostname,
            port=u.port or 5432,
            user=unquote(u.username or ""),
            password=unquote(u.password or ""),
            database=(u.path or "/postgres")[1:],
            ssl="require",  # as the app: encrypted; Supabase uses its own CA
            timeout=15,
            statement_cache_size=0,
        )
    except asyncpg.InvalidPasswordError as e:
        fail(
            "Connect (asyncpg)",
            short(e),
            "Wrong password, or special characters not URL-encoded. Reset it under "
            "Project Settings → Database.",
        )
        return
    except Exception as e:
        hint = "Check host/port from Supabase → Connect → Session pooler."
        if "not found" in str(e).lower() and "tenant" in str(e).lower():
            hint = "User must be postgres.<project-ref> and the pooler host must match your region."
        fail("Connect (asyncpg)", short(e), hint)
        return
    try:
        version = await conn.fetchval("select split_part(version(), ' ', 2)")
        ok("Connect (asyncpg)", f"Postgres {version}")
        available = await conn.fetchval(
            "select count(*) from pg_available_extensions where name = 'vector'"
        )
        installed = await conn.fetchval("select count(*) from pg_extension where extname='vector'")
        if installed:
            ok("pgvector", "installed")
        elif available:
            try:
                await conn.execute("create extension if not exists vector")
                ok("pgvector", "was available; installed it now")
            except Exception as e:
                fail("pgvector", short(e), "Database → Extensions → enable 'vector'.")
        else:
            fail("pgvector", "not available", "Database → Extensions → enable 'vector'.")
        await conn.execute(
            "create temp table _finops_check (x int); insert into _finops_check values (1)"
        )
        ok("Write access", "can create tables (needed by migrations)")
        head = (
            await conn.fetchval("select version_num from alembic_version limit 1")
            if await conn.fetchval("select to_regclass('public.alembic_version') is not null")
            else None
        )
        print(f"    {DIM}migrations applied: {head or 'none yet (they run on first start)'}{END}")
    finally:
        await conn.close()

    # The workflow checkpointer uses psycopg (libpq), with its own SSL spelling.
    sys.path.insert(0, str(ROOT / "backend"))
    import psycopg

    from app.agents.checkpointer import psycopg_dsn

    try:
        async with await psycopg.AsyncConnection.connect(
            psycopg_dsn(env["DATABASE_URL"]), connect_timeout=15, prepare_threshold=None
        ) as pc:
            await pc.execute("select 1")
        ok("Connect (psycopg, workflow checkpoints)")
    except Exception as e:
        fail("Connect (psycopg, workflow checkpoints)", short(e))


# ───────────────────────────── 3. Redis ─────────────────────────────


async def check_redis(env: dict) -> None:
    print("\n3. Upstash Redis")
    from redis.asyncio import Redis

    r = Redis.from_url(env["REDIS_URL"], socket_timeout=10)
    try:
        t = time.perf_counter()
        await r.ping()
        ok("PING", f"{(time.perf_counter() - t) * 1000:.0f} ms round trip")
        key = f"finops:check:{uuid.uuid4().hex[:8]}"
        await r.set(key, "1", ex=30)
        assert await r.get(key) == b"1"
        await r.delete(key)
        ok("Read/write")
        pubsub = r.pubsub()
        await pubsub.subscribe("finops:check")
        await pubsub.aclose()
        ok("Pub/sub (live timeline)")
    except Exception as e:
        hint = "Use the rediss:// TCP URL including the password."
        if "WRONGPASS" in str(e) or "NOAUTH" in str(e):
            hint = "Password in REDIS_URL is wrong. Copy the full URL again from Upstash."
        fail("Redis", short(e), hint)
    finally:
        await r.aclose()


# ───────────────────────────── 4. Storage ─────────────────────────────


def check_storage(env: dict) -> None:
    print("\n4. Supabase Storage (S3)")
    import boto3
    import httpx
    from botocore.client import Config
    from botocore.exceptions import ClientError

    common = dict(
        aws_access_key_id=env["S3_ACCESS_KEY"],
        aws_secret_access_key=env["S3_SECRET_KEY"],
        region_name=env.get("S3_REGION") or "us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    s3 = boto3.client("s3", endpoint_url=env["S3_ENDPOINT"], **common)
    bucket = env["S3_BUCKET"]
    try:
        s3.head_bucket(Bucket=bucket)
        ok("Bucket exists", bucket)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        hint = {
            "404": f"Create a private bucket named '{bucket}' in Supabase → Storage.",
            "NoSuchBucket": f"Create a private bucket named '{bucket}' in Supabase → Storage.",
            "403": "Access key/secret wrong, or S3 connection not enabled.",
            "SignatureDoesNotMatch": "Secret key wrong, or S3_REGION doesn't match Supabase's.",
        }.get(str(code), "Check endpoint, region and keys on the Supabase S3 Connection page.")
        fail("Bucket exists", f"{code}", hint)
        return
    except Exception as e:
        fail("Bucket exists", short(e), "Check S3_ENDPOINT (…/storage/v1/s3).")
        return
    key = f"_connection-check/{uuid.uuid4().hex}.txt"
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=b"finops", ContentType="text/plain")
        assert s3.get_object(Bucket=bucket, Key=key)["Body"].read() == b"finops"
        ok("Upload and download")
        public = boto3.client(
            "s3", endpoint_url=env.get("S3_PUBLIC_ENDPOINT") or env["S3_ENDPOINT"], **common
        )
        url = public.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=120
        )
        resp = httpx.get(url, timeout=15)
        if resp.status_code == 200 and resp.content == b"finops":
            ok("Pre-signed URL (how the browser shows invoice pages)")
        else:
            fail(
                "Pre-signed URL",
                f"HTTP {resp.status_code}",
                "S3_PUBLIC_ENDPOINT should equal S3_ENDPOINT for Supabase.",
            )
    except Exception as e:
        fail("Upload and download", short(e))
    finally:
        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass


# ───────────────────────────── 5. LLM providers ─────────────────────────────


async def check_llms(env: dict) -> None:
    print("\n5. LLM providers (direct mode, as on Render)")
    sys.path.insert(0, str(ROOT / "backend"))
    from app.llm.direct import TIERS

    tested: dict = {}
    tier_ok: dict[str, bool] = dict.fromkeys(TIERS, False)
    for tier, deployments in TIERS.items():
        for d in deployments:
            if not env.get(d.key_env):
                continue
            if d not in tested:
                tested[d] = await _probe(d, env)
            status = tested[d]
            name = f"{tier:8} {d.provider}/{d.model}"
            if status == "ok":
                tier_ok[tier] = True
                print(f"    {GREEN}✓{END} {name}")
            elif status in ("429", "500", "502", "503", "timeout"):
                print(f"    {YELLOW}~ {name}  busy ({status}): a fallback, not an error{END}")
            else:
                hint = {
                    "401": f"{d.key_env} is invalid",
                    "403": "no access or quota for this key",
                    "404": "model retired: update TIERS in app/llm/direct.py",
                }.get(status, "")
                print(f"    {RED}✗ {name}  {status}{END}" + (f"  → {hint}" if hint else ""))
    for tier in ("vision", "normal"):  # the tiers the workflow uses
        if tier_ok[tier]:
            ok(f"'{tier}' tier", "at least one provider answered")
        else:
            fail(
                f"'{tier}' tier",
                "no provider answered",
                "Add or fix a key covering this tier (Gemini covers both), or retry later.",
            )


async def _probe(d, env: dict) -> str:
    import openai

    client = openai.AsyncOpenAI(
        base_url=d.base_url, api_key=env[d.key_env], timeout=30, max_retries=0
    )
    try:
        await client.chat.completions.create(
            model=d.model,
            max_tokens=5,
            messages=[{"role": "user", "content": "Reply with the word ok"}],
        )
        return "ok"
    except openai.APIStatusError as e:
        return str(e.status_code)
    except openai.APITimeoutError:
        return "timeout"
    except Exception as e:
        return short(e)


def load_env() -> tuple[Path, dict] | None:
    p = argparse.ArgumentParser()
    p.add_argument("--env", default=str(ROOT / ".env.cloud"))
    path = Path(p.parse_args().env)
    if not path.exists():
        print(f"{RED}{path} not found{END}")
        return None
    return path, {k: v for k, v in dotenv_values(path).items() if v}


async def main(path: Path, env: dict) -> int:
    print(f"Checking connections from {path.name} (values are never printed)")
    config_ok = check_config(env)
    if env.get("DATABASE_URL") and "POOLER-HOST" not in env["DATABASE_URL"]:
        await check_postgres(env)
    if env.get("REDIS_URL", "").startswith("rediss://"):
        await check_redis(env)
    if all(env.get(k) for k in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET")):
        check_storage(env)
    await check_llms(env)

    passed = sum(r for _, r in results)
    failed = [n for n, r in results if not r]
    print(f"\n{passed}/{len(results)} checks passed.")
    if failed or not config_ok:
        print(f"{RED}Fix: {', '.join(failed)}{END}")
        return 1
    print(f"{GREEN}All connections work. Next: make cloud-local (run the app against them).{END}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    loaded = load_env()
    sys.exit(asyncio.run(main(*loaded)) if loaded else 2)
