import os, time, json, threading, logging
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv
from app.scoring import score_token

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
DB = os.path.join(os.path.dirname(__file__), '..', 'data', 'tokens.json')
LOCK = threading.Lock()
DEX = 'https://api.dexscreener.com/latest/dex/tokens/'
RUG = 'https://api.rugcheck.xyz/v1/tokens/{}/report/summary'
RPC = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')


def load_db():
    try:
        with open(DB, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception: return {}


def save_db(db):
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    tmp = DB + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(db, f, indent=2)
    os.replace(tmp, DB)


def solana_authorities(mint):
    """Read mint/freeze authority from Solana parsed account data; None means unknown."""
    try:
        payload = {'jsonrpc':'2.0','id':1,'method':'getAccountInfo','params':[mint, {'encoding':'jsonParsed'}]}
        r = requests.post(RPC, json=payload, timeout=12); r.raise_for_status()
        value = r.json().get('result', {}).get('value')
        if not value: return None, None
        info = value.get('data', {}).get('parsed', {}).get('info', {})
        return info.get('mintAuthority') is None, info.get('freezeAuthority') is None
    except Exception as e:
        logging.warning('RPC authority check failed for %s: %s', mint, e)
        return None, None


def rugcheck(mint):
    try:
        headers = {}
        key = os.getenv('RUGCHECK_API_KEY')
        if key: headers['Authorization'] = f'Bearer {key}'
        r = requests.get(RUG.format(mint), headers=headers, timeout=12)
        if r.status_code == 200:
            j = r.json()
            return j.get('score')
    except Exception as e: logging.warning('RugCheck unavailable: %s', e)
    return None


def dex_data(mint):
    r = requests.get(DEX + mint, timeout=12); r.raise_for_status()
    pairs = r.json().get('pairs') or []
    pairs = [p for p in pairs if p.get('chainId') == 'solana']
    if not pairs: return None
    p = max(pairs, key=lambda x: (x.get('liquidity') or {}).get('usd') or 0)
    tx = p.get('txns', {}).get('h1', {})
    vol = p.get('volume', {}).get('h1') or 0
    buys, sells = tx.get('buys') or 0, tx.get('sells') or 0
    liq = (p.get('liquidity') or {}).get('usd') or 0
    # High volume/market-cap is contextual, never an automatic rejection.
    unique = p.get('info', {}).get('uniqueTransactions')
    activity = 0
    if liq >= 10000: activity += 4
    if buys > sells and buys + sells >= 10: activity += 4
    if vol > 0 and liq > 0 and vol / liq < 20: activity += 3
    if buys + sells >= 30: activity += 4
    return {
      'pair_address': p.get('pairAddress'), 'dex_id': p.get('dexId'), 'url': p.get('url'),
      'price_usd': p.get('priceUsd'), 'liquidity_usd': liq,
      'market_cap': p.get('marketCap') or p.get('fdv'), 'volume_h1': vol,
      'buys_h1': buys, 'sells_h1': sells, 'base_name': (p.get('baseToken') or {}).get('name'),
      'symbol': (p.get('baseToken') or {}).get('symbol'), 'created_at': p.get('pairCreatedAt'),
      'organic_activity_score': min(15, activity)
    }


def enrich(mint, meta=None):
    meta = meta or {}
    d = dex_data(mint)
    if not d: return None
    mint_ok, freeze_ok = solana_authorities(mint)
    d.update({'mint': mint, 'mint_authority': mint_ok, 'freeze_authority': freeze_ok,
              'rugcheck_score': rugcheck(mint), 'top10_pct': None,
              'verified_smart_money_score': 0, 'narrative_score': 0, 'graduation_score': 0,
              'critical_security_failure': False,
              'data_note': 'Holder concentration, smart-money, narrative and graduation scores require verified data sources/manual evidence.'})
    # Critical checks are conservative: do not claim a failure when the check is unknown.
    d.update(meta)
    d['scoring'] = score_token(d)
    d['updated_at'] = datetime.now(timezone.utc).isoformat()
    return d


def process_mint(mint):
    try:
        item = enrich(mint)
        if item:
            with LOCK:
                db = load_db(); db[mint] = item; save_db(db)
            logging.info('Updated %s score=%s', mint, item['scoring']['total'])
    except Exception as e: logging.exception('Token processing failed %s: %s', mint, e)


def run_listener():
    """PumpPortal new-token feed. Reconnects on disconnect; feed availability can change."""
    import asyncio, websockets
    async def listen():
        uri = 'wss://pumpportal.fun/api/data'
        while True:
            try:
                async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps({'method':'subscribeNewToken'}))
                    logging.info('Connected to PumpPortal new-token feed')
                    async for raw in ws:
                        msg = json.loads(raw)
                        mint = msg.get('mint')
                        if mint: threading.Thread(target=process_mint, args=(mint,), daemon=True).start()
            except Exception as e:
                logging.warning('Feed disconnected: %s; retry in 8 sec', e); await asyncio.sleep(8)
    asyncio.run(listen())


def periodic_refresh():
    while True:
        time.sleep(max(15, int(os.getenv('SCAN_INTERVAL_SECONDS', '25'))))
        with LOCK: mints = list(load_db().keys())
        for mint in mints[:80]: threading.Thread(target=process_mint, args=(mint,), daemon=True).start()


def start():
    threading.Thread(target=periodic_refresh, daemon=True).start()
    run_listener()

if __name__ == '__main__': start()
