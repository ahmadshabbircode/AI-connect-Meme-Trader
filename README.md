# AI CONNECT Meme Trader V3 — Paper Trading

This is a phone-friendly Python project that upgrades the earlier V2 alert scanner into a **live-data paper-trading simulator**. It does not send real trades or require a wallet/private key.

## Important
- Start in paper mode. Pump.fun tokens are extremely risky; stop-losses may not execute at the displayed price in real markets.
- The current V3 deliberately contains no live-buy/sell code.
- PumpPortal's `subscribeNewToken` stream is free. `subscribeTokenTrade` requires an API key and a linked wallet with at least 0.02 SOL; trade streaming is charged at 0.01 SOL per 10,000 events. Review its terms and fees before enabling that data stream.
- The score is a simple activity heuristic, **not** an AI audit, honeypot test, or guarantee against rugs.
- Never paste a wallet seed phrase or private key into GitHub, Render, this app, or chat.

## Files
- `scanner.py`: one websocket connection; detects launches, subscribes to token trades, scores activity, and simulates entries/exits in SQLite.
- `dashboard.py`: Streamlit live dashboard.
- `.env.example`: safe configuration template.

## Run
Python 3.10+:
```bash
pip install -r requirements.txt
cp .env.example .env
python scanner.py
```
In another terminal:
```bash
streamlit run dashboard.py
```
On Android, use a cloud Python host or GitHub Codespaces-like environment; a Streamlit dashboard alone does not keep the scanner process running.

## Default paper rules
- Starting simulated balance: 1 SOL
- Position size: 0.05 SOL
- Maximum concurrent positions: 3
- Minimum score: 75
- Stop loss: -15%
- Sell 25% at +20%, 25% at +40%, 25% at +70%, remaining at +100%

These defaults are experimental. They are not a profitable strategy and exclude network fees, slippage, MEV, failed fills, liquidity depth, and real wallet balances. The paper fill price uses PumpPortal's `marketCapSol` as a proxy and therefore is only a rough simulation.

## Data source
PumpPortal websocket documentation: https://pumpportal.fun/data-api/real-time/
Trading API documentation (not enabled in this build): https://pumpportal.fun/trading-api/
Solana key security guidance: https://solana.com/docs/tools/security

## Next development gate
Before any real-money mode, add reliable executable quotes/fill verification, sellability/liquidity checks, wallet custody outside the app, hard daily-loss and exposure caps, kill switch, and a manual approval stage. Validate the strategy in paper mode first.


## Render demo deployment (paper mode)
A `render.yaml` and `start.sh` are included for a single free web service. Connect the GitHub repository to Render and choose Blueprint deployment, or create a Python web service with build command `pip install -r requirements.txt` and start command `bash start.sh`. Free Render services can sleep/restart and do not have persistent disks, so this is a demo only: scanner uptime and SQLite history are not guaranteed. A continuously running scanner requires a paid always-on service. See https://render.com/docs/free.
