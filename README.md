# minami-public-cron

南高OB会サイト ([minami-baseball-ob](https://github.com/yasumorishima/minami-baseball-ob), private / 技術解説 (public): [minami-baseball-ob-docs](https://github.com/yasumorishima/minami-baseball-ob-docs)) の **public 化可能な cron workflow** を分離した public repo。

## Purpose

- GitHub Actions の無料枠を public repo の unlimited 枠で消化、 private repo の quota 圧迫を回避
- single point of failure (RPi5 self-hosted runner) からの脱却
- RPi5 cron は defense-in-depth として並行稼働継続。 **daily-message は 2026-05-21 から public-cron 側が primary** (private repo の self-hosted 版は disabled)、 GHA scheduler 遅延 (noon 2h+ 遅延でサイト朝停滞事案) 対策で `*/30` polling に切替。 さらに **RPi5 cron `*/30 * * * *` polling を redundant path として並行稼働** (API 冪等性で重複生成なし、 GHA scheduler が極端遅延した日でも RPi5 が拾う)

## Workflows

| File | Schedule | 役割 |
|---|---|---|
| `warm-weather.yml` | `*/30 * * * *` | minami の `/weather` を HTTP GET で warm、 Vercel Data Cache を refresh |
| `keep-alive.yml` | `0 0 * * 0` (週次) | `/schedule` を HTTP GET して SSR 経由で Supabase fetch を起こし、 Free plan の 7 日無活動 auto-pause を回避 (anon key 不要) |
| `purge-deleted-photos.yml` | `0 19 * * *` (毎日 JST 4:00) + dispatch | soft-delete >7日の photos を Supabase Storage+DB から物理削除 + content テーブル purge (2026-05-30 private repo から移行) |
| `check-current-team.yml` | `0 10 * * 1` (週次月曜) + dispatch | App token で minami-baseball-ob を clone し Playwright で新着試合を scrape → Supabase 登録 → private repo に issue 通知 (2026-05-30 移行) |
| `daily-message.yml` | `*/30 * * * *` (polling) | 「今日のひとこと」 API を call。 JST 時刻から slot 自動判定 (06-12=morning / 12-18=noon / 18-24=night / 00-06=skip)、 API 冪等性で既存 slot は HTTP 200 skipped、 各 slot 最終 30 分 (11:30/17:30/23:30 JST) は `is_backfill=true` で失敗時 email 通知。 **RPi5 cron が同一 endpoint を redundant に call** (GHA scheduler 障害時の fallback) |
| `update-readme-stats.yml` | `0 0 1 * *` (毎月1日 09:00 JST) | minami-baseball-ob を `DOCS_SYNC_PAT` で checkout して file/Supabase 計測、 3 repo (minami-baseball-ob / minami-baseball-ob-docs / yasumorishima profile) の `<!--stat:KEY-->...<!--/stat-->` と `<!--ob:KEY-->...<!--/ob-->` marker を auto-update |

全 `runs-on: ubuntu-latest` で public 無料枠運用。

## Required GitHub Secrets

| Name | 必須 | 値 / 取得元 |
|---|---|---|
| `VERCEL_APP_URL` | ✅ | minami の deployed URL (例: `https://minami-baseball-ob.vercel.app`) |
| `CRON_SECRET` | ✅ | minami `/api/cron/*` の Bearer auth token (= minami private repo の同名 secret と同じ) |
| `SUPABASE_URL` | ✅ (stats のみ) | minami の Supabase project URL (例: `https://xxxxx.supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ (stats のみ) | Supabase の new key format `sb_secret_*` (legacy JWT は 2026-04-20 Vercel rotation 以降 revoked、 management API `/v1/projects/<ref>/api-keys?reveal=true` で取得) |
| `DOCS_SYNC_PAT` | ✅ (stats のみ) | minami-baseball-ob (private) を clone + 3 repo README を PUT する PAT (classic `repo` scope) |

Settings → Secrets and variables → Actions → New repository secret から設定。

## What's NOT here

- ❌ OB 名簿 / 会員情報 / 会員管理 logic
- ❌ 写真 / 名前 / 個人情報
- ❌ 認証付き API endpoint の実装 (API は minami private repo 側、 本 repo は呼び出すのみ)

`update-readme-stats.yml` は Supabase REST で **集計のみ取得** (`history_matches` の row count / `user_roles` where role≠viewer の count) し、 個人情報は読み取らない / 書き込まない。 `SUPABASE_SERVICE_ROLE_KEY` を持つが用途は read-only 集計に限定。
