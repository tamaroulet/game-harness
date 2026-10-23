"""台帳の二重の照合（D-067）：具体のタスクを、大局（横木）とオラクルの実在に照らして機械で確かめ直す。

    python -m unittest tests.test_ledger_double_check -v

**なぜ要るか**: 大局の方針からタスクに落とした後、逆向きの確かめを文章の約束にすると、タスクがオラクルの無いまま、
あるいはどの横木にも寄与しないまま台帳に入り、設計の意図は会話と一緒に消える。この試験は CI の全件に入るので、
台帳を変える PR はこれに通らないとマージできない。総監督が書いた。保護パス。

未完了（pending・in_progress）のタスクごとに、次を確かめる。止めた・取り下げたタスク（W-145）は記録なので見ない。
1. 検証コマンド・オラクル（oracle: [{path, from}]）・acceptance（1〜4 項目）・purpose・deliverable・serves がある
2. オラクルの出どころ：existing ならファイルが今ある。W-n なら、そのタスクが依存に（推移的に）入っている。
   self（実装役が同じ What で書く）は H ラインの道具のタスクだけで、横木（rung）と操縦士のタスクには許さない
3. 検証コマンドが、オラクルのモジュールを名指している
4. H ラインのタスクの検証コマンドは `python -m unittest` で始まる 1 つの命令（Gate 1 は shell を通さない）
5. serves に書いた横木が実在し、タスクがその横木そのものか、横木が推移的に依存するタスクである

立て直しのノード（rebuild）の下のタスクは、横木に数えない（serves を持たない。5 を見ない）。受入・検証・オラクルは、
行う者とは別の者が書くので、書かれるまでは 3 つとも無くてよい（片方だけは不合格。検証の無いタスクは complete が拒む）。
書いたときは 1〜4 を見る。オラクルの from は試験が見る点を書いた者（director か executor）で、行う者（by）と違うこと。
"""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "progress.yaml"
OPEN = ("pending", "in_progress")
REBUILD = "rebuild"
WRITERS = ("director", "executor")   # 立て直しのタスクで、試験が見る点を書く者
SPEC = ("verification", "oracle", "acceptance")


def module_of(path):
    return path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")


class LedgerDoubleCheck(unittest.TestCase):
    def setUp(self):
        state = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
        self.tasks = {t["id"]: t for t in state["tasks"]}
        self.parents = {n["id"]: n.get("parent") for n in state["nodes"]}
        self.open = [t for t in state["tasks"] if t.get("status") in OPEN]
        self.specified = [t for t in self.open if not (self.in_rebuild(t) and not any(k in t for k in SPEC))]
        self.rungs = {t["rung"]: tid for tid, t in self.tasks.items() if "rung" in t}

    def in_rebuild(self, t):
        node = t.get("group")
        while node is not None:
            if node == REBUILD:
                return True
            node = self.parents.get(node)
        return False

    def deps(self, tid):
        """tid が推移的に依存するタスクの ID の集合。"""
        seen, stack = set(), list(self.tasks[tid].get("depends_on") or [])
        while stack:
            d = stack.pop()
            if d in self.tasks and d not in seen:
                seen.add(d)
                stack.extend(self.tasks[d].get("depends_on") or [])
        return seen

    def test_every_open_task_has_its_fields(self):
        for t in self.open:
            with self.subTest(task=t["id"]):
                for key in ("purpose", "deliverable") if self.in_rebuild(t) else ("purpose", "deliverable", "serves"):
                    self.assertIsInstance(t.get(key), str)
                    self.assertTrue(t[key].strip())
                    self.assertNotIn("|", t[key])
                if self.in_rebuild(t):
                    self.assertNotIn("serves", t)
                if t not in self.specified:
                    continue
                v = t.get("verification")
                self.assertIsInstance(v, dict)
                self.assertIsInstance(v.get("command"), str)
                self.assertTrue(t.get("oracle"))
                self.assertTrue(1 <= len(t.get("acceptance") or []) <= 4)

    def test_every_oracle_exists_or_comes_from_a_dependency(self):
        for t in self.specified:
            for o in t.get("oracle") or []:
                with self.subTest(task=t["id"], oracle=o.get("path")):
                    source = o.get("from")
                    if source == "existing":
                        self.assertTrue((ROOT / o["path"]).is_file())
                    elif self.in_rebuild(t):
                        self.assertIn(source, WRITERS)
                        self.assertNotEqual(source, t.get("by"))
                    elif source == "self":
                        self.assertNotIn("rung", t)
                        self.assertNotEqual(t.get("by"), "pilot")
                    else:
                        self.assertIn(source, self.deps(t["id"]))

    def test_the_verification_names_every_oracle(self):
        for t in self.specified:
            command = t["verification"]["command"]
            for o in t.get("oracle") or []:
                with self.subTest(task=t["id"], oracle=o["path"]):
                    self.assertIn(module_of(o["path"]), command)

    def test_an_hline_task_is_verified_by_one_unittest_command(self):
        for t in self.specified:
            if t.get("by") == "pilot":
                continue
            command = t["verification"]["command"]
            with self.subTest(task=t["id"]):
                self.assertTrue(command.startswith("python -m unittest "))
                self.assertNotIn("&&", command)

    def test_every_open_task_serves_the_rung_it_names(self):
        self.assertTrue(self.rungs)
        for t in self.open:
            if self.in_rebuild(t):
                continue
            with self.subTest(task=t["id"], serves=t.get("serves")):
                rung = self.rungs.get(t.get("serves"))
                self.assertIsNotNone(rung)
                if t.get("group") == "outer":
                    continue
                self.assertTrue(t["id"] == rung or t["id"] in self.deps(rung))
