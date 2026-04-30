#!/bin/bash
# Watchdog: waits for Phase A (1y fetch) to finish on the shared VM,
# then automatically launches Phase B (15y backfill) in a new container.
#
# Run on the VM with nohup so it survives SSH disconnect:
#   nohup /opt/trading-bot/scripts/watchdog_phase_b.sh > /dev/null 2>&1 & disown

set -euo pipefail

LOG=/opt/trading-bot/data/logs/watchdog.log
mkdir -p "$(dirname "$LOG")"

log() { echo "$(date -Iseconds) WATCHDOG: $*" | tee -a "$LOG"; }

log "started, waiting for trading-bot-fetch (Phase A) to exit..."

# Poll until Phase A container is no longer running
while docker ps --format '{{.Names}}' | grep -q '^trading-bot-fetch$'; do
    sleep 60
done

log "Phase A container exited"

# Capture Phase A summary before removing
{
    echo "----- Phase A final log -----"
    docker logs trading-bot-fetch 2>&1 | tail -30
    echo "----- end -----"
} >> "$LOG"

docker rm trading-bot-fetch 2>/dev/null || true
log "Phase A cleaned up, starting Phase B (15-year chunked backfill)..."

# Phase B: 15-year backfill via chunked requests
docker run -d --name trading-bot-fetch-15y \
    --network container:ib-gateway \
    -v /tmp/fetch_15y.py:/fetch.py:ro \
    -v /opt/trading-bot/data:/data:rw \
    python:3.12-slim \
    bash -c 'pip install -q ib_async pandas pyarrow && python /fetch.py' >> "$LOG" 2>&1

log "Phase B container started, exiting watchdog"
