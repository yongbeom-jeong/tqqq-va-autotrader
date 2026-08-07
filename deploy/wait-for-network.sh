#!/bin/sh
set -eu

HOST="${1:-openapi.tossinvest.com}"
ATTEMPTS="${NETWORK_WAIT_ATTEMPTS:-30}"
INTERVAL="${NETWORK_WAIT_INTERVAL_SECONDS:-10}"

i=1
while [ "$i" -le "$ATTEMPTS" ]; do
  if getent hosts "$HOST" >/dev/null 2>&1; then
    echo "Network preflight: DNS resolved $HOST"
    exit 0
  fi
  echo "Network preflight: waiting for DNS ($i/$ATTEMPTS)"
  sleep "$INTERVAL"
  i=$((i + 1))
done

echo "Network preflight failed: could not resolve $HOST" >&2
exit 1

