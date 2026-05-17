# minami-public-cron

南高OB会サイト ([minami-baseball-ob](https://github.com/yasumorishima/minami-baseball-ob), private / 技術解説 (public): [minami-baseball-ob-docs](https://github.com/yasumorishima/minami-baseball-ob-docs)) の **public 化可能な cron workflow** を分離した public repo。

## Purpose

- GitHub Actions の無料枠を public repo の unlimited 枠で消化、 private repo の quota 圧迫を回避
- single point of failure (RPi5 self-hosted runner) からの脱却
- RPi5 cron は defense-in-depth として並行稼働継続 (即削除しない)

## Workflows

| File | Schedule | 役割 |
|---|---|---|
| `warm-weather.yml` | `*/30 * * * *` | minami の `/weather` を HTTP GET で warm、 Vercel Data Cache を refresh |
| `keep-alive.yml` | `0 0 * * 0` (週次) | `/schedule` を HTTP GET して SSR 経由で Supabase fetch を起こし、 Free plan の 7 日無活動 auto-pause を回避 (anon key 不要) |
| `daily-message.yml` | 6 cron schedule | 「今日のひとこと」 API を call (morning/noon/night × 通常+補完) + /weather warm 補助 step |

両方とも `runs-on: ubuntu-latest` で public 無料枠運用。

## Required GitHub Secrets

| Name | 必須 | 値 |
|---|---|---|
| `VERCEL_APP_URL` | ✅ | minami の deployed URL (例: `https://minami-baseball-ob.vercel.app`) |
| `CRON_SECRET` | ✅ | minami `/api/cron/*` の Bearer auth token |

Settings → Secrets and variables → Actions → New repository secret から設定。 value は minami private repo の同名 secret と同じ。

## What's NOT here

- ❌ OB 名簿 / 会員情報 / Supabase auth code
- ❌ 写真 / 名前 / 個人情報
- ❌ 認証付き API endpoint の実装 (API は minami 私 repo 側、 本 repo は呼び出すのみ)
