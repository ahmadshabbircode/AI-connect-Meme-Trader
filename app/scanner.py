import os
import time
import json
import threading
import logging
from datetime import datetime, timezone

import requests
import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

from app.scoring import score_token

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

LOCK = threading.Lock()

DEX = 'https://api.dexscreener.com/latest/dex/tokens/'
RUG = 'https://api.rugcheck.xyz/v1/tokens/{}/report/summary'

RPC = os.getenv(
    'SOLANA_RPC_URL',
    'https://api.mainnet-beta.solana.com'
)


# NEON DATABASE CONNECTION

def get_db_connection():
    url = os.getenv('DATABASE_URL')

    if not url:
        raise RuntimeError('DATABASE_URL is not configured')

    return psycopg.connect(
        url,
        sslmode='require',
        connect_timeout=10
    )


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            mint TEXT PRIMARY KEY,
            data JSONB NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)


def load_db():
    with get_db_connection() as conn:
        init_db(conn)

        rows = conn.execute(
            "SELECT mint, data FROM tokens"
        ).fetchall()

        return {
            mint: data
            for mint, data in rows
        }


def save_db(db):
    if not db:
        return

    with get_db_connection() as conn:
        init_db(conn)

        conn.executemany("""
            INSERT INTO tokens (mint, data, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (mint) DO UPDATE
            SET data = EXCLUDED.data,
                updated_at = EXCLUDED.updated_at
        """, [
            (mint, Jsonb(data))
            for mint, data in db.items()
        ])


# SOLANA SECURITY CHECKS

def solana_authorities(mint):
    try:
        payload = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'getAccountInfo',
            'params': [
                mint,
                {'encoding': 'jsonParsed'}
            ]
        }

        r = requests.post(
            RPC,
            json=payload,
            timeout=12
        )

        r.raise_for_status()

        value = r.json().get(
            'result', {}
        ).get('value')

        if not value:
            return None, None

        info = value.get(
            'data', {}
        ).get(
            'parsed', {}
        ).get('info', {})

        return (
            info.get('mintAuthority') is None,
            info.get('freezeAuthority') is None
        )

    except Exception as e:
        logging.warning(
            'RPC authority check failed for %s: %s',
            mint, e
        )

        return None, None


def rugcheck(mint):
    try:
        headers = {}

        key = os.getenv('RUGCHECK_API_KEY')

        if key:
            headers['Authorization'] = f'Bearer {key}'

        r = requests.get(
            RUG.format(mint),
            headers=headers,
            timeout=12
        )

        if r.status_code == 200:
            return r.json().get('score')

    except Exception as e:
        logging.warning(
            'RugCheck unavailable: %s',
            e
        )

    return None


# DEX SCREENER DATA

def dex_data(mint):
    r = requests.get(
        DEX + mint,
        timeout=12
    )

    r.raise_for_status()

    pairs = r.json().get('pairs') or []

    pairs = [
        p for p in pairs
        if p.get('chainId') == 'solana'
    ]

    if not pairs:
        return None

    p = max(
        pairs,
        key=lambda x: (
            x.get('liquidity') or {}
        ).get('usd') or 0
    )

    tx = p.get('txns', {}).get('h1', {})

    vol = p.get('volume', {}).get('h1') or 0

    buys = tx.get('buys') or 0
    sells = tx.get('sells') or 0

    liq = (
        p.get('liquidity') or {}
    ).get('usd') or 0

    activity = 0

    if liq >= 10000:
        activity += 4

    if buys > sells and buys + sells >= 10:
        activity += 4

    if vol > 0 and liq > 0 and vol / liq < 20:
        activity += 3

    if buys + sells >= 30:
        activity += 4

    return {
        'pair_address': p.get('pairAddress'),
        'dex_id': p.get('dexId'),
        'url': p.get('url'),
        'price_usd': p.get('priceUsd'),
        'liquidity_usd': liq,
        'market_cap': p.get('marketCap') or p.get('fdv'),
        'volume_h1': vol,
        'buys_h1': buys,
        'sells_h1': sells,
        'base_name': (
            p.get('baseToken') or {}
        ).get('name'),
        'symbol': (
            p.get('baseToken') or {}
        ).get('symbol'),
        'created_at': p.get('pairCreatedAt'),
        'organic_activity_score': min(15, activity)
    }


# TOKEN ENRICHMENT AND SCORING

def enrich(mint, meta=None):
    meta = meta or {}

        d = dex_data(mint) or {
        'pair_address': None,
        'dex_id': None,
        'url': None,
        'price_usd': None,
        'liquidity_usd': 0,
        'market_cap': None,
        'volume_h1': 0,
        'buys_h1': 0,
        'sells_h1': 0,
        'base_name': None,
        'symbol': None,
        'created_at': None,
        'organic_activity_score': 0
    }

    mint_ok, freeze_ok = solana_authorities(mint)

    d.update({
        'mint': mint,
        'mint_authority': mint_ok,
        'freeze_authority': freeze_ok,
        'rugcheck_score': rugcheck(mint),
        'top10_pct': None,
        'verified_smart_money_score': 0,
        'narrative_score': 0,
        'graduation_score': 0,
        'critical_security_failure': False,
        'data_note': (
            'Holder concentration, smart-money, narrative '
            'and graduation scores require verified data.'
        )
    })

    d.update(meta)

    d['scoring'] = score_token(d)

    d['updated_at'] = datetime.now(
        timezone.utc
    ).isoformat()

    return d


# SAVE TOKEN TO NEON

def process_mint(mint):
    try:
        item = enrich(mint)

        if item:
            with LOCK:
                save_db({mint: item})

            logging.info(
                'Updated %s score=%s',
                mint,
                item['scoring']['total']
            )

    except Exception as e:
        logging.exception(
            'Token processing failed %s: %s',
            mint,
            e
        )


# PUMPPORTAL NEW TOKEN LISTENER

def run_listener():
    import asyncio
    import websockets

    async def listen():

        uri = 'wss://pumpportal.fun/api/data'

        while True:
            try:
                async with websockets.connect(
                    uri,
                    ping_interval=20,
                    ping_timeout=20
                ) as ws:

                    await ws.send(
                        json.dumps({
                            'method': 'subscribeNewToken'
                        })
                    )

                    logging.info(
                        'Connected to PumpPortal new-token feed'
                    )

                    async for raw in ws:
                        msg = json.loads(raw)

                        mint = msg.get('mint')

                        if mint:
                            threading.Thread(
                                target=process_mint,
                                args=(mint,),
                                daemon=True
                            ).start()

            except Exception as e:
                logging.warning(
                    'Feed disconnected: %s; retry in 8 sec',
                    e
                )

                await asyncio.sleep(8)

    asyncio.run(listen())


# REFRESH EXISTING TOKENS

def periodic_refresh():

    while True:

        time.sleep(
            max(
                15,
                int(os.getenv(
                    'SCAN_INTERVAL_SECONDS',
                    '25'
                ))
            )
        )

        try:
            mints = list(load_db().keys())

            for mint in mints[:80]:
                threading.Thread(
                    target=process_mint,
                    args=(mint,),
                    daemon=True
                ).start()

        except Exception as e:
            logging.exception(
                'Periodic refresh failed: %s',
                e
            )


# START SCANNER

def start():

    threading.Thread(
        target=periodic_refresh,
        daemon=True
    ).start()

    run_listener()


if __name__ == '__main__':
    start()
