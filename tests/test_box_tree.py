"""箱とのファイルの受け渡し（harness/box_tree.py）の検査：渡すもの・受け取るもの・落とすもの。

    python -m unittest tests.test_box_tree -v

Docker は呼ばない。箱が返す tar を、ここで作る。
"""
import io
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "harness")]
import box_tree  # noqa: E402


def tar_of(*members):
    """(名, 中身) か (名, 種類, 先) の並びの tar。中身が None はディレクトリ。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, *rest in members:
            info = tarfile.TarInfo(name)
            if len(rest) == 2:
                info.type, info.linkname = rest
                tar.addfile(info)
            elif rest[0] is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(rest[0])
                tar.addfile(info, io.BytesIO(rest[0]))
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write(self, rel, data=b"x"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class Pack(Base):
    def members(self, blob):
        with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
            return {m.name: m for m in tar}

    def test_keeps_the_tree_and_leaves_out_git_and_python_output(self):
        self.write("pkg/a.py", b"A")
        self.write(".gitignore", b"I")
        self.write(".git/config", b"secret")
        self.write("pkg/__pycache__/a.cpython-312.pyc", b"P")
        self.write("pkg/b.pyc", b"P")
        names = self.members(box_tree.pack(self.root))
        self.assertEqual(sorted(n for n, m in names.items() if m.isfile()), [".gitignore", "pkg/a.py"])

    def test_a_worktree_git_file_is_not_passed(self):
        self.write(".git", b"gitdir: C:/somewhere/secret")
        self.write("a.py", b"A")
        self.assertEqual([n for n, m in self.members(box_tree.pack(self.root)).items() if m.isfile()], ["a.py"])

    def test_owner_and_modes_belong_to_the_box_user(self):
        self.write("pkg/a.py", b"A")
        names = self.members(box_tree.pack(self.root))
        self.assertTrue(names["pkg"].isdir())
        self.assertEqual((names["pkg"].mode, names["pkg"].uid), (box_tree.DIR_MODE, box_tree.BENCH_UID))
        self.assertEqual((names["pkg/a.py"].mode, names["pkg/a.py"].gid), (box_tree.FILE_MODE, box_tree.BENCH_UID))

    def test_bytes_are_not_changed(self):
        body = "VALUE = 5\r\n# 日本語\n".encode()
        self.write("a.py", body)
        with tarfile.open(fileobj=io.BytesIO(box_tree.pack(self.root))) as tar:
            self.assertEqual(tar.extractfile("a.py").read(), body)

    def test_extra_files_can_go_alone_or_with_the_tree(self):
        self.assertEqual(sorted(self.members(box_tree.pack(None, {"run/r.py": b"R"}))), ["run", "run/r.py"])
        self.write("tree/a.py", b"A")
        names = self.members(box_tree.pack(self.root / "tree", {"r.py": b"R"}))
        self.assertEqual(sorted(n for n, m in names.items() if m.isfile()), ["a.py", "r.py"])

    def test_too_big_tree_stops(self):
        self.write("a.py", b"12345")
        with mock.patch.object(box_tree, "MAX_TOTAL", 3), self.assertRaises(box_tree.TreeError):
            box_tree.pack(self.root)


class Receive(Base):
    def test_writes_only_what_changed_and_keeps_bytes(self):
        same = self.write("same.py", b"S")
        os.utime(same, (1_000_000, 1_000_000))
        self.write("old.py", b"O")
        body = "新しい\n".encode()
        got = box_tree.receive(tar_of(("./same.py", b"S"), ("./old.py", b"N"), ("./pkg", None), ("./pkg/new.py", body)), self.root)
        self.assertEqual(got, {"written": ["old.py", "pkg/new.py"], "removed": [], "dropped": []})
        self.assertEqual(same.stat().st_mtime, 1_000_000)
        self.assertEqual((self.root / "pkg" / "new.py").read_bytes(), body)

    def test_a_file_missing_from_the_tar_is_removed_and_empty_parents_go(self):
        self.write("keep.py")
        self.write("pkg/sub/gone.py")
        got = box_tree.receive(tar_of(("keep.py", b"x")), self.root)
        self.assertEqual(got["removed"], ["pkg/sub/gone.py"])
        self.assertFalse((self.root / "pkg").exists())

    def test_git_and_python_output_are_ignored_and_the_real_git_stays(self):
        real = self.write(".git/config", b"real")
        hook = self.root / ".git" / "hooks" / "pre-commit"
        got = box_tree.receive(tar_of((".git/hooks/pre-commit", b"evil"), (".git/config", b"evil"), ("a/__pycache__/x.pyc", b"p"), ("b.pyc", b"p")), self.root)
        self.assertEqual(got, {"written": [], "removed": [], "dropped": []})
        self.assertEqual(real.read_bytes(), b"real")
        self.assertFalse(hook.exists())
        self.assertFalse((self.root / "a").exists())

    def test_links_and_special_files_are_dropped_with_a_reason(self):
        got = box_tree.receive(tar_of(("s", tarfile.SYMTYPE, "/etc/passwd"), ("h", tarfile.LNKTYPE, "x"), ("f", tarfile.FIFOTYPE, ""), ("c", tarfile.CHRTYPE, "")), self.root)
        self.assertEqual(sorted(got["dropped"]), [["c", "特殊なファイル"], ["f", "特殊なファイル"], ["h", "リンク"], ["s", "リンク"]])
        self.assertEqual(os.listdir(self.root), [])

    def test_a_file_replaced_by_a_link_keeps_the_old_version(self):
        old = self.write("a.py", b"old")
        got = box_tree.receive(tar_of(("a.py", tarfile.SYMTYPE, "/etc/passwd")), self.root)
        self.assertEqual((got["removed"], got["dropped"]), ([], [["a.py", "リンク"]]))
        self.assertEqual(old.read_bytes(), b"old")

    def test_names_outside_the_tree_never_land(self):
        sibling = self.root.parent / (self.root.name + "-x")
        got = box_tree.receive(tar_of(("../" + sibling.name, b"evil"), ("/tmp/box-tree-evil", b"evil"), ("a/../../evil", b"evil")), self.root)
        self.assertEqual([why for _, why in got["dropped"]], ["木の外を指す名前"] * 3)
        self.assertFalse(sibling.exists())
        self.assertFalse(Path("/tmp/box-tree-evil").exists())

    def test_names_windows_cannot_hold_are_dropped(self):
        got = box_tree.receive(tar_of(("a:b.py", b"x"), ("con.txt", b"x"), ("dir./x.py", b"x"), ("sp ace ", b"x"), ("b\\c.py", b"x"), ("q?.py", b"x")), self.root)
        self.assertEqual({why for _, why in got["dropped"]}, {"Windows で作れない名前"})
        self.assertEqual(len(got["dropped"]), 6)
        self.assertEqual(os.listdir(self.root), [])

    def test_a_second_name_differing_only_by_case_is_dropped(self):
        got = box_tree.receive(tar_of(("Pkg.py", b"1"), ("pkg.py", b"2")), self.root)
        self.assertEqual(got["dropped"], [["pkg.py", "大文字と小文字だけが違う名前"]])
        self.assertEqual((self.root / "Pkg.py").read_bytes(), b"1")

    def test_too_big_file_is_dropped_and_the_old_version_stays(self):
        old = self.write("a.py", b"old")
        with mock.patch.object(box_tree, "MAX_FILE", 4):
            got = box_tree.receive(tar_of(("a.py", b"12345"), ("b.py", b"1234")), self.root)
        self.assertEqual(got["dropped"], [["a.py", "大きすぎるファイル"]])
        self.assertEqual(old.read_bytes(), b"old")
        self.assertEqual(got["written"], ["b.py"])

    def test_too_much_in_total_stops(self):
        with mock.patch.object(box_tree, "MAX_TOTAL", 3), self.assertRaises(box_tree.TreeError):
            box_tree.receive(tar_of(("a.py", b"12"), ("b.py", b"34")), self.root)

    def test_allowed_filters_only_new_files(self):
        self.write("docs/existing.md", b"old")
        allowed = lambda rel: rel.startswith("pkg/")  # noqa: E731
        got = box_tree.receive(tar_of(("docs/existing.md", b"new"), ("pkg/a.py", b"A"), ("scratch.py", b"S")), self.root, allowed)
        self.assertEqual(got["written"], ["docs/existing.md", "pkg/a.py"])
        self.assertEqual(got["dropped"], [["scratch.py", "許されたパスの外の新しいファイル"]])
        self.assertFalse((self.root / "scratch.py").exists())

    def test_not_a_tar_stops(self):
        with self.assertRaises(box_tree.TreeError):
            box_tree.receive(b"not a tar at all" * 100, self.root)

    def test_write_failure_is_recorded_and_the_rest_continues(self):
        self.write("blocker", b"file")   # ディレクトリにしたい名がファイル
        got = box_tree.receive(tar_of(("blocker", b"file"), ("blocker/x.py", b"X"), ("ok.py", b"O")), self.root)
        self.assertEqual([row[0] for row in got["dropped"]], ["blocker/x.py"])
        self.assertTrue(got["dropped"][0][1].startswith("書けない"))
        self.assertEqual(got["written"], ["ok.py"])

    def test_strip_takes_off_the_top_directory_docker_cp_adds(self):
        self.write("a.py", b"old")
        got = box_tree.receive(tar_of(("work", None), ("work/a.py", b"new"), ("work/.git/config", b"x"), ("work/pkg/b.py", b"B")), self.root, strip="work")
        self.assertEqual(got, {"written": ["a.py", "pkg/b.py"], "removed": [], "dropped": []})
        self.assertFalse((self.root / "work").exists())

    def test_pack_and_receive_round_trip_changes_nothing(self):
        self.write("pkg/a.py", "日本語\r\n".encode())
        self.write(".gitignore", b"x\n")
        self.write("tests/test_a.py", b"t")
        self.assertEqual(box_tree.receive(box_tree.pack(self.root), self.root), {"written": [], "removed": [], "dropped": []})


if __name__ == "__main__":
    unittest.main()
