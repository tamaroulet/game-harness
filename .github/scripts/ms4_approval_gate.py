"""承認ゲート。PR の必須チェック `approval` の実体（ゲームのリポジトリに配る）。

配布元: tamaroulet/game-harness の harness/templates/game-repo/。直接編集しない。

ワークフロー（.github/workflows/approval.yml）が pull_request_target で呼ぶ。
pull_request_target は保護された base ブランチ側の定義で動くので、PR の中で
このファイルやワークフローを書き換えても、承認チェックは骨抜きにならない。
PR のコードは checkout しない（base を checkout し、GitHub API だけを見る）。

判定（時刻は一切比べない。ローカル PC の時計ずれに影響されない）:

  synchronize（PR へ push された）
      ms4:approved を外して赤。承認は push によって機械的に失効する。
  opened / reopened / labeled / unlabeled
      次のすべてが成り立てば緑:
        1. 実行時点の PR の head SHA が、イベントの head SHA と一致する（途中で push が挟まっていない）
        2. 実行時点で ms4:approved が付いている
        3. 最後に ms4:approved を付けた人が .github/ms4-approvers に居る

必須チェックは head SHA ごとに付くので、「push の前に付けた承認」は新しい SHA で緑にならない。
承認者の一覧が空・読めないときは赤（フェイルクローズド）。

終了コード: 0=承認済み / 1=未承認・失効
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

LABEL = "ms4:approved"


def github_api(token):
    def call(method, path, allow_404=False):
        req = urllib.request.Request(
            "https://api.github.com" + path, method=method,
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if allow_404 and e.code == 404:
                return None
            raise
    return call


def read_approvers(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {l.strip().lower() for l in lines if l.strip() and not l.strip().startswith("#")}


def last_labeler(api, repo, number, label):
    """issue events を全ページ読み、最後に label を付けた人の login を返す。"""
    actor = None
    page = 1
    while True:
        events = api("GET", f"/repos/{repo}/issues/{number}/events?per_page=100&page={page}") or []
        for ev in events:
            if ev.get("event") == "labeled" and (ev.get("label") or {}).get("name") == label:
                actor = (ev.get("actor") or {}).get("login")
        if len(events) < 100:
            return actor
        page += 1


def decide(event, api, repo, approvers, label=LABEL):
    """(承認済みか, 理由)"""
    action = event.get("action")
    pr = event["pull_request"]
    number = pr["number"]

    if action == "synchronize":
        names = {l["name"] for l in pr.get("labels", [])}
        if label in names:
            api("DELETE", f"/repos/{repo}/issues/{number}/labels/{label}", allow_404=True)
            return False, f"push されたため {label} を外しました。承認をやり直してください"
        return False, "push されました（未承認）"

    if not approvers:
        return False, "承認者の一覧（.github/ms4-approvers）が空か読めません"

    current = api("GET", f"/repos/{repo}/pulls/{number}")
    event_sha = pr["head"]["sha"]
    if current["head"]["sha"] != event_sha:
        return False, (f"判定中に push が挟まりました（イベント {event_sha[:8]} / "
                       f"現在 {current['head']['sha'][:8]}）")

    names = {l["name"] for l in current.get("labels", [])}
    if label not in names:
        return False, f"{label} が付いていません"

    who = last_labeler(api, repo, number, label)
    if not who or who.lower() not in approvers:
        return False, f"{label} を付けたのが承認者ではありません（{who}）"

    return True, f"{who} が {event_sha[:8]} を承認"


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    repo = os.environ["GITHUB_REPOSITORY"]
    approvers = read_approvers(os.environ.get("MS4_APPROVERS_FILE", ".github/ms4-approvers"))
    ok, reason = decide(event, github_api(os.environ["GITHUB_TOKEN"]), repo, approvers)
    print(("承認済み: " if ok else "未承認: ") + reason)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
