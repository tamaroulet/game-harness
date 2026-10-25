"""W-152（知識の正本を Git に集める）の検証テスト（tests/test_director_docs.py）。

試験が見ること（reply-architect-20261023-6.md Line 76）：
① docs/director/INDEX.md から、引継ぎ・deferred.md・残した決まりの文書にリンクで辿れる
② H ラインの照合の読み込みが Git の中の置き場を読む（偽の受信箱に checks/ が無くても照合が読める）
③ docs/**/*.md の C:\\src\\.local を含む行が、許しの一覧の場所だけを指す
④ 移さなかったものの一覧があり、hidden/ を含むパスが Git の木に無い
"""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import hline_checks  # noqa: E402


class TestDirectorDocs(unittest.TestCase):
    def setUp(self):
        self.docs = ROOT / "docs"
        self.director_docs = self.docs / "director"

    def test_index_links_to_required_documents(self):
        """① docs/director/INDEX.md から、引継ぎ・deferred.md・残した決まりの文書にリンクで辿れる。"""
        index_file = self.director_docs / "INDEX.md"
        self.assertTrue(index_file.is_file(), "docs/director/INDEX.md が存在すること")

        content = index_file.read_text(encoding="utf-8")
        # Markdown のリンク形式 [text](link) を抽出
        links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)
        link_targets = {target for _, target in links}

        # 必須文書へのリンクが存在すること
        required_docs = [
            "deferred.md",
            "invariants.md",
            "pipeline.md",
            "naming.md",
            "protocol.md",
            "handoff.md",
            "handoff_dialogue.md",
            "omitted.md",
        ]
        for doc in required_docs:
            self.assertTrue(
                any(doc in target for target in link_targets),
                f"INDEX.md から {doc} へのリンクが存在すること",
            )
            target_path = self.director_docs / doc
            self.assertTrue(
                target_path.is_file(),
                f"参照先の正本文書 {target_path} が実在すること",
            )

    def test_checks_read_from_git_with_config(self):
        """② H ラインの照合の読み込みが設定（config/hline.json）に基づき Git の中の docs/checks を読むこと。"""
        config_path = ROOT / "config" / "hline.json"
        self.assertTrue(config_path.is_file(), "config/hline.json が存在すること")
        with open(config_path, encoding="utf-8") as f:
            hline_cfg = json.load(f)
        self.assertIn("checks", hline_cfg, "config/hline.json に 'checks' が設定されていること")

        checks_path = hline_checks.checks_dir(hline_cfg)
        self.assertEqual(
            checks_path.resolve(),
            (ROOT / "docs" / "checks").resolve(),
            f"checks_dir が ROOT/docs/checks を指していること: {checks_path}",
        )
        self.assertTrue(checks_path.is_dir(), f"checks_dir が実在するディレクトリであること: {checks_path}")

        # 照合宣言 JSON が存在すること（今までの 7 件）
        json_files = list(checks_path.glob("*.json"))
        self.assertEqual(
            len(json_files),
            7,
            f"Git の照合置き場に宣言 JSON が 7 件存在すること（実測: {len(json_files)} 件）",
        )

        # checks が無い場合は KeyError で落ちること（総監督 42 本目 §3.1）
        with self.assertRaises(KeyError):
            hline_checks.checks_dir({"inbox": "fake/inbox"})

    def test_docs_local_path_references_allowed(self):
        """③ docs/**/*.md の C:\\src\\.local を含む行が、許しの一覧の場所だけを指す。"""
        # 許しの一覧（許可されたディレクトリプレフィックス）
        allowed_prefixes = [
            "c:/src/.local/out",
            "c:\\src\\.local\\out",
            "c:/src/.local/main",
            "c:\\src\\.local\\main",
            "c:/src/.local/inbox",
            "c:\\src\\.local\\inbox",
            "c:/src/.local/branches",
            "c:\\src\\.local\\branches",
            "c:/src/.local/wt",
            "c:\\src\\.local\\wt",
            "c:/src/.local/oracles",
            "c:\\src\\.local\\oracles",
            "c:/src/.local/machine.json",
            "c:\\src\\.local\\machine.json",
            "c:/src/.local/review",
            "c:\\src\\.local\\review",
            "c:/src/.local/bench",
            "c:\\src\\.local\\bench",
            "c:/src/.local/harness",
            "c:\\src\\.local\\harness",
        ]

        md_files = list(self.docs.rglob("*.md"))
        violations = []

        for md in md_files:
            lines = md.read_text(encoding="utf-8").splitlines()
            for line_no, line in enumerate(lines, 1):
                # .local を含む行を検出
                if ".local" in line.lower():
                    # C:\src\.local または /c/src/.local のパターンを検索
                    matches = re.findall(
                        r"(?:c:[\\/]|//c/|/c/)src[\\/]\.local(?:[\\/][^\s`\"')\]>、。（）]+)?",
                        line,
                        flags=re.IGNORECASE,
                    )
                    for m in matches:
                        norm = m.lower().replace("//c/", "c:/").replace("/c/", "c:/").replace("\\", "/")
                        is_allowed = any(
                            norm.startswith(a.lower().replace("\\", "/"))
                            for a in allowed_prefixes
                        )
                        if not is_allowed:
                            violations.append(f"{md.relative_to(ROOT)}:{line_no} 不許可な参照: {m}")

        self.assertEqual(
            violations,
            [],
            f"許しの一覧の外を指す行が無いこと:\n" + "\n".join(violations),
        )

    def test_omitted_list_and_no_hidden_paths_in_git(self):
        """④ 移さなかったものの一覧があり、hidden/ を含むパスが Git の木に無い。"""
        omitted_file = self.director_docs / "omitted.md"
        self.assertTrue(omitted_file.is_file(), "docs/director/omitted.md が存在すること")

        text = omitted_file.read_text(encoding="utf-8")
        self.assertIn("W-113", text, "omitted.md に W-113 についての言及があること")
        self.assertIn("hidden", text.lower(), "omitted.md に hidden についての言及があること")

        # Git の木（作業ツリー）に hidden/ を含むパスが無いこと
        # .git やキャッシュ等は除外
        hidden_paths = []
        for p in ROOT.rglob("*"):
            rel = p.relative_to(ROOT)
            parts = rel.parts
            if any(part.startswith(".") for part in parts):
                continue
            if "hidden" in parts or any("hidden" in part.lower() for part in parts):
                hidden_paths.append(str(rel))

        self.assertEqual(
            hidden_paths,
            [],
            f"hidden/ を含むパスが Git の木に無いこと: {hidden_paths}",
        )


if __name__ == "__main__":
    unittest.main()
