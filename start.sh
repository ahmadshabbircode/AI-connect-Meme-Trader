#!/usr/bin/env bash
set -e
python scanner.py &
exec streamlit run dashboard.py --server.address 0.0.0.0 --server.port ${PORT:-10000}
