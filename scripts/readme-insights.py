#!/usr/bin/env python3
"""公開 docs README にサイトのアクセス状況（どこから来たかの割合と図 2 枚）を書き込む。

取得元は各サイトの /api/insights-summary（/insights ページと同じ GA4 集計値）。
閲覧数そのものは出さず、参照元（直接・検索・SNS・リンク経由・その他）の割合で見せる。
GA の鍵はここに置かない。図は SVG（ライト/ダーク）を docs リポジトリに置き、
README の insights-start 〜 insights-end の間だけを書き換える。
1 リポジトリにつき 1 日 1 commit（Git Data API でまとめて書く）。

使い方:
  python3 scripts/readme-insights.py                 # 両サイトを更新（GH_TOKEN 必須）
  python3 scripts/readme-insights.py --dry-run DIR [SITE=JSONFILE ...]
      書かずに DIR/<site>/ へ出力する。元にする README は DIR/<site>-README.md
"""
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request

JST = dt.timezone(dt.timedelta(hours=9))

SITES = [
    {
        "key": "funnies",
        "url": "https://yokohama-funnies.vercel.app",
        "repo": "yasumorishima/yokohama-funnies-docs",
        "data_start": None,
        "note": "",
    },
    {
        "key": "minami",
        "url": "https://minami-baseball-ob.vercel.app",
        "repo": "yasumorishima/minami-baseball-ob-docs",
        # 計測 ID の末尾改行で 2026-07-11 以前は GA に何も記録されていない
        "data_start": "2026-07-12",
        "note": "2026-07-11 以前は計測の不具合で記録がありません（グラフは 2026-07-12 から）。",
    },
]

START = "<!-- insights-start (auto-updated daily by GitHub Actions) -->"
END = "<!-- insights-end -->"
BLOCK_RE = re.compile(r"<!-- insights-start[^>]*-->.*?<!-- insights-end -->", re.S)
ANCHOR_RE = re.compile(r"^\*\*https://[^\n]*\n", re.M)

# GA4 の既定の分類（/insights と同じ元データ）を 5 つにまとめる。
# /insights が別に出すメール・広告などは「その他」に入れる。並びと色は固定
CHANNELS = ["直接アクセス", "検索", "SNS", "リンク経由", "その他"]
CHANNEL_OF = {
    "Direct": "直接アクセス",
    "Organic Search": "検索",
    "Organic Social": "SNS",
    "Social": "SNS",
    "Referral": "リンク経由",
}

# 配色は dataviz の参照パレット（スロット 1〜5）。validate_palette.js で両モード PASS
# （ライトの 3 色は面とのコントラスト 3:1 未満＝割合を必ず文字でも出す）
THEMES = {
    "light": {"bg": "#ffffff", "fg": "#1f2328", "sub": "#59636e", "grid": "#e5e7eb",
              "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]},
    "dark": {"bg": "#0d1117", "fg": "#e6edf3", "sub": "#9198a1", "grid": "#30363d",
             "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"]},
}
FONT = "-apple-system,Segoe UI,Hiragino Sans,Noto Sans JP,Yu Gothic,sans-serif"


class GateError(Exception):
    pass


def channel(label):
    return CHANNEL_OF.get(label, "その他")


# ---------- 取得と検査 ----------

def fetch(url):
    req = urllib.request.Request(url + "/api/insights-summary",
                                 headers={"User-Agent": "readme-insights"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def nonneg_int(v, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v != int(v):
        raise GateError(f"{what} が非負の整数でない: {v!r}")
    return int(v)


def check(data):
    t = (data.get("totals") or {}).get("last30") or {}
    s30 = nonneg_int(t.get("sessions"), "totals.last30.sessions")
    for k in ("channels", "monthlyChannels", "monthly", "devices"):
        if not isinstance(data.get(k), list):
            raise GateError(f"{k} が配列でない（サイト側が古い版のまま？）")
    if s30 == 0 or not data["channels"]:
        raise GateError("直近30日のセッションが 0（GA から何も取れていない）")
    for p in data["channels"] + data["monthlyChannels"] + data["devices"]:
        if not isinstance(p.get("label"), str) or not p["label"]:
            raise GateError(f"分類名が空: {p!r}")
        nonneg_int(p["value"], "value")
    csum = sum(p["value"] for p in data["channels"])
    if csum == 0:
        raise GateError("直近30日の参照元がすべて 0")
    if not close_enough(csum, s30):
        raise GateError(f"直近30日の参照元の和 {csum} がセッション合計 {s30} と一致しない")
    sessions = {}
    for p in data["monthly"]:
        if not isinstance(p.get("month"), str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", p["month"]):
            raise GateError(f"月の形式が違う: {p.get('month')!r}")
        if p["month"] in sessions:
            raise GateError(f"同じ月が 2 回ある: {p['month']}")
        sessions[p["month"]] = nonneg_int(p.get("sessions"), "monthly.sessions")
    by_month = {}
    for p in data["monthlyChannels"]:
        if p.get("month") not in sessions:
            raise GateError(f"月別の参照元に、月別の合計に無い月がある: {p.get('month')!r}")
        by_month[p["month"]] = by_month.get(p["month"], 0) + p["value"]
    for m, n in sessions.items():
        if not close_enough(by_month.get(m, 0), n):
            raise GateError(f"{m} の参照元の和 {by_month.get(m, 0)} がセッション {n} と一致しない")


def close_enough(part_sum, total):
    """参照元ごとの和とセッション合計の突き合わせ。
    途中で参照元が変わった訪問は両方の参照元に数えられるので、完全には一致しない
    （2026-10-08 実測: 232 対 231、60 対 59。8・9 月は一致）。数件のずれだけ許す。"""
    return abs(part_sum - total) <= max(2, 0.02 * total)


def shares(rows):
    """[(label, value)] → 固定順の {分類: 割合%}（合計 0 なら None）"""
    acc = dict.fromkeys(CHANNELS, 0)
    for label, v in rows:
        acc[channel(label)] += v
    total = sum(acc.values())
    if total == 0:
        return None
    return {k: 100 * v / total for k, v in acc.items()}


def fmt_pct(x):
    return f"{x:.0f}%" if x >= 1 or x == 0 else "1%未満"


# ---------- 図 ----------

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_open(W, H, c, title, subtitle=""):
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="{FONT}">',
         f'<rect width="{W}" height="{H}" rx="8" fill="{c["bg"]}"/>',
         f'<text x="24" y="38" font-size="20" font-weight="700" fill="{c["fg"]}">{esc(title)}</text>']
    if subtitle:
        o.append(f'<text x="24" y="64" font-size="14" fill="{c["sub"]}">{esc(subtitle)}</text>')
    return o


def hbar_svg(title, sh, theme):
    """直近30日の参照元の割合（大きい順の横棒・割合を文字でも出す）"""
    c = THEMES[theme]
    rows = sorted(CHANNELS, key=lambda k: -sh[k])
    W, top, rh = 880, 84, 44
    H = top + rh * len(rows) + 16
    L, R = 150, 90
    pw = W - L - R
    o = svg_open(W, H, c, title, "訪問（セッション）ごとに、どこから来たかを数えた割合")
    for i, k in enumerate(rows):
        y = top + rh * i
        col = c["series"][CHANNELS.index(k)]
        w = pw * sh[k] / 100
        o.append(f'<text x="{L - 14}" y="{y + 25}" font-size="15" text-anchor="end" '
                 f'fill="{c["fg"]}">{esc(k)}</text>')
        o.append(f'<rect x="{L}" y="{y + 8}" width="{pw}" height="26" rx="4" fill="{c["grid"]}" opacity="0.5"/>')
        if w > 0:
            o.append(f'<rect x="{L}" y="{y + 8}" width="{max(w, 4):.1f}" height="26" rx="4" '
                     f'fill="{col}"><title>{esc(k)}: {fmt_pct(sh[k])}</title></rect>')
        o.append(f'<text x="{L + pw + 12}" y="{y + 26}" font-size="15" font-weight="700" '
                 f'fill="{c["fg"]}">{fmt_pct(sh[k])}</text>')
    o.append("</svg>")
    return "\n".join(o) + "\n"


def stack_svg(title, subtitle, months, theme, partial):
    """月別の参照元の割合（100% 積み上げ縦棒・凡例つき・大きい区分は割合を文字でも出す）"""
    c = THEMES[theme]
    W, H = 880, 400
    L, R, T, B = 64, 20, 112, 44
    pw, ph = W - L - R, H - T - B
    o = svg_open(W, H, c, title, subtitle)
    # 凡例（色だけに頼らない）
    x = L
    for i, k in enumerate(CHANNELS):
        o.append(f'<rect x="{x}" y="80" width="14" height="14" rx="3" fill="{c["series"][i]}"/>')
        o.append(f'<text x="{x + 20}" y="92" font-size="14" fill="{c["fg"]}">{esc(k)}</text>')
        # 全角は 14px・半角は 8px で幅を見積もる（「SNS」の後ろだけ空かないように）
        x += 20 + sum(8 if ord(ch) < 0x80 else 14 for ch in k) + 26
    for p in (0, 50, 100):
        yy = T + ph - ph * p / 100
        o.append(f'<line x1="{L}" y1="{yy:.1f}" x2="{W - R}" y2="{yy:.1f}" stroke="{c["grid"]}"/>')
        o.append(f'<text x="{L - 10}" y="{yy + 5:.1f}" font-size="14" text-anchor="end" '
                 f'fill="{c["sub"]}">{p}%</text>')
    n = max(len(months), 1)
    slot = pw / n
    bw = min(slot * 0.62, 90)
    # 区分の上の割合の文字は暗い色（ダークの面の色でも各色と 4.5:1 を超える）
    ink = "#111111" if theme == "light" else "#0d1117"
    for j, (m, sh) in enumerate(months):
        x = L + slot * j + (slot - bw) / 2
        label = f"{m[:4]}/{int(m[5:])}" if j == 0 or m.endswith("-01") else f"{int(m[5:])}月"
        o.append(f'<text x="{x + bw / 2:.1f}" y="{H - 16}" font-size="14" text-anchor="middle" '
                 f'fill="{c["sub"]}">{esc(label)}</text>')
        if sh is None:
            continue
        op = ' opacity="0.55"' if j in partial else ""
        y = T + ph
        for i, k in enumerate(CHANNELS):
            h = ph * sh[k] / 100
            if h <= 0:
                continue
            y -= h
            # 区分の間に 2px の隙間（面の色）
            o.append(f'<rect x="{x:.1f}" y="{y + 1:.1f}" width="{bw:.1f}" height="{max(h - 2, 0.5):.1f}" '
                     f'fill="{c["series"][i]}"{op}><title>{esc(m)} {esc(k)}: {fmt_pct(sh[k])}</title></rect>')
            if h >= 22 and sh[k] >= 10:
                o.append(f'<text x="{x + bw / 2:.1f}" y="{y + h / 2 + 5:.1f}" font-size="14" '
                         f'text-anchor="middle" fill="{ink}">{sh[k]:.0f}%</text>')
    o.append("</svg>")
    return "\n".join(o) + "\n"


def month_series(data, site, today):
    """最初にセッションがある月（南高は data_start の月）から今月まで、最大 12 か月。"""
    sess = {p["month"]: p["sessions"] for p in data["monthly"] if p["sessions"] > 0}
    rows = {}
    for p in data["monthlyChannels"]:
        rows.setdefault(p["month"], []).append((p["label"], p["value"]))
    first = min(sess) if sess else f"{today.year:04d}-{today.month:02d}"
    if site["data_start"]:
        first = max(first, site["data_start"][:7])
    y, m = map(int, first.split("-"))
    out = []
    while (y, m) <= (today.year, today.month):
        k = f"{y:04d}-{m:02d}"
        out.append((k, shares(rows.get(k, []))))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out[-12:]


def share_title(sh):
    top = max(CHANNELS, key=lambda k: sh[k])
    return f"直近30日のアクセスは「{top}」からが最多（{fmt_pct(sh[top])}）"


MIN_SESSIONS_TO_COMPARE = 30


def monthly_title(months, partial, sessions):
    """最後の丸 1 か月と、その前の丸 1 か月で、いちばん動いた参照元を言う。
    訪問が少ない月どうしの割合の差は偶然で大きく振れるので、どちらかが
    MIN_SESSIONS_TO_COMPARE 未満なら比べずに素の題名にする。"""
    full = [(k, sh) for i, (k, sh) in enumerate(months) if i not in partial and sh]
    if len(full) < 2:
        return "月別の参照元の割合"
    (k0, s0), (k1, s1) = full[-2], full[-1]
    if min(sessions.get(k0, 0), sessions.get(k1, 0)) < MIN_SESSIONS_TO_COMPARE:
        return "月別の参照元の割合"
    ch = max(CHANNELS, key=lambda k: abs(s1[k] - s0[k]))
    d = s1[ch] - s0[ch]
    m0, m1 = int(k0[5:]), int(k1[5:])
    if abs(d) < 1:
        return f"{m1}月の参照元の割合は{m0}月とほぼ同じ"
    return f"{m1}月は「{ch}」からの割合が{m0}月より{abs(d):.0f}ポイント{'増えた' if d > 0 else '減った'}"


def render(data, site, today):
    sh30 = shares([(p["label"], p["value"]) for p in data["channels"]])
    months = month_series(data, site, today)
    partial = {len(months) - 1}  # 今月は途中
    notes = [f"{today.month}月は{today.day}日まで"]
    ds = site["data_start"]
    if ds and months and months[0][0] == ds[:7] and not ds.endswith("-01"):
        partial.add(0)  # 計測を始めた月も途中から
        notes.insert(0, f"{int(ds[5:7])}月は{int(ds[8:])}日から")
    sub = "・".join(notes) + "（色を抑えた棒）"
    files = {}
    for th in THEMES:
        files[f"insights/channels-{th}.svg"] = hbar_svg(share_title(sh30), sh30, th)
        files[f"insights/channels-monthly-{th}.svg"] = stack_svg(
            monthly_title(months, partial, {p["month"]: p["sessions"] for p in data["monthly"]}),
            sub, months, th, partial)
    return files, sh30


# ---------- README ----------

def picture(name, alt):
    return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="insights/{name}-dark.svg">'
            f'<img src="insights/{name}-light.svg" alt="{alt}" width="100%"></picture>')


def block(data, site, today, sh30):
    dev = {d["label"]: d["value"] for d in data["devices"]}
    dsum = sum(dev.values())
    mobile = fmt_pct(100 * dev.get("mobile", 0) / dsum) if dsum else "—"
    order = sorted(CHANNELS, key=lambda k: -sh30[k])
    return "\n".join([
        START,
        "",
        "### どこから見に来ているか",
        "",
        "| " + " | ".join(order) + " | スマホから |",
        "|" + "---:|" * (len(order) + 1),
        "| " + " | ".join(fmt_pct(sh30[k]) for k in order) + f" | {mobile} |",
        "",
        "<sub>直近30日の割合。参照元は訪問（セッション）ごと、スマホは閲覧ごとに数えています。</sub>",
        "",
        picture("channels", "直近30日の参照元の割合"),
        "",
        picture("channels-monthly", "月別の参照元の割合"),
        "",
        f"<sub>最終更新 {today.isoformat()}（JST）・Google Analytics 4 の集計（サイトの "
        f"[アクセス解析ページ]({site['url']}/insights) と同じデータの参照元を 5 つにまとめたもの）を GitHub Actions が毎日取得。"
        f"{site['note']}</sub>",
        "",
        END,
    ])


def splice(readme, new_block):
    n_block = len(BLOCK_RE.findall(readme))
    if n_block > 1:
        raise GateError("README に insights ブロックが 2 つ以上ある")
    if readme.count("<!-- insights-start") != n_block or readme.count(END) != n_block:
        raise GateError("README の insights の始まりと終わりの印が対になっていない")
    if n_block:
        return BLOCK_RE.sub(lambda _: new_block, readme, count=1)
    m = ANCHOR_RE.search(readme)
    if not m:
        raise GateError("README に挿入位置（**https://… の行）が無い")
    return readme[:m.end()] + "\n" + new_block + "\n" + readme[m.end():]


# ---------- GitHub ----------

# 以前の版が置いた閲覧数の図（あれば同じ commit で消す。これ以外は消さない）
RETIRED = ["insights/daily-light.svg", "insights/daily-dark.svg",
           "insights/monthly-light.svg", "insights/monthly-dark.svg"]


def gh(args, payload=None):
    cmd = ["gh", "api", *args] + (["--input", "-"] if payload is not None else [])
    r = subprocess.run(cmd, input=json.dumps(payload) if payload is not None else None,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh api {args[-1]}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def repo_head(repo):
    branch = gh([f"repos/{repo}"])["default_branch"]
    return branch, gh([f"repos/{repo}/git/ref/heads/{branch}"])["object"]["sha"]


def read_readme(repo, ref):
    r = subprocess.run(["gh", "api", f"repos/{repo}/contents/README.md?ref={ref}",
                        "-H", "Accept: application/vnd.github.raw"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"{repo} README 読み込み失敗: {r.stderr.decode()[:200]}")
    return r.stdout.decode("utf-8")


def commit_files(repo, branch, head, files, message):
    """head の上に全ファイルを 1 commit で書く。中身が変わらなければ何もしない。"""
    base_tree = gh([f"repos/{repo}/git/commits/{head}"])["tree"]["sha"]
    existing = {e["path"] for e in gh([f"repos/{repo}/git/trees/{base_tree}?recursive=1"])["tree"]}
    tree = []
    for path, text in files.items():
        blob = gh([f"repos/{repo}/git/blobs"],
                  {"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"})
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    for path in RETIRED:
        if path in existing:
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
    new_tree = gh([f"repos/{repo}/git/trees"], {"base_tree": base_tree, "tree": tree})["sha"]
    if new_tree == base_tree:
        return None
    c = gh([f"repos/{repo}/git/commits"], {"message": message, "tree": new_tree, "parents": [head]})
    # force なしの更新＝その間に別の commit が入っていたら失敗する（上書きしない）
    gh(["-X", "PATCH", f"repos/{repo}/git/refs/heads/{branch}"], {"sha": c["sha"]})
    return c["sha"]


def main():
    argv = sys.argv[1:]
    dry, local_json = None, {}
    if argv[:1] == ["--dry-run"]:
        dry = argv[1]
        local_json = dict(a.split("=", 1) for a in argv[2:])
    today = dt.datetime.now(JST).date()
    failed = []
    for site in SITES:
        try:
            if site["key"] in local_json:
                with open(local_json[site["key"]], encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = fetch(site["url"])
            check(data)
            files, sh30 = render(data, site, today)
            if dry:
                with open(os.path.join(dry, f"{site['key']}-README.md"), encoding="utf-8") as f:
                    old = f.read()
            else:
                branch, head = repo_head(site["repo"])
                old = read_readme(site["repo"], head)
            files["README.md"] = splice(old, block(data, site, today, sh30))
            if dry:
                for p, text in files.items():
                    out = os.path.join(dry, site["key"], p)
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    with open(out, "w", encoding="utf-8") as f:
                        f.write(text)
                print(f"{site['key']}: wrote {len(files)} files")
                continue
            sha = commit_files(site["repo"], branch, head, files, "docs: auto-update site access stats")
            print(f"{site['key']}: " + (f"committed {sha[:7]}" if sha else "no change"))
        except Exception as e:  # 片方が落ちてももう片方は書く。最後に赤で終わる
            print(f"::error::{site['key']}: {type(e).__name__}: {e}")
            failed.append(site["key"])
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
