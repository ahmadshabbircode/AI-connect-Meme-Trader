import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import streamlit as st
import pandas as pd
from app.scanner import load_db
st.set_page_config(page_title='AI-connect Meme Trader V5', layout='wide')
st.title('🚀 AI-connect Meme Trader V5')
st.caption('Read-only Solana meme-token intelligence • heuristic score • no trading or wallet signing')
col1, col2 = st.columns([3,1])
with col2:
    minimum = st.slider('Minimum score', 0, 100, 0)
    if st.button('Refresh'): st.rerun()
with col1:
    st.info('Live feed: PumpPortal new-token stream. Enrichment: DEX Screener, Solana RPC and RugCheck when available.')
data = list(load_db().values())
data = [x for x in data if (x.get('scoring') or {}).get('total', 0) >= minimum]
data.sort(key=lambda x:(x.get('scoring') or {}).get('total',0), reverse=True)
if not data: st.warning('No token records yet. Start the scanner worker and refresh this page.')
else:
    rows=[]
    for x in data:
        rows.append({'Score':(x.get('scoring') or {}).get('total'), 'Decision':(x.get('scoring') or {}).get('decision'),
          'Token':x.get('base_name'), 'Symbol':x.get('symbol'), 'Liquidity $':x.get('liquidity_usd'),
          'Volume 1h $':x.get('volume_h1'), 'Buys 1h':x.get('buys_h1'), 'Sells 1h':x.get('sells_h1'),
          'Mint':x.get('mint'), 'Pair':x.get('url')})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    selected=st.selectbox('Inspect token', [x.get('mint') for x in data])
    item=next(x for x in data if x.get('mint')==selected)
    st.subheader(f"{item.get('base_name')} ({item.get('symbol')})")
    st.json(item)
    if item.get('url'): st.markdown(f"[Open DEX Screener pair]({item['url']})")
    st.caption('Unknown fields are shown as unknown, never treated as safe. No automated buy/sell actions are included.')

st.divider(); st.subheader('Read-only wallet address watch')
address=st.text_input('Paste your public Solana wallet address (never enter seed phrase/private key)')
if st.button('Check wallet address') and address:
    st.warning('This V5 build does not yet implement Phantom/Solflare in-app connection or portfolio history. Use only a public address; no signing or private keys.')
    st.markdown(f'[View address on Solscan](https://solscan.io/account/{address})')
