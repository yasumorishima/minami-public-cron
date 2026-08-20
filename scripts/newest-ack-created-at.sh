#!/usr/bin/env bash
# usage: scripts/newest-ack-created-at.sh <owner> <repo> <display_title>
#
# Health Check Ack (.github/workflows/health-check-ack.yml) の run のうち、この 1 回の照会で
# 見えた中で最も新しい created_at (ISO8601) を 1 行返す。何も見えなければ何も出力せず exit 0。
#
# Actions の runs 一覧 API は古いキャッシュページを返すことがある。2026-08-19 の minami の
# 3 連続赤 (07:46 / 13:50 / 15:38 UTC) では dispatch の 3 秒後に ack が success していたのに、
# 同じ job 内の 3 回の取得が 07-08 -> 07-22 -> 07-11 と毎回別の古い run を「最新」と返した。
# 「間を空けて引き直す」だけでは効かない (引き直すたびに別の stale shard を引くだけ) ので、
#   1. per_page 10 -> 50
#   2. workflow 単位 (.../workflows/health-check-ack.yml/runs) と repo 単位 (.../actions/runs)
#      の 2 エンドポイントを併用する (キャッシュ系統が別)
# の 2 点で当たりを増やす。呼び出し側は複数回呼んで最大値を採ること。
# chain が本当に切れていればどの経路でも新しい ack は出てこないので検知感度は落ちない。
set -uo pipefail

owner="$1"
repo="$2"
export TITLE="$3"

{
  gh api "repos/${owner}/${repo}/actions/workflows/health-check-ack.yml/runs?event=repository_dispatch&per_page=50" \
    --jq '[.workflow_runs[] | select(.display_title==env.TITLE)] | max_by(.created_at) | .created_at' 2>/dev/null || true
  gh api "repos/${owner}/${repo}/actions/runs?event=repository_dispatch&per_page=50" \
    --jq '[.workflow_runs[] | select(.name=="Health Check Ack" and .display_title==env.TITLE)] | max_by(.created_at) | .created_at' 2>/dev/null || true
# API が 404/5xx を返すと本文 (JSON) が混ざることがある。ISO8601 の行だけ通し、
# 壊れた値を呼び出し側の date に渡さない (渡すと set -e で step ごと落ちて誤検知になる)。
} | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' | sort | tail -1 || true
exit 0
