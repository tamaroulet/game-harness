"""高リスク（Tier 2）の PR に付ける 5 行の要約の機械的生成（W-154、req.pilot-report）。

    python -m harness.pilot_brief <PR 番号>

1. 引数はちょうど 1 つ（PR 番号）。不正な引数なら 1 で終わり、何も出力しない。
2. 写しの根に .mirror.json または .branch.json が無ければ stderr に 1 行出し終了コード 2 で終わる。
3. 層の表（config/risk_tiers.json）の tier2 区分に当たるパスが無ければ:
   「低リスク：5 行の要約は要らない」の 1 行を出して 0 で終わる。
4. 高リスク（Tier 2）の PR なら、次の 5 行をこの順序で出力して 0 で終わる:
   1. 要旨と理由: <タイトル>。目的: <目的>
   2. 高リスクの理由: <区分名>: <パス> ...
   3. 検証テストの書き手: <oracle パス>（from: <書き手>）
   4. 副作用と配線の到達: 外側パス: <パス> / 部品: <部品名> / 配線の検査は未達（req.wiring-executed）
   5. レビュアーの批評: <HIGH/LOW> <rationale>

注（58 本目 §4）: main の写しと枝の写しを丸ごと比べているため、枝を切った後に main へ入った変更も枝の変更として出る（写しが merge-base を持つまでの制限）。
"""
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

try:
    from harness.hline_base import load_config
except ImportError:
    from hline_base import load_config

ROOT = Path(__file__).resolve().parent.parent

# 58 本目 §4: 既存テストの特別扱い区分名
CATEGORY_EXISTING_TESTS = "既存のテスト"


def resolve_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """58 本目 §3-1: 設定は本物の読み手 hline_base.load_config() を通す。フォールバックは作らない。"""
    if cfg is not None:
        return cfg
    return load_config()


def get_argon_dir(cfg: Dict[str, Any], root: Path) -> Path:
    """Argon 記録の置き場を解決する。"""
    raw = cfg["argon"]
    p = Path(raw)
    return p if p.is_absolute() else root / p


def match_pattern(path_str: str, pattern: str) -> bool:
    """層の表の型をパスに当てる。
    - / で終わるものは、その下すべてに当たる
    - * は fnmatch
    - パスの区切りは / に揃えてから当てる
    """
    path_str = path_str.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    if pattern.endswith("/"):
        return path_str.startswith(pattern) or path_str == pattern.rstrip("/")
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatch(path_str, pattern)
    return path_str == pattern


def get_changed_files(mirror_dir: Path, branch_dir: Path) -> Tuple[Set[str], Set[str], Set[str]]:
    """main の写しと枝の写しを比較し、(deleted, modified, added) の相対パス集合を返す。"""
    deleted_files: Set[str] = set()
    modified_files: Set[str] = set()
    added_files: Set[str] = set()

    ignored_files = {".mirror.json", ".branch.json", ".git"}

    if mirror_dir.exists():
        for p in mirror_dir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(mirror_dir).as_posix()
            if rel in ignored_files or rel.startswith(".git/"):
                continue
            bp = branch_dir / rel
            if not bp.exists():
                deleted_files.add(rel)
            else:
                try:
                    if bp.read_bytes() != p.read_bytes():
                        modified_files.add(rel)
                except Exception:
                    modified_files.add(rel)

    if branch_dir.exists():
        for p in branch_dir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(branch_dir).as_posix()
            if rel in ignored_files or rel.startswith(".git/"):
                continue
            mp = mirror_dir / rel
            if not mp.exists():
                added_files.add(rel)

    return deleted_files, modified_files, added_files


def find_task(root: Path, branch_name: str) -> Optional[Dict[str, Any]]:
    """枝名から docs/progress.yaml の対応タスクを引く。"""
    progress_path = root / "docs" / "progress.yaml"
    if not progress_path.exists():
        return None
    try:
        data = yaml.safe_load(progress_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    tasks = data.get("tasks", []) if isinstance(data, dict) else []
    if not isinstance(tasks, list):
        return None

    task_id = None
    m = re.search(r"\b(W-\d+)\b", branch_name, re.IGNORECASE)
    if m:
        task_id = m.group(1).upper()

    if not task_id:
        return None

    for t in tasks:
        if isinstance(t, dict) and str(t.get("id", "")).upper() == task_id:
            return t
    return None


def get_argon_critique(argon_dir: Path, pr_num: str) -> str:
    """Argon 評価記録の post レコードから対象 PR の最後の 1 件を取り出す。"""
    evals_path = argon_dir / "evals.jsonl"
    if not evals_path.exists():
        return "無し（Argon 評価記録）"

    query_pr = pr_num.strip().lstrip("#")
    matching_posts = []
    try:
        for line in evals_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                target_str = str(rec.get("target", "")).strip().lstrip("#")
                if rec.get("kind") == "post" and target_str == query_pr:
                    matching_posts.append(rec)
            except Exception:
                continue
    except Exception:
        return "無し（Argon 評価記録）"

    if not matching_posts:
        return "無し（Argon 評価記録）"

    last_rec = matching_posts[-1]
    decision = str(last_rec.get("decision", "")).strip()
    rationale = " ".join(str(last_rec.get("rationale", "")).split())
    if decision and rationale:
        return f"{decision} {rationale}"
    elif decision:
        return decision
    elif rationale:
        return rationale
    return "無し（Argon 評価記録）"


def main(argv: Optional[List[str]] = None, cfg: Optional[Dict[str, Any]] = None, root: Optional[Path] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # 引数はちょうど 1 つでなければ 1 で終わり、何も出さない
    if len(argv) != 1:
        return 1

    pr_num = argv[0].strip()
    if not pr_num:
        return 1

    root_path = Path(root).resolve() if root else ROOT
    config = resolve_cfg(cfg)

    mirror_dir = Path(config["mirror"])
    branches_dir = Path(config["branches"])
    argon_dir = get_argon_dir(config, root_path)
    branch_dir = branches_dir / pr_num

    # 58 本目 §3-2: 写しの根に .mirror.json（main）または .branch.json（枝）が無ければ、
    # stdout には何も出さず、stderr に 1 行出して終了コード 2 で終わる
    mirror_marker = mirror_dir / ".mirror.json"
    branch_marker = branch_dir / ".branch.json"

    if not mirror_marker.exists():
        sys.stderr.write(f"main の写しが見つかりません（.mirror.json がありません: {mirror_dir}）\n")
        return 2

    if not branch_marker.exists():
        sys.stderr.write(f"枝の写しが見つかりません（.branch.json がありません: {branch_dir}）\n")
        return 2

    deleted_files, modified_files, added_files = get_changed_files(mirror_dir, branch_dir)
    all_touched = deleted_files | modified_files | added_files

    # 層の表 config/risk_tiers.json を読み込む
    risk_tiers_path = root_path / "config" / "risk_tiers.json"
    tier2: Dict[str, List[str]] = {}
    if risk_tiers_path.exists():
        try:
            rt_data = json.loads(risk_tiers_path.read_text(encoding="utf-8"))
            tier2 = rt_data.get("tiers", {}).get("tier2", {})
        except Exception:
            pass

    # 高リスク判定:
    # 55 本目 §3-2: 既存テスト判定は main 写しにある tests/ の変更・削除のみ高リスク。新規テスト追加は低リスク。
    matched_categories: Dict[str, List[str]] = {}

    for cat_name, patterns in tier2.items():
        if cat_name == CATEGORY_EXISTING_TESTS:
            candidates = [(p, True) for p in sorted(deleted_files)] + [(p, False) for p in sorted(modified_files)]
        else:
            candidates = (
                [(p, True) for p in sorted(deleted_files)]
                + [(p, False) for p in sorted(modified_files)]
                + [(p, False) for p in sorted(added_files)]
            )

        cat_matches = []
        for p, is_del in candidates:
            if any(match_pattern(p, pat) for pat in patterns):
                label = f"{p}（削除）" if is_del else p
                if label not in cat_matches:
                    cat_matches.append(label)

        if cat_matches:
            matched_categories[cat_name] = cat_matches

    # 層の表に当たるものが無ければ低リスク
    if not matched_categories:
        print("低リスク：5 行の要約は要らない")
        return 0

    # 枝のメタデータ (.branch.json) を読み込む
    branch_info = {}
    branch_json_p = branch_dir / ".branch.json"
    if branch_json_p.exists():
        try:
            branch_info = json.loads(branch_json_p.read_text(encoding="utf-8"))
        except Exception:
            pass
    branch_name = branch_info.get("branch", "")

    task = find_task(root_path, branch_name)

    # 1. 要旨と理由:
    if task:
        title = " ".join(str(task.get("title", "")).split())
        purpose = " ".join(str(task.get("purpose", "")).split())
        line_1 = f"要旨と理由: {title}。目的: {purpose}"
    else:
        line_1 = "要旨と理由: 無し（台帳タスク）"

    # 2. 高リスクの理由:
    reasons = []
    for cat_name, items in matched_categories.items():
        reasons.append(f"{cat_name}: {', '.join(items)}")
    line_2 = f"高リスクの理由: {' / '.join(reasons)}"

    # 3. 検証テストの書き手:
    if task:
        oracle = task.get("oracle")
        oracle_items = []
        if isinstance(oracle, list):
            oracle_items = oracle
        elif isinstance(oracle, dict):
            oracle_items = [oracle]

        if oracle_items and isinstance(oracle_items[0], dict) and oracle_items[0].get("path"):
            o0 = oracle_items[0]
            opath = o0.get("path")
            ofrom = o0.get("from")
            if ofrom:
                line_3 = f"検証テストの書き手: {opath}（from: {ofrom}）"
            else:
                line_3 = f"検証テストの書き手: {opath}"
        else:
            line_3 = "検証テストの書き手: 無し（oracle）"
    else:
        line_3 = "検証テストの書き手: 無し（台帳タスク）"

    # 4. 副作用と配線の到達:
    outside_paths = [
        p for p in sorted(all_touched)
        if not (p.startswith("harness/") or p == "harness" or p.startswith("tests/") or p == "tests")
    ]
    outside_str = ", ".join(outside_paths) if outside_paths else "なし"

    arch_comp_path = root_path / "config" / "arch_components.json"
    comp_titles = []
    if arch_comp_path.exists():
        try:
            arch_data = json.loads(arch_comp_path.read_text(encoding="utf-8"))
            components = arch_data.get("components", {})
            harness_touched = [p for p in all_touched if p.startswith("harness/")]
            for comp_key, comp_info in components.items():
                title = comp_info.get("title", comp_key)
                modules = comp_info.get("modules", [])
                matched = False
                for ht in harness_touched:
                    fname = Path(ht).name
                    if fname in modules or ht in modules or any(match_pattern(ht, m) or match_pattern(fname, m) for m in modules):
                        matched = True
                        break
                if matched and title not in comp_titles:
                    comp_titles.append(title)
        except Exception:
            pass

    comp_str = f"部品: {', '.join(comp_titles)}" if comp_titles else "部品: なし"
    wiring_str = "配線の検査は未達（req.wiring-executed）"
    line_4 = f"副作用と配線の到達: 外側パス: {outside_str} / {comp_str} / {wiring_str}"

    # 5. レビュアーの批評:
    critique_content = get_argon_critique(argon_dir, pr_num)
    line_5 = f"レビュアーの批評: {critique_content}"

    five_lines = [line_1, line_2, line_3, line_4, line_5]
    print("\n".join(five_lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
