"""VibePrevisBench（text の課題）の薄い接続（Gate 1。操縦士の指示、2026-11-25。WSL の root で走らせる）。ベンチ固有の層。

    python -m harness.vpb_run check --task <id>                                   境界と環境の検査（利用枠 0）
    python -m harness.vpb_run run --task <id> --out <置き場> [--fake [--fake-solution <file>]]

公式の手順（vpbench.py の prepare・collect・replay と Rule-based Scene Check）を変えずに使う。prepare が起こした
エージェントのコンテナ（公式の sandbox のイメージ。公式の既定の 8 CPU・16 GB）に実装役（agy）を入れ、公式の TASK.md
（課題文と agent_instructions.txt）をそのまま 1 回渡す（H0。公式の docs/harness.md の「選んだエージェントを、コンテナの
中で TASK.md を課題として /work/blender_agent で走らせる」）。境界・agy・走りの後の検査は共通の層が受け持つ。

- 走らせるのは中身を読んだ dev の扱いの text の課題だけ（config/vpb_run.json の dev_tasks）
- ネットワーク：prepare の --network に内部のネットワークを渡し（外への経路が無い）、プロキシの環境は実装役と境界の検査の
  exec にだけ渡す（公式のコンテナの作りは変えない）。許可はモデルの API だけ（GT のある HF にも届かない）
- 実装役：公式の手順（docker exec … bash -l）と同じく root・HOME=/root。PATH は、ログインのシェルの PATH（公式の prepare が
  /etc/profile.d に置く、管理された blender の置き場が先頭）を exec に渡す。時間の上限は TASK.md の 4 時間（14400 秒）
- 終わり：実装役のプロセスを止め、collect（solution.py と作業場の写し）→ replay（新しいコンテナ、4 CPU・8 GB・ネットワーク
  無し）→ Scene Check。Blender の書き出しは公式の sandbox のイメージの中で score.py の dump() と同じ命令で行い、採点の
  エンジンは公式の score.py の --dumps で走らせる（WSL のホストに Blender の共有ライブラリが無いため。Stage 2 の 0b と同じ）
- replay の落ちの扱い（公式の scorer/engine/vendor/protocol/classify.py の分けと aggregate.py の方針。操縦士の承認
  2026-11-26）：environment_failure などのインフラの落ちは 0 点ではなく未採点で、公式の replay を再走する（再走は
  max_rerun_batches 回まで。公式の replay は再走の初めに前の回の置き場を消すので、再走の前に replay_batch<k>/ へ移して
  残す）。再走しても続けば未採点（infra_exhausted）。モデルの落ち（solution.py が無い・コードの誤りなど）は 0 点。配達の
  後の書き出しか採点の落ちもインフラの扱いで、0 点にしない（未採点）
- collect の落ち（0 でない終わり・時間切れ、または提出物がコンテナにはあるのに run の置き場に写っていない）もインフラの
  扱いで未採点にする（0 点にしない）。コンテナを消す前に提出物の置き場の様子（collect_diag）を記録に残す
- エージェントのコンテナのメモリの上限は 12g（公式の既定は prepare の 16 GB。WSL の VM の物理メモリ 約 15 GiB を超える
  上限は、暴走したときにコンテナの cgroup の OOM より先に VM ごと落ちる恐れがあるので、ホストを守る値にした。Gate 1 の
  実測の最大は 2,007 MiB。H0 と H_harness は同じ上限）
- 記録：Rule の点と 9 次元・落ちの 3 層（D1 の物理の不変条件・claims（課題文の引用つき）・gt_facts（隠しの場面から））への
  分け・エージェントのコンテナのメモリの最大（cgroup の memory.peak）と OOM の数・stream-json の全文・前後の利用枠
- 偽物の agy（--fake、利用枠 0）で通しを確認する。--fake-solution は、偽物のターンの後に与えた solution.py を置く
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agy_port  # noqa: E402
import agy_quota  # noqa: E402
import exitcode  # noqa: E402
import model_pin  # noqa: E402
import phase1_dev  # noqa: E402
import sandbox  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "vpb_run.json"
TASK_RE = re.compile(r"^[A-Z]{1,3}[0-9]{1,4}(_[0-9]+)?$")
TASK_FILE = "/opt/task/TASK.md"
REPLAY_NAME = "^vpb-[0-9a-f]{12}$"   # 公式の replay のコンテナの名前（harness/blender_agent/docker_holder.py の既定）
DIMS = ["A1", "A2", "A3", "P1", "B1", "B2", "C1", "C2", "D1"]
DATA_FILES = ("agent_instructions.txt", "categories.json", "tasks/index.json", "tasks/{id}/task.json", "tasks/{id}/entities.json",
              "tasks/{id}/prompt.txt", "rubrics/{id}/task.json", "rubrics/{id}/gt.json", "rubrics/{id}/claims.json")
INFRA_CATEGORIES = ("environment_failure", "api_protocol_error")         # 公式の classify.py でインフラの落ちの分類
INFRA_STOPS = ("interrupted", "launch_error", "initialization_error")     # 同じく、インフラの落ちの止まりの理由
WORKER_LOST = "Background worker exited without completion status"      # 公式の execution.py の raw() の見張りの文


# ---------------------------------------------------------------- 試験できる部品（docker に依らない）

def load_config(path=CONFIG, common=agy_port.CONFIG):
    """共通の設定に、ベンチ固有の設定を vpb として足す。実装役の uid・HOME・作業場は vpb の sandbox で置き換える。"""
    cfg = agy_port.load_config(common)
    cfg["vpb"] = json.loads(Path(path).read_text(encoding="utf-8"))
    cfg["sandbox"] = dict(cfg["sandbox"], **cfg["vpb"]["sandbox"])
    return cfg


def require_dev(cfg, task):
    """中身を読んだ dev の扱いの課題か。外なら NotDevError（渡された名前を文に入れない）。"""
    if task not in cfg["vpb"]["dev_tasks"]:
        raise phase1_dev.NotDevError("中身を読んだ dev の扱いの課題（config/vpb_run.json の dev_tasks）の外は走らせない"
                                     "（held-out の誤走の防止）")
    if not TASK_RE.match(task):
        raise ValueError(f"課題の ID の形が違う: {task!r}")
    return task


def sums(text):
    """データの SHA256SUMS → {名前: SHA-256}。"""
    out = {}
    for line in (text or "").splitlines():
        digest, _, name = line.partition("  ")
        if digest and name:
            out[name.strip()] = digest.strip()
    return out


def data_files(data, task, listed):
    """使うデータのファイルの SHA-256 と、SHA256SUMS との照合。{名前: {"sha256", "listed_ok"}}（SHA256SUMS に無い名前は None）。"""
    out = {}
    for pat in DATA_FILES:
        name = pat.format(id=task)
        digest = hashlib.sha256((Path(data) / name).read_bytes()).hexdigest()
        out[name] = {"sha256": digest, "listed_ok": (listed[name] == digest) if name in listed else None}
    return out


def layers(row):
    """採点のエンジンの 1 課題の結果（engine/items/<id>/submission.json の row）→ 落ちた点の 3 層への分け。

    Rule の点は、点のある次元の平均の 100 倍（scorer/score.py）。次元の点は、枠（slot）の点の重みつき平均
    （Σ w・点 ÷ Σ w。公式の code/v51_r2/combine.py）。枠の出どころは、claims の層（src v5：課題文の引用つきの事実。
    layout・entity・camera）か、pvscore 5.0.12 の報告（src 509：隠しの GT の場面・出来事・カットから作る項目）。
    D1（物理の妥当さ：めり込み・浮き沈み・瞬間移動・重複・大きさ）は別の層にする。D1 の点は、枠の重みつき平均（raw）に
    内容の関門 g（A2・B1・B2 の平均 ÷ 0.25、上限 1。combine.py の d1_gate）を掛けたものなので、D1 の落ちは、物理の
    不変条件の落ち（1 − raw。D1 の層）と、内容が薄いための関門の減点（raw × (1 − g)。D1_gate）に分ける。
    落ちた点は、次元ごとに (1 − 点) × 100 ÷ 次元の数を、枠の重みで分ける。
    → {"rule", "dims", "lost": {"D1", "D1_gate", "claims", "gt_facts"}, "slots": [...], "d1_gate"}"""
    dims = {d: v for d, v in (row.get("dims") or {}).items() if v}
    unit = 100.0 / len(dims) if dims else 0.0
    lost = {"D1": 0.0, "D1_gate": 0.0, "claims": 0.0, "gt_facts": 0.0}
    slots, scores, gate = [], [], None
    for d in DIMS:
        v = dims.get(d)
        if not v:
            continue
        scores.append(v.get("score") or 0.0)
        ss = [s for s in v.get("slots") or [] if s.get("score") is not None]
        wsum = sum(s.get("w") or 0 for s in ss) or 1.0
        if d == "D1":
            gate = v.get("gate") or row.get("d1_gate")
            if gate and gate.get("g") is not None and gate.get("raw") is not None:
                lost["D1_gate"] += unit * gate["raw"] * (1 - gate["g"])
        for s in ss:
            srcs = {m.get("src") for m in s.get("members") or []}
            layer = "D1" if d == "D1" else ("claims" if srcs == {"v5"} else "gt_facts")
            pts = unit * (s.get("w") or 0) * (1 - s["score"]) / wsum
            lost[layer] += pts
            slots.append({"dim": d, "slot": s.get("slot"), "layer": layer, "w": s.get("w"), "score": s.get("score"),
                          "lost_points": round(pts, 3), "n": sum(m.get("n") or 0 for m in s.get("members") or [])})
    rule = round(100.0 * sum(scores) / len(scores), 3) if scores else 0.0
    return {"rule": rule, "dims": {d: (dims[d].get("score") if d in dims else None) for d in DIMS},
            "lost": {k: round(v, 3) for k, v in lost.items()}, "slots": slots, "d1_gate": gate}


def claim_results(v5, claims):
    """claims の層の事実ごとの結果（v5 の claims と camera_checks。状態は true・false・missing）に、課題文の引用
    （claims.json の claims の cite。番号 n で対応する）を添える。カメラの確認は claims.json の shots から作られ、引用を持たない。"""
    by_n = {c.get("n"): c for c in (claims.get("claims") or []) if isinstance(c, dict)}
    out = []
    for c in (v5.get("claims") or []):
        out.append({"n": c.get("n"), "block": c.get("block"), "text": c.get("text"), "status": c.get("status"),
                    "cite": (by_n.get(c.get("n")) or {}).get("cite")})
    for c in (v5.get("camera_checks") or []):
        out.append({"n": c.get("n"), "block": "camera", "text": c.get("text"), "status": c.get("status"), "cite": None})
    return out


def mib(n):
    return None if n is None else round(n / (1024 * 1024))


def replay_outcome(result):
    """公式の replay の 1 回分（replay.json）→ (kind, reason)。kind は delivered（配達）・infra（インフラの落ち：未採点で
    再走）・model（モデルの落ち：0 点）。公式の classify.py と同じく、最後の試みの failure_category が environment_failure か
    api_protocol_error、または止まりの理由が interrupted・launch_error・initialization_error ならインフラ。replay.json が
    無い（時間切れなど）のもインフラ（公式の方針では watchdog の止めはインフラ）。"""
    if not isinstance(result, dict):
        return "infra", "no_replay_json"
    if result.get("ok") is True:
        return "delivered", "ok"
    attempts = result.get("attempts") or []
    last = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
    if last.get("failure_category") in INFRA_CATEGORIES or last.get("stop_reason") in INFRA_STOPS:
        return "infra", last.get("failure_category") or last.get("stop_reason")
    return "model", last.get("failure_category") or last.get("stop_reason") or "failed"


def next_action(kinds, max_rerun):
    """これまでの replay の回の kind の並び → 次の動き。score（配達）・zero（モデルの落ち）・rerun（インフラの落ち。公式の
    方針の再走）・infra_exhausted（再走が max_rerun 回に達してもインフラの落ち。未採点のまま）。"""
    last = kinds[-1]
    if last == "delivered":
        return "score"
    if last == "model":
        return "zero"
    return "infra_exhausted" if len(kinds) - 1 >= max_rerun else "rerun"


def worker_lost_attempts(task_dir, result):
    """replay の試みごとに、公式の見張りの文（WORKER_LOST）が出たか。replay.json の試みの error と、その試みの置き場
    （公式の replay は落ちた試みを replay.infra<k>/ に移し、最後の試みは replay/）の delivery/validation.json の検査の
    error を見る。Gate 1 で見つけた見張りの競合（終わった命令を dead と誤る）の印。"""
    attempts = (result or {}).get("attempts") or []
    task_dir = Path(task_dir)
    out = []
    for k, a in enumerate(attempts):
        text = str((a or {}).get("error") or "")
        f = task_dir / ("replay" if k == len(attempts) - 1 else f"replay.infra{k}") / "delivery" / "validation.json"
        if f.is_file():
            try:
                checks = json.loads(f.read_text(encoding="utf-8")).get("checks") or []
                text += " " + " ".join(str(c.get("error") or "") for c in checks if isinstance(c, dict))
            except ValueError:
                pass
        out.append(WORKER_LOST in text)
    return out


def keep_batch(task_dir, dest):
    """公式の replay は再走の初めに replay/・replay.infra*/ を消し、replay.json を書き換えるので、再走の前に証拠として
    dest に移す。移した名前の並びを返す。"""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False)
    moved = []
    for p in sorted(Path(task_dir).glob("replay*")):
        shutil.move(str(p), str(dest / p.name))
        moved.append(p.name)
    return moved


def collect_outcome(collect_rc, solution_in_run, solution_in_container):
    """公式の collect の後の分け → (action, reason)。collect が 0 でない終わり（時間切れを含む）か、提出物
    （/work/blender_agent/solution/solution.py）がコンテナにはあるのに run の置き場に写っていないのは、インフラの落ち
    （collect_failed：未採点）。collect が通って提出物がどこにも無いのはモデルの落ち（zero：0 点）。それ以外は replay へ。"""
    if collect_rc != 0:
        return "collect_failed", f"collect rc={collect_rc}"
    if not solution_in_run:
        if solution_in_container:
            return "collect_failed", "solution.py がコンテナにはあるが run の置き場に無い"
        return "zero", "no_solution"
    return "replay", "ok"


def final_aggregate(valid, action, reason, ev):
    """1 課題の集計。status は scored（Rule の点）・model_fail（0 点）・unscored（collect の落ち、インフラの落ちが再走の後も
    続いた、または配達の後の書き出しか採点の落ち。公式はどれも 0 点にしない。rule は None）・invalid_run。"""
    if not valid:
        return {"rule": None, "status": "invalid_run"}
    if action == "zero":
        return {"rule": 0.0, "status": "model_fail", "reason": reason}
    if action in ("infra_exhausted", "collect_failed"):
        return {"rule": None, "status": "unscored", "reason": f"{action} ({reason})"}
    row = (ev or {}).get("scores_csv") or {}
    if row.get("status") == "scored":
        return {"rule": float(row["rule"]), "status": "scored", "lost": ((ev or {}).get("layers") or {}).get("lost")}
    return {"rule": None, "status": "unscored", "reason": "rescore（配達の後の書き出しか採点の落ち。公式はインフラの扱い）",
            "score_status": row.get("status")}


# ---------------------------------------------------------------- 公式の手順を呼ぶ

def official(cfg, args, timeout, log=None, env=None):
    """公式の vpbench.py（作業場の venv の python、公式のリポジトリの中で）。(終わりの符号, 出力)。"""
    v = cfg["vpb"]
    code, out = sandbox.sh([v["python"], "vpbench.py"] + args, timeout=timeout, cwd=v["repo"], env=env)
    if log:
        Path(log).write_text(out, encoding="utf-8")
    return code, out


def check_pins(cfg):
    """公式のリポジトリのコミットと、sandbox のイメージの ID を固定の値と照らす。"""
    v = cfg["vpb"]
    head = sandbox.must(["git", "-C", v["repo"], "rev-parse", "HEAD"], "repo head").strip()
    if head != v["repo_commit"]:
        raise RuntimeError(f"公式のリポジトリのコミットが固定の値と違う: {head}")
    image_id = sandbox.must(["docker", "image", "inspect", "-f", "{{.Id}}", v["image"]], "image inspect").strip()
    if image_id != v["image_id"]:
        raise RuntimeError(f"sandbox のイメージの ID が固定の値と違う: {image_id}")
    return {"repo_commit": head, "image_id": image_id}


def remove_replay_left():
    """公式の replay のコンテナ（docker_holder の既定の名前 vpb-<16 進 12 字>）が残っていれば消して、数を返す（replay を
    時間切れで止めると、子の docker のコンテナが残りうる。走らせるのは 1 題ずつなので、残りはこの走りのもの）。"""
    ids = sandbox.must(["docker", "ps", "-aq", "--filter", f"name={REPLAY_NAME}"], "ps").split()
    for i in ids:
        sandbox.remove(i)
    return len(ids)


def collect_diag(box):
    """collect の後、コンテナを消す前の診断：提出物の置き場（/work/blender_agent/solution）の一覧の頭と、solution.py の有無。"""
    try:
        code, out = sandbox.as_root(box, "ls -la /work/blender_agent/solution 2>&1 | head -20; "
                                         "test -f /work/blender_agent/solution/solution.py && echo SOLUTION_PRESENT", timeout=60)
    except subprocess.TimeoutExpired:
        return {"rc": agy_port.HARD_TIMEOUT_RC, "solution_in_container": None, "listing": []}
    lines = out.splitlines()
    return {"rc": code, "solution_in_container": "SOLUTION_PRESENT" in lines,
            "listing": [ln for ln in lines if ln != "SOLUTION_PRESENT"][:20]}


def login_path(box):
    """公式の手順（docker exec … bash -l）のログインのシェルの PATH。"""
    return sandbox.must(["docker", "exec", box, "bash", "-lc", "printf %s \"$PATH\""], "login path").strip()


def dump_scene(cfg, task, run_dir, dumps, tag, log):
    """Scene Check の書き出し：公式の sandbox のイメージの中で、scorer/score.py の dump() と同じ命令（ネットワーク無し）。"""
    v = cfg["vpb"]
    name = f"{tag}-dump"
    cmd = ["docker", "run", "--rm", "--name", name, "--network", "none", "--cpus", "4", "--memory", "8g",
           "--memory-swap", "8g", "-v", f"{run_dir}:/vpbrun:ro", "-v", f"{dumps}:/dumps", "-v", f"{v['repo']}:/repo:ro",
           v["image"], "blender", "-b", "--factory-startup", "-t", "4", "--python-exit-code", "1",
           f"/vpbrun/{task}/scene.blend", "--python", "/repo/scorer/engine/vendor/v5_0_12/pvscore5/blender_dump.py", "--",
           "--out", f"/dumps/{task}", "--pvtools", "/repo/scorer/engine/common/pvtools.py"]
    t0 = time.time()
    try:
        code, out = sandbox.sh(cmd, timeout=v["dump_timeout_s"])
    except subprocess.TimeoutExpired as e:
        code, out = agy_port.HARD_TIMEOUT_RC, agy_port.timeout_output(e)
    finally:
        sandbox.remove(name)
    Path(log).write_text(out, encoding="utf-8")
    ok = code == 0 and (Path(dumps) / task / "dump.json").is_file() and (Path(dumps) / task / "dump.npz").is_file()
    return {"rc": code, "ok": ok, "seconds": round(time.time() - t0, 1)}


def score(cfg, task, out_dir, run_dir, tag):
    """replay の後の scene.blend を書き出して、公式の採点のエンジンで点を付ける。採点の要約と 3 層の分け。"""
    out_dir = Path(out_dir)
    dumps, results = out_dir / "dumps", out_dir / "scene"
    dumps.mkdir(exist_ok=True)
    rec = {"dump": dump_scene(cfg, task, run_dir, dumps, tag, out_dir / "dump.log")}
    if not rec["dump"]["ok"]:
        return rec
    code, out = official(cfg, ["score", "--data", cfg["vpb"]["data"], "--dumps", str(dumps), "--out", str(results),
                               "--tasks", task], timeout=3600, log=out_dir / "score.log")
    rec["score_rc"] = code
    rows = list(csv.DictReader((results / "scores.csv").open(encoding="utf-8"))) if (results / "scores.csv").is_file() else []
    rec["scores_csv"] = rows[0] if rows else None
    item = results / "engine" / "items" / task / "submission.json"
    if item.is_file():
        cell = json.loads(item.read_text(encoding="utf-8"))
        rec["layers"] = layers(cell.get("row") or {})
        rec["cell_dims"] = cell.get("dims")
        rec["v5_blocks"] = cell.get("v5")
        v5_file = Path(cell.get("v5_file") or "")
        claims = json.loads((Path(cfg["vpb"]["data"]) / "rubrics" / task / "claims.json").read_text(encoding="utf-8"))
        if v5_file.is_file():
            res = claim_results(json.loads(v5_file.read_text(encoding="utf-8")), claims)
            (out_dir / "claims_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
            rec["claims_status"] = {s: sum(1 for c in res if c["status"] == s) for s in sorted({c["status"] for c in res})}
    return rec


def replay_batches(cfg, task, out_dir, run_dir):
    """公式の replay を回し、インフラの落ちなら公式の方針どおり再走する（再走は max_rerun_batches 回まで。再走の前に
    前の回の置き場を out_dir/replay_batch<k>/ へ移して残す）。回ごとの記録の並びを返す（最後の回の action が
    score・zero・infra_exhausted のどれか）。"""
    v = cfg["vpb"]
    t = Path(run_dir) / task
    batches = []
    while True:
        k = len(batches)
        t0 = time.time()
        try:
            code, _ = official(cfg, ["replay", "--data", v["data"], "--run", str(run_dir), "--tasks", task, "--jobs", "1"],
                               timeout=v["replay_timeout_s"],
                               log=Path(out_dir) / ("replay.log" if k == 0 else f"replay_rerun{k}.log"))
        except subprocess.TimeoutExpired:
            code = agy_port.HARD_TIMEOUT_RC
        rp = t / "replay.json"
        result = json.loads(rp.read_text(encoding="utf-8")) if rp.is_file() else None
        kind, reason = replay_outcome(result)
        b = {"batch": k, "rc": code, "seconds": round(time.time() - t0, 1), "result": result, "kind": kind,
             "reason": reason, "worker_lost": worker_lost_attempts(t, result), "left_removed": remove_replay_left()}
        batches.append(b)
        b["action"] = next_action([x["kind"] for x in batches], v["max_rerun_batches"])
        if b["action"] != "rerun":
            return batches
        b["kept_in"] = str(Path(out_dir) / f"replay_batch{k}")
        b["kept"] = keep_batch(t, b["kept_in"])


# ---------------------------------------------------------------- 1 つの課題を回す

def write_record(out_dir, rec):
    (Path(out_dir) / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")


def run_task(cfg, task, out_dir, fake=False, fake_solution=None, token_file=None):
    require_dev(cfg, task)   # dev の扱いの外は、何も作らずに止まる
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise RuntimeError(f"置き場がすでにある: {out_dir}")
    out_dir.mkdir(parents=True)
    v, sbx, net = cfg["vpb"], cfg["sandbox"], cfg["network"]
    run_dir = out_dir / "runs" / v["system"]
    box = f"vpb-{v['system']}-{task.lower()}"
    tag = f"vpbg1-{task.lower()}-{os.getpid()}"
    rec = {"task": task, "cond": "H0", "fake": fake, "model": cfg["agent"]["model_name"], "budget_s": v["budget_s"],
           "limits": {"cpus": v["cpus"], "memory": v["memory"]}, "user": sbx["uid"], "home": sbx["home"],
           "workdir": sbx["workdir"], "allow": net["allow"], "container": box}
    started = False
    try:
        if sandbox.boxes_left(box):
            raise RuntimeError(f"同じ名前のコンテナが残っている: {box}")
        rec["pins"] = check_pins(cfg)
        listed = sums((Path(v["data"]) / "SHA256SUMS").read_text(encoding="utf-8"))
        rec["data_files"] = data_files(v["data"], task, listed)
        if any(f["listed_ok"] is False for f in rec["data_files"].values()):
            raise RuntimeError(f"データのファイルが SHA256SUMS と違う: {rec['data_files']}")
        if not fake:
            rec["quota_before"], ok, why = agy_port.quota_check(cfg)
            if not ok:
                rec["stopped"] = why
                return rec
        rec["egress"] = sandbox.ensure_egress(net)
        code, out = official(cfg, ["prepare", "--data", v["data"], "--task", task, "--run", str(run_dir), "--network",
                                   net["internal"], "--cpus", v["cpus"], "--memory", v["memory"]],
                             timeout=1800, log=out_dir / "prepare.log")
        started = True
        if code != 0:
            raise RuntimeError(f"prepare が落ちた (rc={code}): {out.strip()[-400:]}")
        env = dict(sandbox.proxy_env(net), PATH=login_path(box))
        rec["exec_env_path"] = env["PATH"]
        port = agy_port.AgyPort(cfg, box, lambda n: out_dir / f"stream_t{n}.jsonl", label=task, fake=fake, env=env)
        rec["hooks_sha256"] = port.install(token_file)
        rec["network"] = sandbox.net_check(box, sbx, env=sandbox.proxy_env(net))
        if not rec["network"]["ok"]:
            raise RuntimeError(f"境界の検査に落ちた: {rec['network']}")
        prompt = sandbox.must(["docker", "exec", box, "cat", TASK_FILE], "task file")
        (out_dir / "prompt_sha256.txt").write_text(hashlib.sha256(prompt.encode("utf-8")).hexdigest() + "\n", encoding="utf-8")
        rec["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        rec["agent_start"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        port.start(prompt, v["budget_s"])
        turn = port.records[-1]
        rec["turn"] = {k: val for k, val in turn.items() if k != "turn"}
        rec["valid"] = turn["valid"]
        rec["agent_end"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        rec["left_procs_after_agent"] = sandbox.stop_user(box, sbx)
        rec["memory"] = {"agent_container_peak_mib": mib(sandbox.memory_peak(box)), "oom_kills": sandbox.oom_kills(box)}
        rec["rounds"] = sandbox.as_root(box, "ls -1 /work/blender_agent/rounds 2>/dev/null | wc -l", timeout=60)[1].strip()
        if turn.get("model_error"):
            rec["stopped"] = "モデルの照合に落ちた（採点しない。コンテナは消す）"
            raise model_pin.ModelPinError(turn["model_error"])
        if fake and fake_solution:
            sandbox.copy_in(box, fake_solution, "/work/blender_agent/solution/solution.py", "fake solution copy")
        try:
            code, out = official(cfg, ["collect", "--task", task, "--run", str(run_dir), "--keep"], timeout=3600,
                                 log=out_dir / "collect.log")
        except subprocess.TimeoutExpired:
            code = agy_port.HARD_TIMEOUT_RC
        rec["collect_rc"] = code
        rec["collect_diag"] = collect_diag(box)   # コンテナを消す前に、提出物の置き場の様子を残す
        sandbox.remove(box)
        started = False
        solution = run_dir / task / "solution.py"
        rec["solution_sha256"] = hashlib.sha256(solution.read_bytes()).hexdigest() if solution.is_file() else None
        action, reason = collect_outcome(code, solution.is_file(), rec["collect_diag"].get("solution_in_container"))
        if action == "collect_failed":
            rec["eval"] = {"skipped": f"collect の落ち（{reason}。インフラの扱いで未採点。collect.log と collect_diag を残した）"}
        elif action == "zero":
            rec["eval"] = {"skipped": "solution.py が無い（提出物が無い。公式の集計ではモデルの落ち＝0 点）"}
        else:
            rec["replay"] = replay_batches(cfg, task, out_dir, run_dir)
            action, reason = rec["replay"][-1]["action"], rec["replay"][-1]["reason"]
            if action == "score":
                rec["delivered_sha256"] = {f: hashlib.sha256((run_dir / task / f).read_bytes()).hexdigest()
                                           for f in ("scene.blend", "video.mp4") if (run_dir / task / f).is_file()}
                rec["eval"] = score(cfg, task, out_dir, run_dir, tag)
            elif action == "zero":
                rec["eval"] = {"skipped": f"replay がモデルの落ち（{reason}。公式の集計では 0 点）"}
            else:
                rec["eval"] = {"skipped": f"replay のインフラの落ちが再走 {len(rec['replay']) - 1} 回の後も続いた"
                                          f"（{reason}。公式の方針では未採点で、0 点にしない）"}
        rec["aggregate"] = final_aggregate(rec["valid"], action, reason, rec.get("eval"))
        if not fake:
            rec["quota_after"] = agy_quota.read()
    finally:
        if started:
            sandbox.remove(box)
        rec["boxes_left"] = sandbox.boxes_left(box) + sandbox.boxes_left(tag) + sandbox.boxes_left(REPLAY_NAME)
        write_record(out_dir, rec)
    return rec


# ---------------------------------------------------------------- 入口

def cmd_check(args):
    """境界と環境の検査（利用枠 0）：公式の prepare のコンテナ（内部のネットワーク）に本物の agy（SHA-512 を照らす）と
    フックとトークンを置き、境界の検査と、モデルを呼ばない /quota（root・プロキシ越しの認証）と、ログインの PATH の
    管理された blender を見る。終わったらコンテナと prepare の置き場を消す。"""
    cfg = load_config()
    task = require_dev(cfg, args.task)
    v, sbx, net = cfg["vpb"], cfg["sandbox"], cfg["network"]
    pins = check_pins(cfg)
    sandbox.ensure_egress(net)
    run_dir = Path(args.out) / "runs" / v["system"]
    if run_dir.exists():
        raise RuntimeError(f"置き場がすでにある: {run_dir}")
    box = f"vpb-{v['system']}-{task.lower()}"
    try:
        code, out = official(cfg, ["prepare", "--data", v["data"], "--task", task, "--run", str(run_dir), "--network",
                                   net["internal"], "--cpus", v["cpus"], "--memory", v["memory"]], timeout=1800)
        if code != 0:
            raise RuntimeError(f"prepare が落ちた (rc={code}): {out.strip()[-400:]}")
        env = dict(sandbox.proxy_env(net), PATH=login_path(box))
        sha = agy_port.install(cfg, box, token_file=Path(cfg["agent"]["token"]))
        res = sandbox.net_check(box, sbx, env=sandbox.proxy_env(net))
        code, quota = sandbox.sh(["docker", "exec", "--user", "0:0", "-e", f"HOME={sbx['home']}",
                                  "-e", "AGY_CLI_DISABLE_AUTO_UPDATE=1"] + sandbox.env_args(env) +
                                 [box, agy_port.AGY_BIN, "-p", "/quota", "--output-format", "json"], timeout=180)
        try:
            q = agy_quota.parse(quota[quota.index("{"):])
            quota_line = {"tokens": q["tokens"], "turns": q["turns"],
                          "weekly": round(q["buckets"]["gemini-weekly"]["remaining_fraction"], 4)}
        except (ValueError, KeyError):
            quota_line = {"rc": code, "tail": quota.strip()[-300:]}
        probe = sandbox.sh(["docker", "exec"] + sandbox.env_args(env) + [box, "bash", "-c",
                           "command -v blender; whoami; echo $HOME; pwd; ls /work/blender_agent; head -c 300 " + TASK_FILE],
                           timeout=120)[1]
        print(json.dumps({"pins": pins, "hooks_sha256": sha, "network": res, "quota_in_container": quota_line,
                          "path": env["PATH"], "probe": probe.splitlines()}, ensure_ascii=False, indent=1))
        return 0 if res["ok"] and quota_line.get("tokens") == 0 else 1
    finally:
        sandbox.remove(box)


def cmd_run(args):
    cfg = load_config()
    if args.fake_solution and not args.fake:
        raise RuntimeError("--fake-solution は偽物の走り（--fake）の確認だけで使う")
    token = None if args.fake else Path(args.token or cfg["agent"]["token"])
    if token is not None and not token.is_file():
        raise RuntimeError(f"認証のトークンが無い: {token}")
    rec = run_task(cfg, args.task, args.out, fake=args.fake, fake_solution=args.fake_solution, token_file=token)
    t = rec.get("turn") or {}
    ag = t.get("agent") or {}
    print(f"{rec['task']} valid={rec.get('valid')} aggregate={rec.get('aggregate')} rc={ag.get('rc')} "
          f"timeout={ag.get('timeout')} agent_s={ag.get('seconds')} memory={rec.get('memory')} rounds={rec.get('rounds')} "
          f"denied_ran={sum(1 for d in t.get('denied_tools') or [] if d['ran'])} stopped={rec.get('stopped')}")
    print(f"boxes_left={rec['boxes_left']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="VibePrevisBench（text）の薄い接続（Gate 1）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="境界と環境の検査（利用枠 0）")
    c.add_argument("--task", required=True)
    c.add_argument("--out", required=True, help="prepare の置き場（終わったら残る記録だけ）")
    r = sub.add_parser("run", help="1 つの課題を回す（H0）")
    r.add_argument("--task", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--fake", action="store_true", help="偽物の agy（利用枠 0）")
    r.add_argument("--fake-solution", default=None, help="偽物の走りの後に置く solution.py（経路の確認）")
    r.add_argument("--token", default=None)
    args = ap.parse_args(argv)
    return cmd_check(args) if args.cmd == "check" else cmd_run(args)


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
