#!/usr/bin/env bash
set -e

python -m app.scanner &

exec streamlit run dashboard.py \
  --server.address 0.0.0.0 \
  --server.port ${PORT:-10000}
