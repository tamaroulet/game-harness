"""固定の計画（bench/plans/）と、ベンチの実装役の設定（config/bench_implementer_*.json）が、今のコードで読めることの検査（W-103）。

リポジトリの実物を読む。書き込みは一時のディレクトリだけ。
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import hline_base  # noqa: E402
import hline_queue  # noqa: E402
import plan_check  # noqa: E402

PLANS = ROOT / "bench" / "plans"
CONFIG = ROOT / "config"


def plan_dirs(root=PLANS):
    return sorted(p for p in Path(root).iterdir() if p.is_dir()) if Path(root).is_dir() else []


def plan_contract(d):
    """課題のディレクトリ d が計画の契約を破っている理由の列。通れば空。"""
    d = Path(d)
    found = []
    try:
        files = json.loads((d / "planner.json").read_text(encoding="utf-8")).get("files")
    except (OSError, ValueError) as e:
        return [f"{d.name}: planner.json を読めません: {e}"]
    if not isinstance(files, dict) or not files:
        return [f"{d.name}: planner.json に files がありません"]
    for name, want in sorted(files.items()):
        p = d / name
        if not p.is_file():
            found.append(f"{d.name}: sha256: {name} がありません")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != want:
            found.append(f"{d.name}: sha256: {name} が planner.json と一致しません")
    found += [f"{d.name}: plan_check: {rule}（{task or '全体'}）" for rule, task in plan_check.check(d)]
    for p in sorted(d.glob("P-*.md")):
        if not hline_queue.parse_header(p.read_text(encoding="utf-8"))["task"]:
            found.append(f"{d.name}: ヘッダ: {p.name} の タスク: が空です")
    return found


def config_contract(path):
    """設定 path を config/hline.json の implementer に差し替えて load_config に通した結果の理由の列。通れば空。"""
    path = Path(path)
    cfg = json.loads((CONFIG / "hline.json").read_text(encoding="utf-8"))
    cfg["implementer"] = json.loads(path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        swapped = Path(tmp) / "hline.json"
        swapped.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        try:
            hline_base.load_config(swapped)
        except Exception as e:  # noqa: BLE001 - 例外の型を問わず契約の破れとして出す
            return [f"{path.name}: load_config: {type(e).__name__}: {e}"]
    return []


class RealPlans(unittest.TestCase):
    def test_every_frozen_plan_keeps_its_contract(self):
        for d in plan_dirs():
            with self.subTest(task=d.name):
                self.assertEqual(plan_contract(d), [], f"課題 {d.name} の計画の契約が破れています")


class RealConfigs(unittest.TestCase):
    def test_every_bench_implementer_loads(self):
        for path in sorted(CONFIG.glob("bench_implementer_*.json")):
            with self.subTest(config=path.name):
                self.assertEqual(config_contract(path), [], f"{path.name} が load_config の検査に通りません")


class BrokenPlans(unittest.TestCase):
    def setUp(self):
        dirs = plan_dirs()
        if not dirs:
            return
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.copy = Path(self.tmp.name) / dirs[0].name
        shutil.copytree(dirs[0], self.copy)

    def need_plan(self):
        return hasattr(self, "copy")

    def test_copy_passes(self):
        if self.need_plan():
            self.assertEqual(plan_contract(self.copy), [])

    def test_one_byte_change_fails_with_task_and_reason(self):
        if not self.need_plan():
            return
        target = sorted(self.copy.glob("P-*.md"))[0]
        target.write_bytes(target.read_bytes() + b" ")
        found = plan_contract(self.copy)
        self.assertTrue(any("sha256" in f and target.name in f for f in found), f"{self.copy.name}: {found}")

    def test_missing_task_line_fails_with_task_and_reason(self):
        if not self.need_plan():
            return
        target = sorted(self.copy.glob("P-*.md"))[0]
        lines = [ln for ln in target.read_text(encoding="utf-8").splitlines(keepends=True)
                 if not hline_queue.HEADER.match(ln) or hline_queue.HEADER.match(ln).group(1) != "タスク"]
        target.write_text("".join(lines), encoding="utf-8")
        files = json.loads((self.copy / "planner.json").read_text(encoding="utf-8"))
        files["files"][target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        (self.copy / "planner.json").write_text(json.dumps(files, ensure_ascii=False), encoding="utf-8")
        found = plan_contract(self.copy)
        self.assertEqual([f for f in found if "ヘッダ" not in f], [], f"{self.copy.name}: {found}")
        self.assertTrue(any("ヘッダ" in f and target.name in f for f in found), f"{self.copy.name}: {found}")


class BrokenConfig(unittest.TestCase):
    def test_missing_model_fails_with_file_name_and_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = CONFIG / "bench_implementer_agy.json"
            doc = json.loads(src.read_text(encoding="utf-8"))
            del doc["model"]
            broken = Path(tmp) / src.name
            broken.write_text(json.dumps(doc), encoding="utf-8")
            found = config_contract(broken)
        self.assertEqual(len(found), 1, found)
        self.assertIn("load_config", found[0])
        self.assertIn("model", found[0])


if __name__ == "__main__":
    unittest.main()
