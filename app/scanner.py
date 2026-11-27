import os
import time
import json
import asyncio
import threading
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from datetime import datetime, timezone

import requests
import psycopg
import websockets
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

from app.scoring import score_token

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# General database-write lock
LOCK = threading.Lock()

# Bounded token-processing workers: four workers, maximum 40 queued/running jobs.
EXECUTOR = ThreadPoolExecutor(max_workers=4)
JOB_SLOTS = BoundedSemaphore(40)
JOB_LOCK = threading.Lock()
ACTIVE_MINTS = set()

DEX = "https://api.dexscreener.com/latest/dex/tokens/"
RUG = "https://api.rugcheck.xyz/v1/tokens/{}/report/summary"
RPC = os.getenv(
    "SOLANA_RPC_URL",
    "https://api.mainnet-beta.solana.com",
)


# Neon database connection
def get_db_connection():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")

    return psycopg.connect(
        url,
        sslmode="require",
        connect_timeout=10,
    )


def init_db(conn):
    conn.execute(
        """ CREATE TABLE IF NOT EXISTS tokens ( mint TEXT PRIMARY KEY, data JSONB NOT NULL, updated_at TIMESTAMPTZ DEFAULT NOW() ) """
    )


def load_db():
    with get_db_connection() as conn:
        init_db(conn)
        rows = conn.execute(
            "SELECT mint, data FROM tokens"
        ).fetchall()

    return {mint: data for mint, data in rows}


def save_db(db):
    if not db:
        return

    with get_db_connection() as conn:
        init_db(conn)

        for mint, data in db.items():
            conn.execute(
                """ INSERT INTO tokens (mint, data, updated_at) VALUES (%s, %s, NOW()) ON CONFLICT (mint) DO UPDATE SET data = EXCLUDED.data, updated_at = NOW() """,
                (mint, Jsonb(data)),
            )


# Solana security checks
def solana_authorities(mint):
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [
                mint,
                {"encoding": "jsonParsed"},
            ],
        }

        response = requests.post(
            RPC,
            json=payload,
            timeout=12,
        )
        response.raise_for_status()

        value = response.json().get(
            "result", {}
        ).get("value")

        if not value:
            return None, None

        info = (
            value.get("data", {})
            .get("parsed", {})
            .get("info", {})
        )

        return (
            info.get("mintAuthority") is None,
            info.get("freezeAuthority") is None,
        )

    except Exception as exc:
        logging.warning(
            "RPC authority check failed for %s: %s",
            mint,
            exc,
        )
        return None, None


def rugcheck(mint):
    try:
        headers = {}
        key = os.getenv("RUGCHECK_API_KEY")

        if key:
            headers["Authorization"] = f"Bearer {key}"

        response = requests.get(
            RUG.format(mint),
            headers=headers,
            timeout=12,
        )

        if response.status_code == 200:
            return response.json().get("score")

        logging.warning(
            "RugCheck returned HTTP %s for %s",
            response.status_code,
            mint,
        )

    except Exception as exc:
        logging.warning(
            "RugCheck unavailable for %s: %s",
            mint,
            exc,
        )

    return None


# DexScreener data with cache and request-rate limiting
def dex_data(mint):
    if not hasattr(dex_data, "_cache"):
        dex_data._cache = {}

    if not hasattr(dex_data, "_lock"):
        dex_data._lock = threading.Lock()

    if not hasattr(dex_data, "_last_request"):
        dex_data._last_request = 0.0

    cache = dex_data._cache

    with dex_data._lock:
        cached = cache.get(mint)
        if cached:
            saved_time, saved_data = cached
            if time.monotonic() - saved_time < 60:
                return dict(saved_data) if saved_data else None

        for attempt in range(4):
            elapsed = time.monotonic() - dex_data._last_request
            if elapsed < 1.25:
                time.sleep(1.25 - elapsed)

            try:
                response = requests.get(
                    DEX + mint,
                    timeout=15,
                )
                dex_data._last_request = time.monotonic()

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        wait = float(retry_after)
                    except (TypeError, ValueError):
                        wait = 5 * (2 ** attempt)

                    logging.warning(
                        "DexScreener rate limit for %s. Retry in %s seconds.",
                        mint,
                        wait,
                    )
                    if attempt < 3:
                        time.sleep(min(wait, 60))
                    continue

                response.raise_for_status()
                pairs = response.json().get("pairs") or []
                pairs = [
                    pair for pair in pairs
                    if pair.get("chainId") == "solana"
                ]

                if not pairs:
                    cache[mint] = (time.monotonic(), None)
                    return None

                pair = max(
                    pairs,
                    key=lambda item: (
                        (item.get("liquidity") or {}).get("usd") or 0
                    ),
                )

                tx = (pair.get("txns") or {}).get("h1") or {}
                vol = (pair.get("volume") or {}).get("h1") or 0
                buys = tx.get("buys") or 0
                sells = tx.get("sells") or 0
                liquidity = (
                    (pair.get("liquidity") or {}).get("usd") or 0
                )

                activity = 0
                if liquidity >= 10000:
                    activity += 4
                if buys > sells and buys + sells >= 10:
                    activity += 4
                if vol > 0 and liquidity > 0 and vol / liquidity < 20:
                    activity += 3
                if buys + sells >= 30:
                    activity += 4

                result = {
                    "pair_address": pair.get("pairAddress"),
                    "dex_id": pair.get("dexId"),
                    "url": pair.get("url"),
                    "price_usd": pair.get("priceUsd"),
                    "liquidity_usd": liquidity,
                    "market_cap": pair.get("marketCap") or pair.get("fdv"),
                    "volume_h1": vol,
                    "buys_h1": buys,
                    "sells_h1": sells,
                    "base_name": (
                        (pair.get("baseToken") or {}).get("name")
                    ),
                    "symbol": (
                        (pair.get("baseToken") or {}).get("symbol")
                    ),
                    "created_at": pair.get("pairCreatedAt"),
                    "organic_activity_score": min(15, activity),
                }

                cache[mint] = (time.monotonic(), dict(result))
                return result

            except requests.RequestException as exc:
                logging.warning(
                    "DexScreener request failed for %s: %s",
                    mint,
                    exc,
                )
                break

            except (ValueError, TypeError, AttributeError) as exc:
                logging.warning(
                    "DexScreener data error for %s: %s",
                    mint,
                    exc,
                )
                break

        cache[mint] = (time.monotonic(), None)
        return None


# Token enrichment and scoring
def enrich(mint, meta=None):
    meta = meta or {}

    data = dex_data(mint) or {
        "pair_address": None,
        "dex_id": None,
        "url": None,
        "price_usd": None,
        "liquidity_usd": 0,
        "market_cap": None,
        "volume_h1": 0,
        "buys_h1": 0,
        "sells_h1": 0,
        "base_name": None,
        "symbol": None,
        "created_at": None,
        "organic_activity_score": 0,
    }

    mint_ok, freeze_ok = solana_authorities(mint)

    data.update(
        {
            "mint": mint,
            "mint_authority": mint_ok,
            "freeze_authority": freeze_ok,
            "rugcheck_score": rugcheck(mint),
            "top10_pct": None,
            "verified_smart_money_score": 0,
            "narrative_score": 0,
            "graduation_score": 0,
            "critical_security_failure": False,
            "data_note": (
                "Holder concentration, smart-money, narrative "
                "and graduation scores require verified data."
            ),
        }
    )

    data.update(meta)
    data["scoring"] = score_token(data)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    return data


# Save token to Neon
def process_mint(mint):
    try:
        item = enrich(mint)

        if item:
            with LOCK:
                save_db({mint: item})

            logging.info(
                "Updated %s score=%s",
                mint,
                item["scoring"]["total"],
            )

    except Exception as exc:
        logging.exception(
            "Token processing failed %s: %s",
            mint,
            exc,
        )


# Controlled token job submission
def submit_mint(mint):
    if not isinstance(mint, str) or not mint.strip():
        return False

    mint = mint.strip()

    with JOB_LOCK:
        if mint in ACTIVE_MINTS:
            return False

        if not JOB_SLOTS.acquire(blocking=False):
            logging.warning(
                "Scanner queue full; skipping %s",
                mint,
            )
            return False

        ACTIVE_MINTS.add(mint)

    def worker():
        try:
            process_mint(mint)
        finally:
            with JOB_LOCK:
                ACTIVE_MINTS.discard(mint)
            JOB_SLOTS.release()

    try:
        EXECUTOR.submit(worker)
        return True

    except Exception:
        with JOB_LOCK:
            ACTIVE_MINTS.discard(mint)

        JOB_SLOTS.release()
        logging.exception(
            "Could not submit token job: %s",
            mint,
        )
        return False


# PumpPortal new-token listener
def run_listener():
    async def listen():
        uri = "wss://pumpportal.fun/api/data"

        while True:
            try:
                async with websockets.connect(
                    uri,
                    ping_interval=20,
                    ping_timeout=20,
                    open_timeout=20,
                ) as websocket:

                    await websocket.send(
                        json.dumps({"method": "subscribeNewToken"})
                    )

                    logging.info(
                        "Connected to PumpPortal new-token feed"
                    )

                    async for raw in websocket:
                        try:
                            message = json.loads(raw)
                        except (ValueError, TypeError):
                            logging.warning(
                                "Invalid PumpPortal message received"
                            )
                            continue

                        if not isinstance(message, dict):
                            continue

                        mint = message.get("mint")
                        if mint:
                            submit_mint(mint)

            except Exception as exc:
                logging.warning(
                    "Feed disconnected: %s; retry in 8 sec",
                    exc,
                )
                await asyncio.sleep(8)

    asyncio.run(listen())


# Refresh existing tokens
def periodic_refresh():
    while True:
        time.sleep(
            max(
                15,
                int(os.getenv("SCAN_INTERVAL_SECONDS", "25")),
            )
        )

        try:
            mints = list(load_db().keys())
            for mint in mints[:80]:
                submit_mint(mint)

        except Exception:
            logging.exception("Periodic refresh failed")


# GeckoTerminal Solana new-pools discovery
def run_geckoterminal_listener():
    url = (
        "https://api.geckoterminal.com/api/v2/"
        "networks/solana/new_pools"
    )

    seen_mints = set()

    while True:
        try:
            response = requests.get(
                url,
                headers={"Accept": "application/json"},
                timeout=20,
            )
            response.raise_for_status()

            pools = response.json().get("data", [])
            discovered = 0

            for pool in pools:
                relationships = pool.get("relationships") or {}
                base_data = (
                    relationships.get("base_token") or {}
                ).get("data") or {}

                token_id = base_data.get("id", "")
                if not token_id.startswith("solana_"):
                    continue

                mint = token_id.replace("solana_", "", 1)
                if not mint or mint in seen_mints:
                    continue

                seen_mints.add(mint)
                submit_mint(mint)

                discovered += 1
                if discovered >= 10:
                    break

            logging.info(
                "GeckoTerminal discovered %s new Solana tokens",
                discovered,
            )

            if len(seen_mints) > 2000:
                seen_mints.clear()

        except Exception:
            logging.exception("GeckoTerminal discovery failed")

        time.sleep(70)


# Start scanner
def start():
    logging.info("AI-connect Meme Trader V5 scanner starting")

    try:
        with get_db_connection() as conn:
            init_db(conn)
        logging.info("Neon database connection ready")
    except Exception:
        logging.exception(
            "Database initialization failed; scanner will retry"
        )

    threading.Thread(
        target=periodic_refresh,
        name="periodic-refresh",
        daemon=True,
    ).start()

    threading.Thread(
        target=run_geckoterminal_listener,
        name="geckoterminal-listener",
        daemon=True,
    ).start()

    run_listener()


if __name__ == "__main__":
    start()
