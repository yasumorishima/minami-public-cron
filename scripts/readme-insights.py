#!/usr/bin/env python3
"""公開 docs README にサイトのアクセス状況（数字の行と図 2 枚）を書き込む。

取得元は各サイトの /api/insights-summary（/insights ページと同じ GA4 集計値）。
GA の鍵はここに置かない。図は SVG（ライト/ダーク）を docs リポジトリに置き、
README の insights-start 〜 insights-end の間だけを書き換える。
1 リポジトリにつき 1 日 1 commit（Git Data API でまとめて書く）。

使い方:
  python3 scripts/readme-insights.py                 # 両サイトを更新（GH_TOKEN 必須）
  python3 scripts/readme-insights.py --dry-run DIR [SITE=JSONFILE ...]
      書かずに DIR/<site>/ へ出力する。DIR/<site>-README.md があればそれを元にする
"""
import base64
import datetime as dt
import json
import math
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
TOTAL_RE = re.compile(r"<!--ins:total-->([0-9,]+)<!--/ins-->")

THEMES = {
    "light": {"bg": "#ffffff", "fg": "#1f2328", "sub": "#59636e", "grid": "#e5e7eb",
              "bar": "#2563eb", "bar2": "#93b4f5"},
    "dark": {"bg": "#0d1117", "fg": "#e6edf3", "sub": "#9198a1", "grid": "#30363d",
             "bar": "#58a6ff", "bar2": "#2a4a70"},
}
FONT = "-apple-system,Segoe UI,Hiragino Sans,Noto Sans JP,Yu Gothic,sans-serif"


class GateError(Exception):
    pass


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


def check(data, today):
    t = data.get("totals") or {}
    for k in ("last30", "allTime"):
        for m in ("views", "users"):
            nonneg_int((t.get(k) or {}).get(m), f"totals.{k}.{m}")
    if t["allTime"]["views"] < t["last30"]["views"]:
        raise GateError("累計が直近30日より小さい")
    for k in ("daily", "monthly", "devices"):
        if not isinstance(data.get(k), list):
            raise GateError(f"{k} が配列でない")
    if not data["daily"] or not data["monthly"]:
        raise GateError("日別か月別が空（GA から何も取れていない）")
    dates = []
    for p in data["daily"]:
        if not isinstance(p.get("date"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p["date"]):
            raise GateError(f"日付の形式が違う: {p.get('date')!r}")
        d = dt.date.fromisoformat(p["date"])
        if d > today:
            raise GateError(f"未来の日付がある: {p['date']}")
        dates.append(p["date"])
        nonneg_int(p["views"], "daily.views")
    if len(set(dates)) != len(dates):
        raise GateError("同じ日付が 2 回ある")
    # 直近30日（今日を含む）の日別の和は last30 の合計と一致するはず（GA は 0 の日を返さない）
    w0 = (today - dt.timedelta(days=29)).isoformat()
    s30 = sum(p["views"] for p in data["daily"] if p["date"] >= w0)
    if s30 != t["last30"]["views"]:
        raise GateError(f"直近30日の日別の和 {s30} が合計 {t['last30']['views']} と一致しない")
    months = []
    for p in data["monthly"]:
        if not isinstance(p.get("month"), str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", p["month"]):
            raise GateError(f"月の形式が違う: {p.get('month')!r}")
        months.append(p["month"])
        nonneg_int(p["views"], "monthly.views")
    if len(set(months)) != len(months):
        raise GateError("同じ月が 2 回ある")
    for p in data["devices"]:
        nonneg_int(p["value"], "devices.value")
    # 閲覧数は足し算できる量なので、月別の和は累計と一致するはず（取り違えの検出）
    msum = sum(p["views"] for p in data["monthly"])
    if msum != t["allTime"]["views"]:
        raise GateError(f"月別の和 {msum} が累計 {t['allTime']['views']} と一致しない")


# ---------- 図 ----------

def nice_max(v):
    if v <= 0:
        return 4, 1
    raw = v / 4
    p = 10 ** math.floor(math.log10(raw))
    step = next(m * p for m in (1, 2, 2.5, 5, 10) if raw <= m * p)
    step = max(int(math.ceil(step)), 1)
    return step * math.ceil(v / step), step


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bar_svg(title, labels, values, xticks, theme, partial=(), value_labels=False, subtitle=""):
    """labels: 各バーの名前 / xticks: {index: 表示文字} / partial: 薄く塗る index"""
    c = THEMES[theme]
    W, H = 880, 340
    L, R, T, B = 70, 20, 64, 46
    pw, ph = W - L - R, H - T - B
    ymax, step = nice_max(max(values) if values else 0)
    n = max(len(values), 1)
    slot = pw / n
    bw = max(slot * (0.6 if value_labels else 0.75), 1.5)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="{FONT}">',
         f'<rect width="{W}" height="{H}" rx="8" fill="{c["bg"]}"/>',
         f'<text x="{L}" y="34" font-size="20" font-weight="700" fill="{c["fg"]}">{esc(title)}</text>']
    if subtitle:
        o.append(f'<text x="{W - R}" y="34" font-size="14" text-anchor="end" '
                 f'fill="{c["sub"]}">{esc(subtitle)}</text>')
    y = 0
    while y <= ymax:
        yy = T + ph - ph * y / ymax
        o.append(f'<line x1="{L}" y1="{yy:.1f}" x2="{W - R}" y2="{yy:.1f}" stroke="{c["grid"]}"/>')
        o.append(f'<text x="{L - 10}" y="{yy + 5:.1f}" font-size="14" text-anchor="end" '
                 f'fill="{c["sub"]}">{y:,}</text>')
        y += step
    for i, v in enumerate(values):
        h = ph * v / ymax
        x = L + slot * i + (slot - bw) / 2
        fill = c["bar2"] if i in partial else c["bar"]
        o.append(f'<rect x="{x:.1f}" y="{T + ph - h:.1f}" width="{bw:.1f}" height="{h:.1f}" '
                 f'fill="{fill}"><title>{esc(labels[i])}: {v:,}</title></rect>')
        if value_labels and v > 0:
            o.append(f'<text x="{x + bw / 2:.1f}" y="{T + ph - h - 7:.1f}" font-size="14" '
                     f'text-anchor="middle" fill="{c["fg"]}">{v:,}</text>')
    for i, s in xticks.items():
        x = L + slot * i + slot / 2
        o.append(f'<text x="{x:.1f}" y="{H - 16}" font-size="14" text-anchor="middle" '
                 f'fill="{c["sub"]}">{esc(s)}</text>')
    o.append("</svg>")
    return "\n".join(o) + "\n"


def daily_series(data, site, today):
    """API の窓（today-89 〜 today）のうち昨日まで。GA が返さない日は 0 で埋める。
    今日は途中なので描かない。"""
    end = today - dt.timedelta(days=1)
    start = today - dt.timedelta(days=89)
    if site["data_start"]:
        start = max(start, dt.date.fromisoformat(site["data_start"]))
    got = {p["date"]: p["views"] for p in data["daily"]}
    days, d = [], start
    while d <= end:
        days.append((d, got.get(d.isoformat(), 0)))
        d += dt.timedelta(days=1)
    return days


def monthly_series(data, site, today):
    got = {p["month"]: p["views"] for p in data["monthly"] if p["views"] > 0}
    first = min(got) if got else f"{today.year:04d}-{today.month:02d}"
    if site["data_start"]:
        first = max(first, site["data_start"][:7])
    y, m = map(int, first.split("-"))
    months = []
    while (y, m) <= (today.year, today.month):
        k = f"{y:04d}-{m:02d}"
        months.append((k, got.get(k, 0)))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months[-24:]


def compare(new, old):
    """「12%多い」「26%少ない」「同じ」。比べる相手が 0 なら None。"""
    if old <= 0:
        return None
    pct = round(100 * (new - old) / old)
    if pct == 0:
        return "ほぼ同じ"
    return f"{abs(pct)}%{'多い' if pct > 0 else '少ない'}"


def daily_title(days):
    """題名で結論を言う: 直近 30 日（昨日まで）を、その前の 30 日と比べる。"""
    v = [x for _, x in days]
    if len(v) >= 60:
        c = compare(sum(v[-30:]), sum(v[-60:-30]))
        if c:
            return f"直近30日の閲覧数は、その前の30日より{c}"
    return f"日別の閲覧数（直近{len(days)}日）"


def monthly_title(months, partial):
    """題名で結論を言う: 最後の丸 1 か月を、その前の丸 1 か月と比べる。"""
    full = [(k, v) for i, (k, v) in enumerate(months) if i not in partial]
    if len(full) >= 2:
        (k0, v0), (k1, v1) = full[-2], full[-1]
        c = compare(v1, v0)
        if c:
            return f"{int(k1[5:])}月の閲覧数は{int(k0[5:])}月より{c}"
    return "月別の閲覧数"


def render(data, site, today):
    days = daily_series(data, site, today)
    dticks = {i: f"{d.month}/{d.day}" for i, (d, _) in enumerate(days) if d.day == 1}
    if days:
        dticks.setdefault(0, f"{days[0][0].month}/{days[0][0].day}")
        # 最初のラベルと月初のラベルが重ならないよう、近すぎる月初は消す
        for i in [i for i in dticks if 0 < i < 6]:
            del dticks[i]
    months = monthly_series(data, site, today)
    ml = [k for k, _ in months]
    mticks = {i: (f"{k[:4]}/{int(k[5:])}" if i == 0 or k.endswith("-01") else f"{int(k[5:])}月")
              for i, k in enumerate(ml)}
    partial = {len(months) - 1}  # 今月は途中
    notes = [f"{today.month}月は{today.day}日まで"]
    ds = site["data_start"]
    if ds and ml and ml[0] == ds[:7] and not ds.endswith("-01"):
        partial.add(0)  # 計測を始めた月も途中から
        notes.insert(0, f"{int(ds[5:7])}月は{int(ds[8:])}日から")
    files = {}
    for th in THEMES:
        files[f"insights/daily-{th}.svg"] = bar_svg(
            daily_title(days), [d.isoformat() for d, _ in days],
            [v for _, v in days], dticks, th)
        files[f"insights/monthly-{th}.svg"] = bar_svg(
            monthly_title(months, partial), ml, [v for _, v in months], mticks, th,
            partial, value_labels=True, subtitle="・".join(notes) + "（色の淡い棒）")
    return files


# ---------- README ----------

def picture(name, alt):
    return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="insights/{name}-dark.svg">'
            f'<img src="insights/{name}-light.svg" alt="{alt}" width="100%"></picture>')


def block(data, site, today):
    t = data["totals"]
    dev = {d["label"]: d["value"] for d in data["devices"]}
    dsum = sum(dev.values())
    mobile = f"{100 * dev.get('mobile', 0) / dsum:.0f}%" if dsum else "—"
    since = f"（{site['data_start']} 以降）" if site["data_start"] else ""
    return "\n".join([
        START,
        "",
        "### サイトのアクセス状況",
        "",
        f"| 累計の閲覧数{since} | 直近30日の閲覧数 | 直近30日の訪問者数 | スマホからの閲覧（直近30日） |",
        "|---:|---:|---:|---:|",
        f"| <!--ins:total-->{t['allTime']['views']:,}<!--/ins--> | {t['last30']['views']:,} "
        f"| {t['last30']['users']:,} | {mobile} |",
        "",
        picture("daily", "日別の閲覧数"),
        "",
        picture("monthly", "月別の閲覧数"),
        "",
        f"<sub>最終更新 {today.isoformat()}（JST）・Google Analytics 4 の集計（サイトの "
        f"[アクセス解析ページ]({site['url']}/insights) と同じ集計）を GitHub Actions が毎日取得。"
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
    if BLOCK_RE.search(readme):
        return BLOCK_RE.sub(lambda _: new_block, readme, count=1)
    m = ANCHOR_RE.search(readme)
    if not m:
        raise GateError("README に挿入位置（**https://… の行）が無い")
    return readme[:m.end()] + "\n" + new_block + "\n" + readme[m.end():]


def check_monotonic(old_readme, data):
    m = TOTAL_RE.search(old_readme)
    if not m:
        return
    old = int(m.group(1).replace(",", ""))
    new = data["totals"]["allTime"]["views"]
    # GA は後から数をわずかに直すことがある。1% を超えて減ったら取り違えとみなす
    if new < old * 0.99:
        raise GateError(f"累計が減った: {old:,} → {new:,}")


# ---------- GitHub ----------

def gh(args, payload=None):
    cmd = ["gh", "api", *args] + (["--input", "-"] if payload is not None else [])
    r = subprocess.run(cmd, input=json.dumps(payload) if payload is not None else None,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh api {args[-1]}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def read_readme(repo, ref):
    r = subprocess.run(["gh", "api", f"repos/{repo}/contents/README.md?ref={ref}",
                        "-H", "Accept: application/vnd.github.raw"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"{repo} README 読み込み失敗: {r.stderr.decode()[:200]}")
    return r.stdout.decode("utf-8")


def repo_head(repo):
    branch = gh([f"repos/{repo}"])["default_branch"]
    return branch, gh([f"repos/{repo}/git/ref/heads/{branch}"])["object"]["sha"]


def commit_files(repo, branch, head, files, message):
    """head の上に全ファイルを 1 commit で書く。中身が変わらなければ何もしない。"""
    base_tree = gh([f"repos/{repo}/git/commits/{head}"])["tree"]["sha"]
    tree = []
    for path, text in files.items():
        blob = gh([f"repos/{repo}/git/blobs"],
                  {"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"})
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
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
            check(data, today)
            files = render(data, site, today)
            if dry:
                with open(os.path.join(dry, f"{site['key']}-README.md"), encoding="utf-8") as f:
                    old = f.read()
            else:
                branch, head = repo_head(site["repo"])
                old = read_readme(site["repo"], head)
            check_monotonic(old, data)
            files["README.md"] = splice(old, block(data, site, today))
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
