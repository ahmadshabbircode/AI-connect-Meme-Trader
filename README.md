# AI-connect Meme Trader V5 (phone-friendly deployment package)

## What is included
- PumpPortal new-token WebSocket listener with reconnect loop.
- DEX Screener pair enrichment (liquidity, market cap/FDV, hourly volume, buys/sells).
- Solana parsed mint-account checks for mint and freeze authority.
- RugCheck summary lookup when the endpoint responds.
- Transparent 100-point heuristic scoring with the agreed weights: security 25, smart money 20, holders 15, organic activity 15, narrative 15, graduation 10.
- Streamlit dashboard and a small FastAPI health/tokens API.
- Read-only public-address watch link. No private keys, signing, swaps or automatic trades.

## Important V5 limitations (do not mistake missing data for a pass)
This is a working starter scanner, not the complete intelligence system discussed. It does **not** yet verify top-10 holder ownership/concentration, profitable smart-money wallets, wallet clusters/bundled buys, Bubblemaps graph data, exact Pump.fun bonding-curve percentage/graduation, X/Reddit/TikTok trend APIs, Elon Musk quote verification, or native Phantom/Solflare connection. These require additional supported data providers, API access, and validation. Corresponding score inputs stay at zero/unknown rather than being fabricated.

The scoring is a heuristic, not an AI prediction. Volume greater than market cap is not an automatic rejection; review volume together with liquidity, token age, buy/sell counts, wallet diversity and suspicious activity. Current starter does not yet calculate wallet diversity or bot probability.

## Android-only GitHub upload
1. Download and extract this ZIP on your Android phone.
2. Open your GitHub repository `AI-connect-Meme-Trader` in Chrome.
3. Use **Add file → Upload files** and upload the extracted files/folders (not the ZIP itself if you want GitHub Actions/Render to read the project).
4. Commit to a new branch named `v5-test` first. Keep your V3 deployment unchanged.
5. In Render, create a **Blueprint** from that branch/repository and review the services before applying.

## Render deployment notes
The included `render.yaml` defines a web dashboard and a separate scanner worker. The worker uses a Starter plan because a continuously running background worker is not reliably available on the free web service. The dashboard and worker currently use local `data/tokens.json`; separate Render services do not share local files. For a real deployment, configure a shared persistent database (PostgreSQL) or a persistent disk and update storage before relying on dashboard data. Do not assume this Blueprint is production-ready until shared storage is configured.

For a quick single-service test, run locally:
```bash
pip install -r requirements.txt
python -m app.scanner
```
In another process:
```bash
streamlit run dashboard.py
```

## Environment
Copy `.env.example` to `.env` for local use. On Render, set environment variables in each service. A dedicated Solana RPC provider is recommended because the public RPC is rate-limited. RugCheck may rate-limit or change its API; unavailable results remain unknown.

## Safety
Never paste a seed phrase, private key, or secret recovery phrase into this app, GitHub, Render, or ChatGPT. Wallet support is public-address monitoring only. No real trading is enabled.
