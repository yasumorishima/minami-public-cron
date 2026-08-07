#!/usr/bin/env python3
"""member パイプライン (member-request / sync-roles) のワークフロー実行を監視する。
直近の run が失敗していれば private repo に追跡 issue を作成し GAS 経由で admin にメール通知。
sync-roles のような cron 定期実行は「一定時間走っていない (liveness)」も検出する。
復旧 (問題解消) を検出したら対応する監視 issue を自動 close する。
health-check.yml から hourly に呼ばれる。"""
import os
import json
import datetime
import urllib.request
import urllib.error

OWNER = "yasumorishima"
PUB_REPO = "minami-public-cron"
PRIV_REPO = "minami-baseball-ob"
APP_NAME = "minami-baseball-ob-bot"
WORKFLOWS = ["member-request.yml", "sync-roles.yml"]
BAD = {"failure", "cancelled", "timed_out", "startup_failure"}
MAX_AGE_SEC = 93600
# cron 定期実行ワークフローの liveness しきい値 (秒)。直近 run がこれより古ければ「停止」とみなす。
# member-request は申請時しか走らない event-driven なので対象外。
# GitHub は */15 cron を実際は ~4h に間引くため、stall 判定は 6h で誤検知を避ける
LIVENESS = {"sync-roles.yml": 21600}
# ランナー未割当 (0 ステップ) を GitHub 側起因として通知抑制してよい上限 (秒)。
# これを過ぎても復旧していなければ、原因が何であれ知らせるべきなので抑制しない。
# (GitHub 障害が継続中なら actions_component_degraded 側が引き続き抑制する)
SUPPRESS_NEVER_STARTED_MAX_AGE_SEC = 21600

PROBE_TOKEN = os.environ["PROBE_TOKEN"]
APP_TOKEN = os.environ["APP_TOKEN"]
GAS_URL = os.environ.get("GAS_WEBHOOK_URL", "")
GAS_SECRET = os.environ.get("GAS_WEBHOOK_SECRET", "")
FORCE_TEST = os.environ.get("HEALTHCHECK_FORCE_TEST", "") == "1"
UA = "minami-health-check/1.0"


def gh(path, token, method="GET", payload=None):
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request("https://api.github.com" + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def age_sec(ts):
    t = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    return (now() - t).total_seconds()


def title_for(wf):
    return "🚨 [監視] " + wf + " のワークフロー実行が失敗しています"


def open_monitor_issues():
    out = {}
    page = 1
    while True:
        items = gh("/repos/%s/%s/issues?state=open&per_page=100&page=%d" % (OWNER, PRIV_REPO, page), APP_TOKEN)
        if not items:
            break
        for it in items:
            if "pull_request" in it:
                continue
            out[it.get("title", "")] = it.get("number")
        if len(items) < 100:
            break
        page = page + 1
    return out


def latest_completed_run(wf):
    data = gh("/repos/%s/%s/actions/workflows/%s/runs?per_page=10" % (OWNER, PUB_REPO, wf), PROBE_TOKEN)
    for run in data.get("workflow_runs", []):
        if run.get("status") == "completed":
            return run
    return None


def send_email(subject, issue_url):
    if not (GAS_URL and GAS_SECRET):
        print("  GAS 未設定: メール skip")
        return False
    payload = {"secret": GAS_SECRET, "category": "🚨 監視アラート (ワークフロー異常)", "subject": subject, "issueUrl": issue_url}
    try:
        req = urllib.request.Request(GAS_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            print("  メール送信 HTTP %s" % r.status)
        return True
    except Exception as e:
        print("  ::warning::メール送信失敗: %s" % e)
        return False


_ACTIONS_STATUS = {}


def actions_component_degraded():
    """GitHub Status の Actions コンポーネントが operational でなければ True。
    取得できなければ False (= 抑制しない)。監視を握り潰す方向へは倒さない。"""
    if "v" in _ACTIONS_STATUS:
        return _ACTIONS_STATUS["v"]
    val = False
    try:
        req = urllib.request.Request("https://www.githubstatus.com/api/v2/components.json", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            comps = json.loads(r.read()).get("components", [])
        for c in comps:
            if c.get("name") == "Actions":
                status = c.get("status", "")
                print("  githubstatus: Actions=%s" % status)
                val = status != "operational"
                break
    except Exception as e:
        print("  ::warning::githubstatus 取得失敗 (%s) — 抑制せず通常判定します" % e)
        val = False
    _ACTIONS_STATUS["v"] = val
    return val


def run_never_started(run_id):
    """run 内の job が 1 ステップも実行していなければ True (= ランナー未割当)。
    権限 / secret / コードの不備による失敗は必ず steps >= 1 になるため本物の異常は抑制されない。"""
    try:
        data = gh("/repos/%s/%s/actions/runs/%s/jobs?per_page=100" % (OWNER, PUB_REPO, run_id), PROBE_TOKEN)
    except Exception as e:
        print("  ::warning::job 一覧の取得失敗 (%s) — 抑制せず通常判定します" % e)
        return False
    jobs = data.get("jobs", [])
    if not jobs:
        return False
    for j in jobs:
        if len(j.get("steps") or []) > 0:
            return False
    print("  run %s: 全 job が 0 ステップ (ランナー未割当)" % run_id)
    return True


def infra_outage(st):
    """GitHub 側インフラ起因と判断できるなら True。

    失敗している場合、抑制の必須条件は「1 ステップも実行していない (= ランナー未割当)」。
    権限不足 / secret drift / Supabase 障害 / スクリプト例外は必ず steps >= 1 になるため、
    GitHub 障害の最中でも本物の異常は握り潰さない。
    cron がそもそも起動せず liveness だけが立つケースは失敗 run が無いので status で判断する。
    """
    if st.get("conclusion") in BAD:
        if not run_never_started(st["id"]):
            return False
        if st.get("age", 0) <= SUPPRESS_NEVER_STARTED_MAX_AGE_SEC:
            return True
    return actions_component_degraded()


def detect_problem(wf, st):
    """問題があれば説明文字列を返す。健全なら None。"""
    if st["conclusion"] in BAD and st["age"] <= MAX_AGE_SEC:
        return "直近の実行が %s で終了しました" % st["conclusion"]
    if wf in LIVENESS and st["age"] > LIVENESS[wf]:
        return "直近の実行が約 %.1f 時間前で、定期実行 (cron) が停止した可能性があります" % (st["age"] / 3600.0)
    return None


def main():
    statuses = {}
    for wf in WORKFLOWS:
        run = latest_completed_run(wf)
        if run is None:
            print("%s: completed run なし" % wf)
            continue
        a = age_sec(run.get("created_at", "")) if run.get("created_at") else 1e12
        print("%s: 最新 conclusion=%s age=%.0fs url=%s" % (wf, run.get("conclusion"), a, run.get("html_url", "")))
        statuses[wf] = {"conclusion": run.get("conclusion"), "age": a, "created": run.get("created_at", ""), "url": run.get("html_url", ""), "id": run.get("id")}
    if FORCE_TEST:
        print("HEALTHCHECK_FORCE_TEST=1: 合成異常 (失敗+liveness) を注入してアラート経路を検証")
        statuses["__selftest_fail__"] = {"conclusion": "failure", "age": 0.0, "created": now().strftime("%Y-%m-%dT%H:%M:%SZ"), "url": "https://github.com/%s/%s/actions" % (OWNER, PUB_REPO)}
        statuses["__selftest_liveness__"] = {"conclusion": "success", "age": 999999.0, "created": now().strftime("%Y-%m-%dT%H:%M:%SZ"), "url": "https://github.com/%s/%s/actions" % (OWNER, PUB_REPO)}
        LIVENESS["__selftest_liveness__"] = 10800
    issues = open_monitor_issues()
    undelivered = 0
    for wf, st in statuses.items():
        title = title_for(wf)
        problem = detect_problem(wf, st)
        if problem is not None:
            if st.get("id") and infra_outage(st):
                print("  ::warning::%s: GitHub 側の Actions 障害と判断し通知を抑制しました (conclusion=%s)。障害が明けても継続していれば次回の毎時チェックで通知します。" % (wf, st["conclusion"]))
                continue
            if title in issues:
                print("  既報 (open issue #%s) のため通知 skip: %s" % (issues[title], wf))
                continue
            body = ("自動監視 (health-check) が member パイプラインの異常を検出しました。\n\n"
                    "| 項目 | 内容 |\n|---|---|\n"
                    "| ワークフロー | `%s` (リポ `%s`) |\n"
                    "| 検出内容 | **%s** |\n"
                    "| 直近 conclusion | %s |\n"
                    "| 直近実行時刻 (UTC) | %s |\n"
                    "| 実行ログ | %s |\n\n"
                    "### 主な原因の候補\n"
                    "- GitHub App `%s` の権限不足 (Pull requests / Issues / Contents の write)\n"
                    "- Supabase service_role key / secret の drift\n"
                    "- public-cron の secret 不一致 / cron schedule の自動停止\n\n"
                    "### 対応\n"
                    "1. 上記ログで失敗ステップを特定\n"
                    "2. 原因を修正\n"
                    "3. 当該ワークフローを手動 dispatch で再実行し success を確認\n\n"
                    "※ この issue は問題が解消すると自動 close されます。手動 close も可。") % (wf, PUB_REPO, problem, st["conclusion"], st["created"], st["url"], APP_NAME)
            delivered = False
            issue_url = st["url"]
            try:
                created_issue = gh("/repos/%s/%s/issues" % (OWNER, PRIV_REPO), APP_TOKEN, "POST", {"title": title, "body": body})
                issue_url = created_issue.get("html_url", st["url"])
                print("  issue 作成: %s" % issue_url)
                delivered = True
            except urllib.error.HTTPError as e:
                print("  ::warning::issue 作成失敗: HTTP %s %s" % (e.code, e.read()[:200]))
            if send_email("%s: %s" % (wf, problem), issue_url):
                delivered = True
            if not delivered:
                undelivered = undelivered + 1
                print("  ::error::%s の異常を検出したが issue/メールどちらも配信できませんでした" % wf)
        else:
            if title in issues:
                num = issues[title]
                try:
                    gh("/repos/%s/%s/issues/%d/comments" % (OWNER, PRIV_REPO, num), APP_TOKEN, "POST", {"body": "✅ 自動監視: `%s` の問題が解消したことを確認したため自動 close します。 (%s)" % (wf, st["url"])})
                    gh("/repos/%s/%s/issues/%d" % (OWNER, PRIV_REPO, num), APP_TOKEN, "PATCH", {"state": "closed"})
                    print("  復旧検出: issue #%d を close" % num)
                except urllib.error.HTTPError as e:
                    print("  ::warning::issue #%d の close 失敗: HTTP %s" % (num, e.code))
    if undelivered > 0:
        raise SystemExit("alert 配信に失敗した異常が %d 件あります" % undelivered)
    print("✅ パイプライン監視ステップ完了")


if __name__ == "__main__":
    main()
