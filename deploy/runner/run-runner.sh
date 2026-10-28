#!/usr/bin/env bash
# Runner entrypoint for Linux servers: non-headless Chrome on a private Xvfb display.
# Usage: run-runner.sh <port> <data-dir> <jar-path>
set -euo pipefail

PORT="${1:?port}"
DATA_DIR="${2:?data dir}"
JAR="${3:?jar path}"

DISPLAY_NUM="${XVFB_DISPLAY:-$((100 + PORT % 1000))}"
export DISPLAY=":${DISPLAY_NUM}"

mkdir -p "${DATA_DIR}/logs"

if ! pgrep -f "Xvfb ${DISPLAY} " >/dev/null 2>&1; then
  Xvfb "${DISPLAY}" -screen 0 1440x900x24 -nolisten tcp -ac >"${DATA_DIR}/logs/xvfb.log" 2>&1 &
  XVFB_PID=$!
  for _ in $(seq 1 50); do
    [ -S "/tmp/.X11-unix/X${DISPLAY_NUM}" ] && break
    sleep 0.1
  done
fi

java ${JAVA_OPTS:-} \
  -Dplaywright.cli.dir="${PLAYWRIGHT_CLI_DIR:?PLAYWRIGHT_CLI_DIR is required}" \
  -Dfile.encoding=UTF-8 \
  -Dsun.stdout.encoding=UTF-8 \
  -Dsun.stderr.encoding=UTF-8 \
  -jar "${JAR}" \
  --server.port="${PORT}" \
  --server.address=127.0.0.1 &
JAVA_PID=$!

cleanup() {
  kill "${JAVA_PID}" 2>/dev/null || true
  if [ -n "${XVFB_PID:-}" ]; then
    kill "${XVFB_PID}" 2>/dev/null || true
  fi
}
trap cleanup TERM INT EXIT

wait "${JAVA_PID}"