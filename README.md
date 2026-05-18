# minami-public-cron

南高OB会サイト ([minami-baseball-ob](https://github.com/yasumorishima/minami-baseball-ob), private / 技術解説 (public): [minami-baseball-ob-docs](https://github.com/yasumorishima/minami-baseball-ob-docs)) の **public 化可能な cron workflow** を分離した public repo。

## Purpose

- GitHub Actions の無料枠を public repo の unlimited 枠で消化、 private repo の quota 圧迫を回避
- single point of failure (RPi5 self-hosted runner) からの脱却
- RPi5 cron は defense-in-depth として並行稼働継続 (即削除しない、 例: keep-alive / daily-message は両側で動く設計)

## Workflows

| File | Schedule | 役割 |
|---|---|---|
| `warm-weather.yml` | `*/30 * * * *` | minami の `/weather` を HTTP GET で warm、 Vercel Data Cache を refresh |
| `keep-alive.yml` | `0 0 * * 0` (週次) | `/schedule` を HTTP GET して SSR 経由で Supabase fetch を起こし、 Free plan の 7 日無活動 auto-pause を回避 (anon key 不要) |
| `daily-message.yml` | 6 cron schedule | 「今日のひとこと」 API を call (morning/noon/night × 通常+補完) + /weather warm 補助 step |
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
