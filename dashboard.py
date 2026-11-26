import sqlite3, os, time
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
load_dotenv()
DB=os.getenv("DB_PATH","trader.db")
st.set_page_config(page_title="AI CONNECT Meme Trader V3",layout="wide")
st.title("🚀 AI CONNECT — Meme Trader V3")
st.warning("PAPER MODE ONLY. Simulated orders; no blockchain transactions or real funds are used.")
def read(q):
    with sqlite3.connect(DB) as c: return pd.read_sql_query(q,c)
try:
    wallet=read("SELECT balance FROM wallet WHERE id=1")
    pos=read("SELECT * FROM positions ORDER BY opened DESC")
    tokens=read("SELECT name,symbol,mint,score,buys,sells,unique_buyers,last_mc,status FROM tokens ORDER BY created DESC LIMIT 100")
    trades=read("SELECT mint,side,price,amount,pnl,datetime(ts,'unixepoch','localtime') AS time,note FROM trades ORDER BY ts DESC LIMIT 100")
    balance=float(wallet.iloc[0,0]) if len(wallet) else 1.0
    realized=float(trades[trades.side=="PAPER SELL"].pnl.sum()) if len(trades) else 0
    a,b,c=st.columns(3)
    a.metric("Paper wallet balance",f"{balance:.4f} SOL")
    b.metric("Open positions",len(pos))
    c.metric("Recent realized P/L",f"{realized:+.4f} SOL")
    st.subheader("Open paper positions")
    if len(pos):
        pos["opened"]=pd.to_datetime(pos.opened,unit="s")
        st.dataframe(pos,use_container_width=True)
    else: st.info("No open positions yet.")
    st.subheader("Recently detected tokens")
    st.dataframe(tokens,use_container_width=True)
    st.subheader("Trade history")
    st.dataframe(trades,use_container_width=True)
except Exception as e:
    st.info("Waiting for scanner to create its database. Start scanner.py first.")
    st.caption(str(e))
st.caption("Refresh this page to update. Scores are heuristic signals, not verified safety ratings.")
