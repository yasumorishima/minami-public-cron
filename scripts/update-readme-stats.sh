#!/usr/bin/env bash
# README の <!--stat:KEY-->...<!--/stat--> マーカーを実測値で自動置換
# GitHub Actions 経由で minami-baseball-ob を checkout した dir で実行
# Usage: bash update-readme-stats.sh <target_dir>
set -euo pipefail

TARGET="${1:-.}"
cd "$TARGET"

# --- 計測 ---
PAGES=$(find app -name 'page.tsx' | wc -l | tr -d ' ')
APIS=$(find app/api -name 'route.ts' 2>/dev/null | wc -l | tr -d ' ')
COMPONENTS=$(find components -name '*.tsx' | wc -l | tr -d ' ')
MIGRATIONS=$(ls supabase/migrations/*.sql 2>/dev/null | wc -l | tr -d ' ')
WORKFLOWS=$(ls .github/workflows/*.yml 2>/dev/null | wc -l | tr -d ' ')
E2E_TESTS=$(find e2e -name '*.spec.ts' 2>/dev/null | wc -l | tr -d ' ')

# Supabase REST で計測 (history_matches / user_roles) — HTTP code 明示 + max-time 60
fetch_count() {
  local table="$1" filter="${2:-}"
  local url="$SUPABASE_URL/rest/v1/$table?select=id"
  [ -n "$filter" ] && url="$url&$filter"
  local resp http_code count
  resp=$(curl --max-time 60 -sS -D - -o /dev/null -w 'HTTP_STATUS:%{http_code}\n' \
    "$url" \
    -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
    -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
    -H "Prefer: count=exact" \
    -H "Range: 0-0" 2>&1) || { echo "DEBUG $table curl failed" >&2; echo 0; return; }
  http_code=$(printf '%s' "$resp" | grep '^HTTP_STATUS:' | sed 's/HTTP_STATUS://')
  count=$(printf '%s' "$resp" | grep -i '^content-range:' | sed -E 's/.*\///' | tr -d '\r\n ')
  echo "DEBUG $table http=$http_code count=$count" >&2
  if [ -z "$count" ] || ! [[ "$count" =~ ^[0-9]+$ ]]; then
    echo 0
  else
    echo "$count"
  fi
}

if [ -n "${SUPABASE_URL:-}" ] && [ -n "${SUPABASE_SERVICE_ROLE_KEY:-}" ]; then
  SENSEKI=$(fetch_count history_matches)
  ACTIVE_USERS=$(fetch_count user_roles 'role=neq.viewer')
else
  echo "DEBUG SUPABASE secrets missing" >&2
  SENSEKI=0
  ACTIVE_USERS=0
fi

TS_FILES=$(find app components lib -name '*.ts' -o -name '*.tsx' | wc -l | tr -d ' ')
LOC=$(find app components lib -name '*.ts' -o -name '*.tsx' -exec cat {} + | wc -l | tr -d ' ')
LOC_APPROX=$(( (LOC / 100) * 100 ))

# テーブル数: CREATE TABLE - DROP TABLE
CREATED_MAIN=$(grep -rhi 'create table' supabase/migrations/ \
  | grep -iv '_history' | grep -iv 'storage\.' | grep -iv 'supabase_migrations\.' \
  | sed -E 's/.*create table( if not exists)?[ ]+(public\.)?//i' \
  | sed 's/[( ].*//' | sort -u)
DROPPED=$(grep -rhi 'drop table' supabase/migrations/ \
  | sed -E 's/.*drop table( if exists)?[ ]+(public\.)?//i' \
  | sed 's/[; ].*//' | sort -u)
TABLES_MAIN=$(comm -23 <(echo "$CREATED_MAIN") <(echo "$DROPPED") | grep -iv '_history' | wc -l | tr -d ' ')
TABLES_HISTORY=$(comm -23 \
  <(grep -rhi 'create table' supabase/migrations/ | grep -i '_history' \
    | sed -E 's/.*create table( if not exists)?[ ]+(public\.)?//i' \
    | sed 's/[( ].*//' | sort -u) \
  <(echo "$DROPPED") \
  | wc -l | tr -d ' ')
TABLES_ALL=$((TABLES_MAIN + TABLES_HISTORY))

echo "pages=$PAGES apis=$APIS components=$COMPONENTS migrations=$MIGRATIONS"
echo "workflows=$WORKFLOWS senseki=$SENSEKI ts_files=$TS_FILES loc=~$LOC_APPROX"
echo "tables_main=$TABLES_MAIN tables_history=$TABLES_HISTORY tables_all=$TABLES_ALL"
echo "e2e_tests=$E2E_TESTS active_users=$ACTIVE_USERS"

# --- 置換関数 ---
replace_stat() {
  local key="$1" value="$2" file="$3"
  sed -i "s/<!--stat:${key}-->[^<]*<!--\/stat-->/<!--stat:${key}-->${value}<!--\/stat-->/g" "$file"
}

# --- ob-source/README.md 更新 (既存 markers) ---
for readme in README.md; do
  [ -f "$readme" ] || continue
  replace_stat pages       "$PAGES"             "$readme"
  replace_stat apis        "$APIS"              "$readme"
  replace_stat components  "$COMPONENTS"        "$readme"
  replace_stat migrations  "$MIGRATIONS"        "$readme"
  replace_stat workflows   "$WORKFLOWS"         "$readme"
  replace_stat senseki     "$SENSEKI"           "$readme"
  replace_stat ts_files    "$TS_FILES"          "$readme"
  replace_stat loc         "~${LOC_APPROX}"     "$readme"
  replace_stat tables_main "$TABLES_MAIN"       "$readme"
  replace_stat tables_hist "$TABLES_HISTORY"    "$readme"
  replace_stat tables_all  "$TABLES_ALL"        "$readme"
  echo "Updated $readme"
done

# --- GitHub Actions output (profile sync 用) ---
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "pages=$PAGES"
    echo "apis=$APIS"
    echo "components=$COMPONENTS"
    echo "migrations=$MIGRATIONS"
    echo "workflows=$WORKFLOWS"
    echo "senseki=$SENSEKI"
    echo "ts_files=$TS_FILES"
    echo "loc=~$LOC_APPROX"
    echo "tables_main=$TABLES_MAIN"
    echo "tables_hist=$TABLES_HISTORY"
    echo "tables_all=$TABLES_ALL"
    echo "e2e_tests=$E2E_TESTS"
    echo "active_users=$ACTIVE_USERS"
    echo "cost=¥0"
  } >> "$GITHUB_OUTPUT"
fi
