"""計画役の道具（W-42）：課題の仕様から、What と台帳のタスクの対を受信箱に書かせる。

    python -m harness.planner --spec <spec.md> --package <名前> --inbox <dir>

計画役は docs/prompts/planner.md の指示で 1 回呼ぶ。呼んだ後に受信箱を確かめ、<dir>/planner.json に
files（What と ledger.yaml の名前 → sha256）と problems（満たさなかった項目。満たせば空）を書く。
problems が空なら終了コード 0、そうでなければ 1（環境の異常は 2）。
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import hline_base  # noqa: E402
import model_pin  # noqa: E402
from hline_base import ROOT, Infra, run_agent  # noqa: E402

SEPARATOR = "\n\n---\n\n"
MAX_TASKS = 4
WHAT_NAME = re.compile(r"P-(\d{2})\.md")
FIELDS = ("title", "depends_on", "acceptance", "oracle", "verification", "files")
LEDGER = "ledger.yaml"
RESULT = "planner.json"


def build_input(instruction, inbox, package, spec_text, feedback=None):
    """計画役への入力：指示の全文、区切り、受信箱とパッケージの 2 行、区切り、仕様の全文。
    差し戻し（feedback：{"stage", "target", "reason"} の列）があれば、その後ろに区切りと「## 差し戻された項目」の節を足す。"""
    text = f"{instruction}{SEPARATOR}受信箱: {inbox}\nパッケージ: {package}{SEPARATOR}{spec_text}"
    if feedback:
        items = "\n".join(f"- {f['stage']}（{f['target'] or '全体'}）: {f['reason']}" for f in feedback)
        text += f"{SEPARATOR}## 差し戻された項目\n{items}\n"
    return text


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def what_files(inbox):
    return sorted(p for p in Path(inbox).iterdir() if p.is_file() and p.name.startswith("P-") and p.suffix == ".md")


def what_problems(inbox):
    """(問題の列, What にあるタスク ID の集まり)。"""
    found, ids, numbers = [], set(), []
    paths = what_files(inbox)
    if not paths:
        found.append("What のファイル（P-01.md〜）がありません")
    for p in paths:
        m = WHAT_NAME.fullmatch(p.name)
        if not m:
            found.append(f"What のファイル名 {p.name} が P-NN.md の形ではありません")
            continue
        numbers.append(int(m.group(1)))
        tid = f"P-{m.group(1)}"
        ids.add(tid)
        text = p.read_text(encoding="utf-8", errors="replace")
        if not re.search(rf"^タスク:\s*{tid}\s*$", text, re.M):
            found.append(f"{p.name} に「タスク: {tid}」の行がありません")
        if numbers[-1] > MAX_TASKS:
            found.append(f"{p.name} は P-01〜P-{MAX_TASKS:02d} の外です")
    if numbers and sorted(numbers) != list(range(1, len(numbers) + 1)):
        found.append("What のファイルの番号が P-01 から欠けずに続いていません: " + ", ".join(f"P-{n:02d}" for n in sorted(numbers)))
    return found, ids


def relative_path(value):
    if not isinstance(value, str) or not value.strip():
        return False
    return not (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()
                or ".." in PurePosixPath(value.replace("\\", "/")).parts)


def task_problems(task, where):
    found = []
    for field in FIELDS:
        if field not in task or task[field] is None:
            found.append(f"{where}: {field} がありません")
    if "files" in task and task["files"] is not None:
        files = task["files"]
        if not isinstance(files, list) or not files or not all(relative_path(f) for f in files):
            found.append(f"{where}: files が、相対パスの文字列の列（1 件以上）ではありません")
    if isinstance(task.get("verification"), dict):
        if not task["verification"].get("command"):
            found.append(f"{where}: verification に command がありません")
    elif task.get("verification") is not None:
        found.append(f"{where}: verification が表ではありません")
    for field in ("depends_on", "acceptance", "oracle"):
        if task.get(field) is not None and not isinstance(task[field], list):
            found.append(f"{where}: {field} が列ではありません")
    return found


def ledger_problems(inbox, what_ids):
    path = Path(inbox) / LEDGER
    if not path.is_file():
        return [f"{LEDGER} がありません"]
    try:
        tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        return [f"{LEDGER} を YAML として読めません: {str(e)[:200]}"]
    if not isinstance(tasks, list) or not all(isinstance(t, dict) for t in tasks):
        return [f"{LEDGER} がタスクの表の列ではありません"]
    found, ids = [], []
    for i, t in enumerate(tasks, 1):
        tid = t.get("id")
        ids.append(tid)
        found += task_problems(t, tid if isinstance(tid, str) else f"{LEDGER} の {i} 番目")
    for tid in sorted(what_ids - set(ids)):
        found.append(f"{tid}: {LEDGER} にタスクがありません")
    for tid in sorted({i for i in ids if isinstance(i, str)} - what_ids):
        found.append(f"{tid}: What のファイル {tid}.md がありません")
    if len(ids) != len(set(ids)):
        found.append(f"{LEDGER} に同じ id のタスクが重なっています")
    return found


def check(inbox):
    """受信箱を確かめる。(files, problems)。"""
    inbox = Path(inbox)
    found, ids = what_problems(inbox)
    found += ledger_problems(inbox, ids)
    names = [p.name for p in what_files(inbox)] + ([LEDGER] if (inbox / LEDGER).is_file() else [])
    return {n: sha256_file(inbox / n) for n in names}, found


def plan(cfg, spec, package, inbox, log=None, feedback=None):
    """計画役を 1 回呼び、受信箱を確かめて planner.json を書く。problems を返す。"""
    agent = cfg["planner"]
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    instruction = (ROOT / agent["instruction"]).read_text(encoding="utf-8")
    prompt = build_input(instruction, inbox, package, Path(spec).read_text(encoding="utf-8"), feedback)
    latest = None
    if log is None:   # 回ごとの生ログ（<名前>-<回>.log）を残し、<名前>.log は最後の回の写しにする
        out = Path(cfg["out"]) / "planner"
        out.mkdir(parents=True, exist_ok=True)
        name, n = hline_base.slug(package), 1
        while hline_base.prompt_path(out / f"{name}-{n}.log").exists():
            n += 1
        latest, log = out / f"{name}.log", out / f"{name}-{n}.log"
    try:
        code, _models, _out = run_agent(agent, inbox, agent["ttl_seconds"], "計画役", prompt, log)
    finally:
        if latest is not None and Path(log).is_file():
            shutil.copyfile(log, latest)
    files, problems = check(inbox)
    if code != 0:
        problems.insert(0, f"計画役の CLI が終了コード {code} で終わりました")
    hline_base.write_json(inbox / RESULT, {"files": files, "problems": problems})
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.planner", description=__doc__.splitlines()[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--inbox", required=True)
    args = ap.parse_args(argv)
    try:
        cfg = hline_base.load_config()
        model_pin.require(cfg["planner"], "config/hline.json の planner")
        model_pin.auxiliary_prefixes(cfg["planner"], "config/hline.json の planner")
        problems = plan(cfg, args.spec, args.package, args.inbox)
    except (Infra, model_pin.ModelPinError) as e:
        print(f"ABORT: {e}")
        return 2
    for p in problems:
        print(f"NG: {p}")
    print("合格" if not problems else f"不合格（{len(problems)} 件）")
    return 0 if not problems else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
