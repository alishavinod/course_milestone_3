#!/bin/sh
mkdir -p /tmp
[ -f /tmp/huddle_state.json ] || printf '%s\n' '{"users":{},"tokens":{},"workspaces":{},"channels":{},"messages":{},"members":{},"reactions":{},"pins":{},"files":{},"dms":{}}' > /tmp/huddle_state.json
python3 -u /app/server.py &
P0=$!
PORT=8001 NODE_ID=1 python3 -u /app/server.py &
P1=$!
PORT=8002 NODE_ID=2 python3 -u /app/server.py &
P2=$!
python3 -u /app/irc.py &
PI=$!
trap 'kill "$P0" "$P1" "$P2" "$PI" 2>/dev/null; wait' TERM INT EXIT
wait "$P0" "$P1" "$P2" "$PI"
