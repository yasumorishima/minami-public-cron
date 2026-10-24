# CLAUDE.md — minami-public-cron

南高 OB 会のサイト（private の `yasumorishima/minami-baseball-ob`）のための GitHub Actions だけを置く public リポジトリです。
**運用の正本は private 側の `CLAUDE.md`**。ここには、この repo を触るときに要ることだけを書きます。

## 守ること
- **Actions のログは誰でも読める**。氏名・メールなど個人の情報が出る処理を置かない。ログに出すのは UID の先頭 8 桁と期まで。`client_payload` の個人情報を step の `env:` に入れない（ログの見出しに平文で出る）。
- 秘密は Secrets だけ。値を `echo` しない（トークンは `::add-mask::`）。
- `health-check.yml` は **CRLF**（他は LF）。書き換えで全行の差分にしない。
- `member-request.yml` は申請者の氏名とメールをイベントのファイルから読み、ジョブの最初に `::add-mask::` で登録している（2026-10-24 まで env に入れていた）。この形を崩さない。
- 監視（`health-check.yml` / `scripts/`）は `yokohama-funnies-public-cron` に同じ写しがある＝**片方だけ直さない**。
- `check-current-team.yml` は `disabled_manually`＝有効化しない（本番は private 側。両方動くと PR が二重に立つ）。
- README の Workflows 表は自動では更新されない。workflow を足したら表も直す。

## workflow
| file | 起動 | 役割 |
|---|---|---|
| `member-request.yml` | `repository_dispatch` | 入会申請から private repo に `config/members/<uid>.yml` の PR を作り、`user_roles` の承認待ち行（氏名・期）を書く |
| `sync-roles.yml` | 5 分おき・手動・dispatch | private repo の `config/members*.yml` を読んで role を同期。承認メールも |
| `health-check.yml` / `-ack.yml` | 毎時 / dispatch | 会員パイプラインの監視（異常時だけ private repo に Issue とメール） |
| `daily-message.yml` | 30 分おき | 「今日のひとこと」の API を呼ぶ |
| `keep-alive.yml` / `purge-deleted-photos.yml` / `warm-weather.yml` | 週 1 / 毎日 / 30 分おき | 休止防止 / 論理削除 7 日後の物理削除 / 天気キャッシュ |
| `update-readme-stats.yml` / `update-readme-insights.yml` | 毎月 1 日 / 毎日 | README の統計 / 公開 docs の参照元の図 |

## sync-roles の即時起動
- 会員承認の直後の即時同期は、private の実行役 `yasumorishima/site-ops` が `workflow_dispatch` で起動する（cloud セッションから直接は起動できない）。
- ⛔ `push` などブランチ上で動く起動条件を足さない（2026-10-24 にいったん足して戻した）。
- ⚠️ ただし、それだけでは鍵は守れない。cloud セッションがこの repo につながれていれば、ブランチに workflow を1 本足すだけで repo の Secrets（Supabase の service role キー・GitHub App の秘密鍵）を読める。鍵の置き場所の見直しは 2026-10-24 時点で未了。

## Session protocol
- 返答は日本語・です/ます。走らせていないこと・確かめていないことは書かない。
- 変更は PR で出し、checks を見てから自分で merge する。終わったら private 側 `CLAUDE.md` の Progress log に 1 行書く。
