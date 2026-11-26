import asyncio, json, os, sqlite3, time, logging
from collections import defaultdict, deque
from dotenv import load_dotenv
import websockets

load_dotenv()
DB=os.getenv("DB_PATH","trader.db")
API_KEY=os.getenv("PUMPPORTAL_API_KEY","").strip()
WS_URL="wss://pumpportal.fun/api/data" + (f"?api-key={API_KEY}" if API_KEY else "")
MIN_SCORE=int(os.getenv("MIN_SCORE","75"))
MAX_POSITIONS=int(os.getenv("MAX_POSITIONS","3"))
TRADE_SIZE=float(os.getenv("PAPER_TRADE_SOL","0.05"))
STOP_LOSS=float(os.getenv("STOP_LOSS_PCT","15"))
TAKE_PROFITS=[(20,0.25),(40,0.25),(70,0.25)] # final quarter exits at +100%
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def db():
    c=sqlite3.connect(DB, timeout=20)
    c.execute("""CREATE TABLE IF NOT EXISTS tokens(mint TEXT PRIMARY KEY,name TEXT,symbol TEXT,created REAL,score INTEGER,last_mc REAL,buys INTEGER DEFAULT 0,sells INTEGER DEFAULT 0,unique_buyers INTEGER DEFAULT 0,status TEXT DEFAULT 'WATCH',reason TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT,mint TEXT,side TEXT,price REAL,amount REAL,pnl REAL,ts REAL,note TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS positions(mint TEXT PRIMARY KEY,entry REAL,remaining REAL,initial REAL,opened REAL,take1 INTEGER DEFAULT 0,take2 INTEGER DEFAULT 0,take3 INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS wallet(id INTEGER PRIMARY KEY CHECK(id=1),balance REAL)""")
    c.execute("INSERT OR IGNORE INTO wallet(id,balance) VALUES(1,1.0)")
    c.commit()
    return c

stats=defaultdict(lambda: {"buys":0,"sells":0,"buyers":set(),"trades":deque(maxlen=40),"mc":0.0})
subscribed=set()

def score(s):
    n=s["buys"]+s["sells"]
    if n < 5: return 0
    ratio=s["buys"]/max(1,n)
    unique=len(s["buyers"])
    # Conservative heuristic only; not an audit or rug detector.
    return max(0,min(100, round(45*ratio + min(unique,15)*2 + min(n,20)*0.75)))

def add_token(c,d):
    mint=d.get("mint")
    if not mint: return
    c.execute("INSERT OR IGNORE INTO tokens(mint,name,symbol,created) VALUES(?,?,?,?)",
              (mint,d.get("name",""),d.get("symbol",""),time.time()))
    c.commit()

def trade_event(c,d):
    mint=d.get("mint")
    if not mint: return
    s=stats[mint]
    side=str(d.get("txType","")).lower()
    wallet=d.get("traderPublicKey","")
    mc=float(d.get("marketCapSol") or 0)
    if mc>0: s["mc"]=mc
    if side=="buy":
        s["buys"]+=1
        if wallet: s["buyers"].add(wallet)
    elif side=="sell": s["sells"]+=1
    else: return
    s["trades"].append((side,mc))
    sc=score(s)
    c.execute("""UPDATE tokens SET buys=?,sells=?,unique_buyers=?,score=?,last_mc=? WHERE mint=?""",
              (s["buys"],s["sells"],len(s["buyers"]),sc,s["mc"],mint))
    c.commit()
    maybe_buy(c,mint,sc,s)
    manage_position(c,mint,s["mc"])

def maybe_buy(c,mint,sc,s):
    if sc<MIN_SCORE or s["mc"]<=0: return
    if c.execute("SELECT 1 FROM positions WHERE mint=?",(mint,)).fetchone(): return
    count=c.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    if count>=MAX_POSITIONS: return
    wallet=c.execute("SELECT balance FROM wallet WHERE id=1").fetchone()[0]
    if wallet<TRADE_SIZE: return
    c.execute("UPDATE wallet SET balance=balance-? WHERE id=1",(TRADE_SIZE,))
    c.execute("INSERT INTO positions(mint,entry,remaining,initial,opened) VALUES(?,?,?,?,?)",
              (mint,s["mc"],TRADE_SIZE,TRADE_SIZE,time.time()))
    c.execute("INSERT INTO trades(mint,side,price,amount,pnl,ts,note) VALUES(?,?,?,?,?,?,?)",
              (mint,"PAPER BUY",s["mc"],TRADE_SIZE,0,time.time(),f"score {sc}/100"))
    c.execute("UPDATE tokens SET status='PAPER OPEN' WHERE mint=?",(mint,))
    c.commit()
    logging.info("PAPER BUY %s score=%s mc=%s",mint,sc,s["mc"])

def manage_position(c,mint,mc):
    if mc<=0: return
    row=c.execute("SELECT entry,remaining,initial,take1,take2,take3 FROM positions WHERE mint=?",(mint,)).fetchone()
    if not row: return
    entry,remaining,initial,t1,t2,t3=row
    pct=(mc/entry-1)*100 if entry else 0
    exits=[]
    if pct<=-STOP_LOSS: exits=[("STOP LOSS",1.0)]
    else:
        if pct>=20 and not t1: exits.append(("TAKE PROFIT 20%",.25))
        if pct>=40 and not t2: exits.append(("TAKE PROFIT 40%",.25))
        if pct>=70 and not t3: exits.append(("TAKE PROFIT 70%",.25))
        if pct>=100: exits=[("TAKE PROFIT 100%",1.0)]
    for label,fraction in exits:
        fraction=min(1.0,fraction)
        sell_value=remaining*fraction*(mc/entry)
        cost_basis=remaining*fraction
        pnl=sell_value-cost_basis
        c.execute("UPDATE wallet SET balance=balance+? WHERE id=1",(sell_value,))
        remaining-=cost_basis
        c.execute("INSERT INTO trades(mint,side,price,amount,pnl,ts,note) VALUES(?,?,?,?,?,?,?)",
                  (mint,"PAPER SELL",mc,sell_value,pnl,time.time(),label))
        if label.startswith("TAKE PROFIT 20"): t1=1
        if label.startswith("TAKE PROFIT 40"): t2=1
        if label.startswith("TAKE PROFIT 70"): t3=1
        logging.info("%s %s P/L %.4f SOL",label,mint,pnl)
        if remaining<=0.0000001:
            c.execute("DELETE FROM positions WHERE mint=?",(mint,))
            c.execute("UPDATE tokens SET status='CLOSED' WHERE mint=?",(mint,))
            c.commit(); return
        c.execute("UPDATE positions SET remaining=?,take1=?,take2=?,take3=? WHERE mint=?",
                  (remaining,t1,t2,t3,mint))
    c.commit()

async def run():
    c=db()
    while True:
        try:
            logging.info("Connecting to PumpPortal live feed (paper trading only)")
            async with websockets.connect(WS_URL,ping_interval=20,ping_timeout=20) as ws:
                await ws.send(json.dumps({"method":"subscribeNewToken"}))
                # Subscribe to already tracked open/watch tokens if API key supplied
                rows=c.execute("SELECT mint FROM tokens WHERE status IN ('WATCH','PAPER OPEN')").fetchall()
                if API_KEY:
                    for (mint,) in rows:
                        await ws.send(json.dumps({"method":"subscribeTokenTrade","keys":[mint]}))
                        subscribed.add(mint)
                async for raw in ws:
                    try: d=json.loads(raw)
                    except Exception: continue
                    if d.get("mint") and ("txType" in d or "marketCapSol" in d):
                        if d.get("txType"):
                            trade_event(c,d)
                    elif d.get("mint"):
                        add_token(c,d)
                        mint=d["mint"]
                        if API_KEY and mint not in subscribed:
                            await ws.send(json.dumps({"method":"subscribeTokenTrade","keys":[mint]}))
                            subscribed.add(mint)
                        logging.info("NEW TOKEN %s %s",d.get("symbol",""),mint)
        except Exception as e:
            logging.warning("Feed disconnected: %s; reconnecting in 5 sec",e)
            await asyncio.sleep(5)

if __name__=="__main__":
    asyncio.run(run())
