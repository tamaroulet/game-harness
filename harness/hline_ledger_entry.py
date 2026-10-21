"""H ライン（harness/hline.py）の台帳の項目：What の `## 台帳の項目` の節（```yaml の囲みの中のタスク 1 件）を、取り込みが確かめて統合ブランチの台帳に積む。

**なぜ要るか**: 台帳の PR を別に出すと、人の手番が統合 PR の承認と台帳の PR の 2 つになる。項目を What が持ち、統合 PR の点検に入れれば、台帳の PR が要らない。
base の台帳に無いタスクの What だけが対象。確かめる規則は tests/test_ledger_double_check.py の 1〜5 と同じ（欄・オラクルの出どころ・検証の名指し・
unittest の 1 命令・横木へのたどり）に、progress.validate と status・by を足したもの。検証コマンドを base の台帳と照合する決まりは変えない。
harness.hline_queue を import しない（循環を作らない）。
"""
import copy
import re

from hline_base import must

import hline_git  # noqa: E402
import proc  # noqa: E402
import progress  # noqa: E402
import yaml  # noqa: E402

SECTION = re.compile(r"^##\s+台帳の項目\s*$")
FENCE = re.compile(r"^```\s*ya?ml\s*$")
TRAILER = "H-Line-Ledger-Entry"
FIELDS = ("purpose", "deliverable", "serves")


def parse(text):
    """(項目, 理由)。節が無ければ (None, None)。節があって読めなければ (None, 理由)。"""
    lines = text.splitlines()
    starts = [n for n, l in enumerate(lines) if SECTION.match(l)]
    if not starts:
        return None, None
    body = []
    for l in lines[starts[0] + 1:]:
        if l.startswith("## "):
            break
        body.append(l)
    opens = [n for n, l in enumerate(body) if FENCE.match(l.strip())]
    if not opens:
        return None, "規則 形: `## 台帳の項目` に ```yaml の囲みがありません"
    block = []
    for l in body[opens[0] + 1:]:
        if l.strip().startswith("```"):
            break
        block.append(l)
    try:
        entry = yaml.safe_load("\n".join(block))
    except yaml.YAMLError as e:
        return None, f"規則 形: 台帳の項目の YAML を解釈できません: {type(e).__name__}"
    if not isinstance(entry, dict) or not entry.get("id"):
        return None, "規則 形: 台帳の項目は id を持つタスク 1 件の辞書です"
    return entry, None


def module_of(path):
    return path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")


def base_state(cfg):
    """base の台帳（docs/progress.yaml を読んだ辞書）。読めなければ ValueError。"""
    code, out, err = proc.run(["git", "show", f"{cfg['base']}:{progress.REL_PATH}"], hline_git.ROOT, cfg["ttl_seconds"]["git"], "git show (ledger)")
    if code != 0:
        raise ValueError(f"{cfg['base']} の {progress.REL_PATH} を読めません: {' '.join((err or out or '').split())[:200]}")
    return yaml.safe_load(out)


def exists_in_base(cfg, path):
    code, _, _ = proc.run(["git", "cat-file", "-e", f"{cfg['base']}:{path}"], hline_git.ROOT, cfg["ttl_seconds"]["git"], "git cat-file (oracle)")
    return code == 0


def reaches(tasks, tid):
    """tid が推移的に依存するタスクの ID の集合。"""
    seen, stack = set(), list(tasks[tid].get("depends_on") or [])
    while stack:
        d = stack.pop()
        if d in tasks and d not in seen:
            seen.add(d)
            stack.extend(tasks[d].get("depends_on") or [])
    return seen


def problems(state, entry, task, exists):
    """項目が規則を満たさない理由（規則ごとに 1 行。空なら合格）。state は base の台帳、exists(path) は base にファイルがあるか。"""
    if entry.get("id") != task:
        return [f"規則 id: 台帳の項目の id（{entry.get('id')}）が What の `タスク:`（{task}）と違います"]
    new = copy.deepcopy(state)
    new["tasks"].append(copy.deepcopy(entry))
    out = [f"規則 validate: {p}" for p in progress.validate(new) if p not in set(progress.validate(state))]
    if entry.get("status") != "pending" or entry.get("by", "hline") != "hline":
        out.append(f"規則 status・by: status は pending、by は無いか hline です（status={entry.get('status')}, by={entry.get('by')}）")
    tasks = {t["id"]: t for t in new["tasks"]}
    command = (entry.get("verification") or {}).get("command") if isinstance(entry.get("verification"), dict) else None
    oracle = entry.get("oracle") if isinstance(entry.get("oracle"), list) and all(isinstance(o, dict) for o in entry["oracle"]) else []
    out += [r for r in (fields_problem(entry, command), source_problem(entry, oracle, tasks, exists),
                        naming_problem(oracle, command), command_problem(entry, command), rung_problem(entry, tasks)) if r]
    return out


def fields_problem(entry, command):
    """規則 1：検証コマンド・オラクル・acceptance（1〜4 項目）・purpose・deliverable・serves がある。"""
    missing = [] if isinstance(command, str) else ["verification.command"]
    oracle = entry.get("oracle")
    if not oracle or not isinstance(oracle, list) or not all(isinstance(o, dict) and o.get("path") for o in oracle):
        missing.append("oracle（path と from）")
    if not isinstance(entry.get("acceptance"), list) or not 1 <= len(entry["acceptance"]) <= 4:
        missing.append("acceptance（1〜4 項目）")
    missing += [f"{k}（空でない文字列。`|` を含まない）" for k in FIELDS
                if not isinstance(entry.get(k), str) or not entry[k].strip() or "|" in entry[k]]
    return f"規則 1（欄）: 足りないか形の違う欄: {', '.join(missing)}" if missing else None


def source_problem(entry, oracle, tasks, exists):
    """規則 2：existing は base にファイルがある。self は横木と操縦士のタスクに許さない。ほかはそのタスクが depends_on に（推移的に）入っている。"""
    bad = []
    for o in oracle:
        src, path = o.get("from"), str(o.get("path") or "")
        if src == "existing":
            ok = exists(path)
        elif src == "self":
            ok = "rung" not in entry and entry.get("by") != "pilot"
        else:
            ok = src in reaches(tasks, entry["id"])
        if not ok:
            bad.append(f"{path}（from: {src}）")
    return f"規則 2（オラクルの出どころ）: 出どころを満たさないオラクル: {'／'.join(bad)}" if bad else None


def naming_problem(oracle, command):
    """規則 3：検証コマンドがオラクルのモジュールを名指している。"""
    unnamed = [str(o.get("path")) for o in oracle if isinstance(command, str) and module_of(str(o.get("path"))) not in command]
    return f"規則 3（検証の名指し）: 検証コマンドがオラクルのモジュールを名指していません: {', '.join(unnamed)}" if unnamed else None


def command_problem(entry, command):
    """規則 4：H ラインのタスクの検証コマンドは `python -m unittest ` で始まる 1 つの命令。"""
    if isinstance(command, str) and entry.get("by") != "pilot" and (not command.startswith("python -m unittest ") or "&&" in command):
        return "規則 4（unittest の 1 命令）: 検証コマンドは `python -m unittest ` で始まる 1 つの命令です"
    return None


def rung_problem(entry, tasks):
    if entry.get("group") == "outer":
        return None
    """規則 5：serves の横木が実在し、タスクがその横木そのものか、横木が推移的に依存するタスクである。"""
    rungs = {t["rung"]: tid for tid, t in tasks.items() if "rung" in t}
    rung = rungs.get(entry.get("serves"))
    if rung is None:
        return f"規則 5（横木へのたどり）: serves {entry.get('serves')} の横木が台帳にありません"
    if entry["id"] != rung and entry["id"] not in reaches(tasks, rung):
        return f"規則 5（横木へのたどり）: タスク {entry['id']} は横木 {entry.get('serves')} そのものでも、横木が依存するタスクでもありません"
    return None


def check(cfg, text, task):
    """(項目, 理由の列)。節が無ければ (None, [])。読めない台帳は ValueError。理由が空なら項目は規則を満たす。"""
    entry, why = parse(text)
    if entry is None:
        return None, [why] if why else []
    return entry, problems(base_state(cfg), entry, task, lambda p: exists_in_base(cfg, p))


def as_row(entry):
    """監修役に渡すためのタスクの欄（read_ledger の行と同じ形）。"""
    return {"deps": list(entry.get("depends_on") or []), "done": False, "by": entry.get("by") or "hline",
            "oracle": list(entry.get("oracle") or []), "row": entry, "after": list(entry.get("after") or []), "rung": entry.get("rung")}


def on_integration(cfg, task):
    """統合ブランチの台帳にすでにこのタスクがあるか。"""
    if not hline_git.integration_exists(cfg):
        return False
    code, out, _ = proc.run(["git", "show", f"{hline_git.remote_ref(cfg)}:{progress.REL_PATH}"], hline_git.ROOT, cfg["ttl_seconds"]["git"], "git show (integration ledger)")
    return code == 0 and task in {t.get("id") for t in (yaml.safe_load(out) or {}).get("tasks", [])}


def stack(cfg, entry, name):
    """統合ブランチの先端に、台帳の tasks の末尾へ項目だけを足すコミットを 1 つ積む。同じ id がすでにあれば積まない。積んだら True。"""
    if on_integration(cfg, entry["id"]):
        return False
    wt, _ = hline_git.new_worktree(cfg, entry["id"])
    t = cfg["ttl_seconds"]["git"]
    path = wt / progress.REL_PATH
    state = progress.load(path)
    state["tasks"].append(entry)
    progress.save(state, path)
    must(["git", "add", progress.REL_PATH], wt, t, "git add")
    must(["git", "commit", "-q", "-m", f"feat(hline): 台帳の項目 {entry['id']} を足す\n\n{TRAILER}: {name}\n{cfg['commit_trailer']}\n"], wt, t, "git commit")
    hline_git.push_tip(cfg, wt)
    return True
