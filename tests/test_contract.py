"""契約（.harness.toml）の最小の読み込み（docs/design/spec_pipeline.md §11.2、S14）。

    python -m unittest discover -s tests -v

git は一時ディレクトリの本物（origin に見立てた bare リポジトリと、その clone）。
- 契約は origin/<base> の先頭のオブジェクトから読み、作業ツリーの改ざんに影響されない
- 無い・壊れている・型が違う・正規表現が壊れている・probe が空振り、のいずれかで止まる
- 静的な門は「契約 ∪ 単位定義」で判定し、単位定義から契約のパターンを消せない
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import contract  # noqa: E402
import pipeline  # noqa: E402

VALID = """schema = 1

[static]
selftest_forbidden_probe = "var r = new System.Random(42);"

[[static.forbidden]]
pattern = '\\bSystem\\.Random\\b|\\bnew Random\\s*\\('
label = "XorShift32 以外の乱数"

[[static.forbidden]]
pattern = '\\bDateTime\\.(Now|UtcNow)\\b'
label = "実時間への依存"
"""


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"] + list(args),
                       cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr}")
    return r.stdout.strip()


class RepoFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = self.tmp / "origin.git"
        git(self.tmp, "init", "--bare", "-b", "main", str(self.origin))
        self.repo = self.tmp / "repo"
        git(self.tmp, "clone", str(self.origin), str(self.repo))
        git(self.repo, "switch", "-c", "main")

    def publish(self, text, name=contract.PATH):
        (self.repo / name).write_text(text, encoding="utf-8", newline="\n")
        git(self.repo, "add", name)
        git(self.repo, "commit", "-m", "contract")
        git(self.repo, "push", "origin", "main")
        return git(self.repo, "rev-parse", "HEAD")


class LoadTests(RepoFixture):
    def test_reads_forbidden_and_probe_from_origin_head(self):
        sha = self.publish(VALID)
        c = contract.load(self.repo, "main", 60)
        self.assertEqual(c["sha"], sha)
        self.assertEqual([label for _, label in c["forbidden"]], ["XorShift32 以外の乱数", "実時間への依存"])
        self.assertEqual(c["probe"], "var r = new System.Random(42);")

    def test_working_tree_tampering_is_ignored(self):
        self.publish(VALID)
        (self.repo / contract.PATH).write_text("schema = 1\n", encoding="utf-8")   # 禁止を全部消す改ざん
        self.assertEqual(len(contract.load(self.repo, "main", 60)["forbidden"]), 2)

    def test_reads_the_latest_origin_head_not_a_stale_local_ref(self):
        self.publish("schema = 1\n")
        other = self.tmp / "other"
        git(self.tmp, "clone", str(self.origin), str(other))
        (other / contract.PATH).write_text(VALID, encoding="utf-8", newline="\n")
        git(other, "commit", "-am", "tighten")
        new_sha = git(other, "rev-parse", "HEAD")
        git(other, "push", "origin", "main")
        c = contract.load(self.repo, "main", 60)
        self.assertEqual((c["sha"], len(c["forbidden"])), (new_sha, 2))

    def test_missing_contract_stops(self):
        self.publish("x", name="README.md")
        with self.assertRaisesRegex(contract.ContractError, "ありません"):
            contract.load(self.repo, "main", 60)

    def test_missing_base_branch_stops(self):
        self.publish(VALID)
        with self.assertRaisesRegex(contract.ContractError, "取得できません"):
            contract.load(self.repo, "no-such-branch", 60)


class ParseTests(unittest.TestCase):
    def refused(self, text, message):
        with self.assertRaisesRegex(contract.ContractError, message):
            contract.parse_static(text)

    def test_valid(self):
        self.assertEqual(len(contract.parse_static(VALID)["forbidden"]), 2)

    def test_no_static_table_means_no_patterns(self):
        self.assertEqual(contract.parse_static("schema = 1\n"), {"forbidden": [], "probe": None})

    def test_broken_toml(self):
        self.refused("[static\n", "TOML として読めません")

    def test_wrong_shapes(self):
        self.refused("static = 1\n", r"\[static\] が表ではありません")
        self.refused("[static]\nforbidden = 1\n", "表の配列ではありません")
        self.refused("[static]\nforbidden = [1]\n", "1 件目 が表ではありません")
        self.refused(VALID.replace('label = "実時間への依存"', "label = 3"), "2 件目 の label")
        self.refused(VALID.replace('label = "実時間への依存"', 'label = " "'), "2 件目 の label")
        self.refused(VALID.replace("pattern = '\\bDateTime", "patern = '\\bDateTime"), "未対応のキー")

    def test_unsupported_applies_to_is_not_silently_ignored(self):
        self.refused(VALID.replace('label = "実時間への依存"', 'label = "実時間への依存"\napplies_to = ["x"]'),
                     "未対応のキーがあります: applies_to")

    def test_broken_regex(self):
        self.refused(VALID.replace("\\bDateTime\\.(Now|UtcNow)\\b", "(unclosed"), "正規表現として読めません")

    def test_probe_is_required_and_must_fire(self):
        self.refused(VALID.replace('selftest_forbidden_probe = "var r = new System.Random(42);"\n', ""),
                     "selftest_forbidden_probe がありません")
        self.refused(VALID.replace("new System.Random(42)", "Next()"), "どの禁止パターンにも当たりません")

    def test_real_game_contracts_parse(self):
        """配備済みの契約（手元の clone の作業ツリー。無ければ飛ばす）が、この読み込みで通ること。"""
        for repo in (Path(r"C:\src\unity-2d"), Path(r"C:\src\falling-blocks")):
            p = repo / contract.PATH
            if not p.exists():
                continue
            with self.subTest(repo=repo.name):
                parsed = contract.parse_static(p.read_text(encoding="utf-8"))
                self.assertGreaterEqual(len(parsed["forbidden"]), 4)


class GateTests(unittest.TestCase):
    """静的な門は契約 ∪ 単位定義で判定する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.impl = self.tmp / "Core.cs"
        self.unit = {"whitelist": ["Core.cs"], "forbidden_leftover": "NotImplementedException",
                     "forbidden_skip_attribute_regex": r"\[\s*(Ignore|Explicit)\b"}
        self.c = SimpleNamespace(unit=self.unit, contract=contract.parse_static(VALID),
                                 sb=lambda rel: self.tmp / rel)

    def gate(self, code):
        self.impl.write_text(code, encoding="utf-8")
        return pipeline.gate_static_common(self.c)

    def test_contract_pattern_blocks_even_if_unit_has_none(self):
        self.assertIsNone(self.gate("class A { int x = 1; }"))
        self.assertIn("XorShift32 以外の乱数", self.gate("class A { System.Random r = new System.Random(1); }"))
        self.assertIn("実時間への依存", self.gate("class A { var t = DateTime.UtcNow; }"))

    def test_unit_cannot_remove_contract_patterns(self):
        self.unit["forbidden_patterns"] = [["\\bNeverMatches\\b", "単位定義の禁止"]]
        self.assertIn("XorShift32 以外の乱数", self.gate("class A { var r = new Random(7); }"))

    def test_unit_can_add_patterns(self):
        self.unit["forbidden_patterns"] = [["\\bThread\\.Sleep\\b", "実時間の待機"]]
        self.assertIn("実時間の待機", self.gate("class A { void F() { Thread.Sleep(1); } }"))

    def test_gate_stops_when_contract_was_not_loaded(self):
        self.c.contract = None
        with self.assertRaisesRegex(contract.ContractError, "読み込まれていません"):
            self.gate("class A { }")

    def test_duplicates_are_reported_once_with_contract_label_first(self):
        self.unit["forbidden_patterns"] = [[contract.parse_static(VALID)["forbidden"][0][0], "単位定義の言い方"]]
        eff = contract.effective_forbidden(self.c.contract, self.unit)
        self.assertEqual(len(eff), 2)
        self.assertEqual(eff[0][1], "XorShift32 以外の乱数")


class PipelineWiringTests(unittest.TestCase):
    def test_contract_is_loaded_before_the_repo_is_touched(self):
        """main で、単位の安全確認の直後・リポジトリの検査と実行の前に契約を読む。"""
        src = (ROOT / "harness" / "pipeline.py").read_text(encoding="utf-8")
        body = src[src.index("def main("):]
        order = [body.index(s) for s in ("require_unit_safe(c)", "apply_contract(c)",
                                          "require_repo_clean(c)", "run_unit(c, args)")]
        self.assertEqual(order, sorted(order))

    def test_apply_contract_aborts_on_unreadable_contract(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        git(tmp, "init", "-b", "main")
        c = SimpleNamespace(cfg={"project": {"base_branch": "main"}}, repo=tmp, ttl={"git": 60},
                            tel={}, contract=None)
        with self.assertRaises(SystemExit) as e:
            pipeline.apply_contract(c)
        self.assertIn("ABORT: 契約を読めません", str(e.exception))
        self.assertIsNone(c.contract)

    def test_apply_contract_records_sha_in_telemetry(self):
        fx = RepoFixture()
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        sha = fx.publish(VALID)
        c = SimpleNamespace(cfg={"project": {"base_branch": "main"}}, repo=fx.repo, ttl={"git": 60},
                            tel={}, contract=None)
        pipeline.apply_contract(c)
        self.assertEqual((c.tel["contract_sha"], c.tel["contract_forbidden_count"]), (sha, 2))


if __name__ == "__main__":
    unittest.main()
