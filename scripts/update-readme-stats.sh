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

# Supabase の件数は前の step（scripts/count-db-rows.sh）が取って env で渡す。この script は鍵を使わない
SENSEKI="${SENSEKI:-KEEP}"; [[ "$SENSEKI" =~ ^[0-9]+$ ]] || SENSEKI=KEEP
ACTIVE_USERS="${ACTIVE_USERS:-KEEP}"; [[ "$ACTIVE_USERS" =~ ^[0-9]+$ ]] || ACTIVE_USERS=KEEP

TS_FILES=$(find app components lib -name '*.ts' -o -name '*.tsx' | wc -l | tr -d ' ')
LOC=$(find app components lib \( -name '*.ts' -o -name '*.tsx' \) -exec cat {} + | wc -l | tr -d ' ')
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

# --- 置換関数（KEEP の場合はスキップ）---
replace_stat() {
  local key="$1" value="$2" file="$3"
  if [ "$value" = "KEEP" ]; then
    echo "  $key: KEEP (skip)"
    return
  fi
  sed -i "s/<!--stat:${key}-->[^<]*<!--\/stat-->/<!--stat:${key}-->${value}<!--\/stat-->/g" "$file"
}

# README.md が symlink（や通常のファイル以外）なら止まる。sed -i はリンク先を読んで普通のファイルに書き戻すので、
# `README.md -> .git/config` や `-> /proc/self/environ` を commit されると、トークンや環境変数が README に入って push される。
if [ -L README.md ] || { [ -e README.md ] && [ ! -f README.md ]; }; then
  echo "::error::README.md が通常のファイルではありません（symlink 等）。書き換えません" >&2
  exit 1
fi

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

# --- GitHub Actions output (profile sync 用、KEEP も含めて出力)---
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
