#!/usr/bin/env bash
# Supabase の件数だけを取り、数字（取れなければ KEEP）を $GITHUB_OUTPUT に書く。
# 鍵（SUPABASE_SERVICE_ROLE_KEY）を使うのはこの script だけ。サイト repo の clone のファイルには一切触れない
# （clone の中身は cloud セッションが書き換えられる＝鍵を持つ step で読むと鍵が漏れる。2026-10-25）。
set -euo pipefail

fetch_count() {
  local table="$1" filter="${2:-}"
  if [ -z "${SUPABASE_URL:-}" ] || [ -z "${SUPABASE_SERVICE_ROLE_KEY:-}" ]; then
    echo "DEBUG SUPABASE secrets missing" >&2; echo KEEP; return
  fi
  local url="$SUPABASE_URL/rest/v1/$table?select=id"
  [ -n "$filter" ] && url="$url&$filter"
  local resp http_code count
  resp=$(curl --max-time 60 -sS -D - -o /dev/null -w 'HTTP_STATUS:%{http_code}\n' \
    "$url" \
    -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
    -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
    -H "Prefer: count=exact" \
    -H "Range: 0-0" 2>&1) || { echo "DEBUG $table curl failed" >&2; echo KEEP; return; }
  http_code=$(printf '%s' "$resp" | grep '^HTTP_STATUS:' | sed 's/HTTP_STATUS://' || true)
  count=$(printf '%s' "$resp" | grep -i '^content-range:' | sed -E 's/.*\///' | tr -d '\r\n ' || true)
  echo "DEBUG $table http=$http_code count=$count" >&2
  if [ -z "$http_code" ] || [ "$http_code" -lt 200 ] || [ "$http_code" -ge 300 ]; then
    echo KEEP
  elif ! [[ "$count" =~ ^[0-9]+$ ]]; then
    echo KEEP
  else
    echo "$count"
  fi
}

emit() {  # emit <output 名> <table> <filter>
  local v; v=$(fetch_count "$2" "$3")
  echo "$1=$v"
  if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "$1=$v" >> "$GITHUB_OUTPUT"; fi
}

emit senseki history_matches ""
emit active_users user_roles "role=neq.viewer"
