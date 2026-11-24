#!/bin/sh
set -eu
mkdir -p /app
run_node() {
  port="$1"
  while :; do
    python3 /app/server.py "$port" || true
    sleep 1
  done
}
run_node 8000 &
p0=$!
run_node 8001 &
p1=$!
run_node 8002 &
p2=$!
while :; do
  python3 /app/server.py irc || true
  sleep 1
done &
pi=$!
trap 'kill "$p0" "$p1" "$p2" "$pi" 2>/dev/null || true; wait' INT TERM EXIT
wait
