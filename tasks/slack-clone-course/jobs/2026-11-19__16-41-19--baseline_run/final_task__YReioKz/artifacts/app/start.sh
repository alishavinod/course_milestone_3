#!/bin/sh
set -eu
mkdir -p /app
python3 /app/app.py 8000 &
cd /app && python3 -c 'import app; app.run_irc()' &
pirc=$!
p1=$!
python3 /app/app.py 8001 &
p2=$!
python3 /app/app.py 8002 &
p3=$!
trap 'kill $p1 $p2 $p3 $pirc 2>/dev/null || true' EXIT INT TERM
wait
