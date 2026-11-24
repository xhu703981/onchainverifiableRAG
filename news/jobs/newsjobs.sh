#!/usr/bin/env bash
# Background jobs for the news registry (box only). Usage: newsjobs.sh start|stop|status
# crawl: every hour.  register: every 8 hours (3 batches/day) on Arc TESTNET.
set -u
NEWS_DATA=${NEWS_DATA:-/workspace/news-data}
REPO_NEWS=$(cd "$(dirname "$0")/.." && pwd)
PY="$NEWS_DATA/venv/bin/python -W ignore"
RUN="$NEWS_DATA/run"; LOG="$NEWS_DATA/logs"
mkdir -p "$RUN" "$LOG"
export NEWS_DATA PATH="$HOME/.foundry/bin:$PATH"

loop() {  # name interval_seconds initial_delay module
  local name=$1 every=$2 delay=$3 mod=$4
  sleep "$delay"
  while true; do
    echo "=== $(date -u +%FT%TZ) $name start" >> "$LOG/$name.log"
    (cd "$REPO_NEWS" && timeout 3000 $PY -m "$mod") >> "$LOG/$name.log" 2>&1
    echo "=== $(date -u +%FT%TZ) $name exit $?" >> "$LOG/$name.log"
    sleep "$every"
  done
}

case "${1:-status}" in
  start)
    for j in crawl register; do
      if [ -f "$RUN/$j.pid" ] && kill -0 "$(cat "$RUN/$j.pid")" 2>/dev/null; then echo "$j already running (pid $(cat "$RUN/$j.pid"))"; continue; fi
      if [ $j = crawl ]; then args="crawl 3600 ${CRAWL_DELAY:-0} newsreg.crawl"; else args="register 28800 ${REGISTER_DELAY:-28800} newsreg.register"; fi
      setsid nohup "$0" _loop $args > /dev/null 2>&1 < /dev/null &
      echo $! > "$RUN/$j.pid"; echo "$j started (pid $!)"
    done ;;
  _loop) shift; loop "$@" ;;
  stop)
    for j in crawl register; do
      if [ -f "$RUN/$j.pid" ]; then kill -- -"$(cat "$RUN/$j.pid")" 2>/dev/null || kill "$(cat "$RUN/$j.pid")" 2>/dev/null; rm -f "$RUN/$j.pid"; echo "$j stopped"; fi
    done ;;
  status)
    for j in crawl register; do
      if [ -f "$RUN/$j.pid" ] && kill -0 "$(cat "$RUN/$j.pid")" 2>/dev/null; then echo "$j running (pid $(cat "$RUN/$j.pid")), log $LOG/$j.log"; else echo "$j not running"; fi
    done ;;
esac
