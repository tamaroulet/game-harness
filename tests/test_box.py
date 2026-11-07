"""箱（harness/box.py）の検査：箱の守りの引数・受け渡しの順・時間切れ・docker の失敗・確かめ。

    python -m unittest tests.test_box -v

Docker は呼ばない。box._call を差し替え、呼ばれた命令を見る。本物の箱の確かめは python -m harness.box check。
"""
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "harness")]
import bench_run  # noqa: E402
import box  # noqa: E402
import box_tree  # noqa: E402
import toolchain  # noqa: E402

CFG = box.settings()


def tar_of(**files):
    """{名: 中身} の tar。名の __ は / にする。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name.replace("__", "/"))
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


class Fake:
    """box._call の偽物。命令を覚え、決めた結果を返す。"""

    def __init__(self, agent=(0, b"out", ""), receive=None):
        self.calls, self.inputs, self.agent, self.receive = [], [], agent, receive if receive is not None else tar_of()

    def __call__(self, args, data=None, ttl=box.TTL):
        self.calls.append(args)
        self.inputs.append(data)
        if "start" in args:
            return self.agent
        if "cp" in args and args[-1] == "-":
            return 0, self.receive, ""
        return 0, b"", ""

    def named(self, word):
        return [c for c in self.calls if word in c]


def fake_docker(good_agent, good_tests, curl=None, probe_code=0):
    """check が呼ぶ命令の偽物。curl は URL から結果のバイト列を返す関数。"""
    want = box.fingerprint(CFG)

    def fake(args, data=None, ttl=box.TTL):
        if "{{.Id}}" in args:
            return 0, b"sha256:i\n", ""
        if any("gh.box.fingerprint" in a for a in args):
            return 0, want.encode(), ""
        if "{{.State.Running}} {{.Image}}" in args:
            return 0, b"true sha256:i\n", ""
        if "python" in args:
            doc = good_agent if CFG["network"] in args else good_tests
            return probe_code, doc if isinstance(doc, bytes) else json.dumps(doc).encode(), "boom"
        if "curl" in args:
            return 0, (curl or (lambda a: b"200" if "https://pypi.org/simple/" in a else b"000"))(args), ""
        return 0, b"", ""
    return fake


class Guards(unittest.TestCase):
    def assert_guarded(self, args):
        joined = " ".join(args)
        for must in ("--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--pids-limit", "--memory", f"--user {box.USER_ID}"):
            self.assertIn(must, joined)
        for never in ("--privileged", "docker.sock", "/mnt/", ":\\"):
            self.assertNotIn(never, joined)

    def test_agent_box_is_on_the_closed_network_with_the_proxy(self):
        args = box.agent_opts(CFG)
        self.assert_guarded(args)
        self.assertEqual(args[args.index("--network") + 1], CFG["network"])
        joined = " ".join(args)
        self.assertIn(f"HTTPS_PROXY=http://{CFG['proxy_name']}:{CFG['proxy_port']}", joined)
        self.assertIn(f"-v {box.WORK}", joined)
        self.assertIn(f"-v {CFG['token']}:{box.TOKEN_IN}:ro", joined)

    def test_test_box_has_no_network_no_token_and_no_volume(self):
        args = box.tests_opts(CFG)
        self.assert_guarded(args)
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertNotIn("-v", args)
        joined = " ".join(args)
        self.assertNotIn("HTTPS_PROXY", joined)
        self.assertNotIn(CFG["token"], joined)

    def test_docker_is_called_through_wsl_on_windows_without_translating_cwd(self):
        with mock.patch.object(box.sys, "platform", "win32"):
            self.assertEqual(box.docker_cmd(CFG), ["wsl.exe", "-d", CFG["distro"], "-u", "root", "--cd", "/", "--exec", "docker"])
        with mock.patch.object(box.sys, "platform", "linux"):
            self.assertEqual(box.docker_cmd(CFG), ["docker"])

    def test_the_start_script_copies_the_token_and_commits_before_the_agent(self):
        at = [box.START.index(word) for word in ("install -m 600", "git init", "add -A", "git commit", 'exec "$@"')]
        self.assertEqual(at, sorted(at))

    def test_new_files_rule_is_the_same_as_the_boundary_check(self):
        rule = box.new_files_rule("pkg")
        for rel in ("pkg/a.py", "pkg/sub/b.py", "tests/test_pkg.py", "tests/test_pkg_s3.py", "tests/test_other.py", "tests/helpers.py",
                    "scratch.py", "MUJOCO_LOG.TXT", "docs/x.md", "pkg2/a.py", "tests/test_pkg/x.py"):
            self.assertEqual(rule(rel), bench_run.allowed_path(rel, "pkg"), rel)


class Agent(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wt = Path(tmp.name)
        (self.wt / "a.py").write_bytes(b"old")
        (self.wt / ".git").write_text("gitdir: somewhere", encoding="utf-8")

    def run_agent(self, fake, allowed=None):
        with mock.patch.object(box, "_call", fake):
            return box.agent(CFG, ["agy", "-p="], self.wt, 60, "頼み", allowed)

    def test_order_creates_copies_starts_receives_and_removes(self):
        fake = Fake(receive=tar_of(work__a_py=b"", **{"work__a.py": b"new", "work__b.py": b"B"}))
        code, out, err, got = self.run_agent(fake)
        self.assertEqual((code, out, err), (0, "out", ""))
        self.assertEqual(got["written"], ["a.py", "a_py", "b.py"])
        self.assertEqual((self.wt / "a.py").read_bytes(), b"new")
        words = [c[len(box.docker_cmd(CFG))] for c in fake.calls]
        self.assertEqual(words, ["create", "cp", "start", "cp", "rm"])
        self.assertEqual(fake.calls[-1][-3:-1], ["-f", "-v"])
        create = fake.calls[0]
        self.assertEqual(create[create.index("_") + 1:], ["agy", "-p="])

    def test_the_real_git_is_not_passed_and_the_prompt_goes_by_stdin(self):
        fake = Fake(receive=tar_of(**{"work__a.py": b"old"}))
        self.run_agent(fake)
        with tarfile.open(fileobj=io.BytesIO(fake.inputs[1])) as tar:
            self.assertEqual([m.name for m in tar if m.isfile()], ["a.py"])
        self.assertEqual(fake.inputs[2].decode("utf-8"), "頼み")

    def test_the_box_git_is_not_received(self):
        fake = Fake(receive=tar_of(**{"work__a.py": b"old", "work__.git__hooks__pre-commit": b"evil", "work__.git__config": b"evil"}))
        _, _, _, got = self.run_agent(fake)
        self.assertEqual(got, {"written": [], "removed": [], "dropped": []})
        self.assertEqual((self.wt / ".git").read_text(encoding="utf-8"), "gitdir: somewhere")

    def test_the_box_is_removed_even_when_something_fails(self):
        fake = Fake(agent=(125, b"", "daemon error"))
        with self.assertRaises(box.BoxError):
            self.run_agent(fake)
        self.assertIn("rm", fake.calls[-1])

    def test_timeout_stops_the_box_and_still_receives_the_changes(self):
        fake = Fake(agent=(box.TIMED_OUT, b"partial", ""), receive=tar_of(**{"work__a.py": b"half"}))
        code, out, err, got = self.run_agent(fake)
        self.assertEqual((code, out), (box.TIMED_OUT, "partial"))
        self.assertIn("TTL超過", err)
        self.assertTrue(fake.named("kill"))
        self.assertEqual(got["written"], ["a.py"])

    def test_agent_failure_exit_codes_are_passed_on(self):
        code, _, _, got = self.run_agent(Fake(agent=(3, b"", "AGY_ERROR: {}"), receive=tar_of(**{"work__a.py": b"old"})))
        self.assertEqual(code, 3)
        self.assertEqual(got["removed"], [])

    def test_docker_failures_are_box_errors(self):
        for failed in box.DOCKER_FAILED:
            with self.assertRaises(box.BoxError):
                self.run_agent(Fake(agent=(failed, b"", "no")))

    def test_a_failing_copy_stops_before_the_agent(self):
        calls = []

        def fail_copy(args, data=None, ttl=box.TTL):
            calls.append(args)
            return (1, b"", "disk full") if "cp" in args else (0, b"", "")
        with mock.patch.object(box, "_call", fail_copy), self.assertRaises(box.BoxError):
            box.agent(CFG, ["agy"], self.wt, 60, "x")
        self.assertFalse([c for c in calls if "start" in c])
        self.assertIn("rm", calls[-1])

    def test_not_a_tar_from_the_box_is_a_box_error(self):
        with self.assertRaises(box.BoxError):
            self.run_agent(Fake(receive=b"garbage" * 200))

    def test_allowed_reaches_receive(self):
        fake = Fake(receive=tar_of(**{"work__a.py": b"old", "work__scratch.py": b"S"}))
        _, _, _, got = self.run_agent(fake, lambda rel: False)
        self.assertEqual(got["dropped"], [["scratch.py", "許されたパスの外の新しいファイル"]])

    def test_a_tree_too_big_is_a_box_error(self):
        with mock.patch.object(box_tree, "MAX_TOTAL", 1), self.assertRaises(box.BoxError):
            self.run_agent(Fake())


class Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tree = Path(tmp.name)
        (self.tree / "a.py").write_bytes(b"A")

    def test_runs_in_the_box_with_the_tree_and_extra_files_on_stdin(self):
        fake = mock.Mock(return_value=(1, "試験の出力".encode(), "err"))
        with mock.patch.object(box, "_call", fake):
            got = box.tests(CFG, ["python", "-m", "unittest", "tests.x"], self.tree, 30, {"runner.py": b"R"})
        self.assertEqual(got, (1, "試験の出力", "err"))   # 試験が落ちるのは普通の結果
        args, data, ttl = fake.call_args.args
        self.assertEqual(ttl, 30)
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertEqual(args[args.index("_") + 1:], ["python", "-m", "unittest", "tests.x"])
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            self.assertEqual(sorted(m.name for m in tar if m.isfile()), ["a.py", "runner.py"])

    def test_timeout_stops_the_box(self):
        calls = []

        def fake(args, data=None, ttl=box.TTL):
            calls.append(args)
            return (box.TIMED_OUT, b"", "") if data is not None else (0, b"", "")
        with mock.patch.object(box, "_call", fake):
            code, _, err = box.tests(CFG, ["python"], self.tree, 1)
        self.assertEqual(code, box.TIMED_OUT)
        self.assertIn("TTL超過", err)
        self.assertTrue(any("rm" in c for c in calls))

    def test_docker_failure_is_a_box_error(self):
        with mock.patch.object(box, "_call", return_value=(125, b"", "no daemon")), self.assertRaises(box.BoxError):
            box.tests(CFG, ["python"], self.tree, 1)


class Images(unittest.TestCase):
    def test_fingerprint_follows_the_box_files_and_agy(self):
        one = box.fingerprint(CFG)
        self.assertEqual(one, box.fingerprint(json.loads(json.dumps(CFG))))
        other = json.loads(json.dumps(CFG))
        other["agy"]["sha512"] = "0" * 128
        self.assertNotEqual(one, box.fingerprint(other))
        with mock.patch.object(box, "context_files", return_value={"Dockerfile": b"changed"}):
            self.assertNotEqual(one, box.fingerprint(CFG))

    def test_context_is_lf_even_on_a_crlf_checkout(self):
        for data in box.context_files().values():
            self.assertNotIn(b"\r\n", data)

    def test_require_stops_on_a_stale_image(self):
        with mock.patch.object(box, "_call", return_value=(0, b"stale\n", "")), self.assertRaisesRegex(box.BoxError, "造り直"):
            box.require(CFG)

    def test_require_returns_the_image_id(self):
        with mock.patch.object(box, "_call", fake_docker({}, {})):
            self.assertEqual(box.require(CFG), "sha256:i")

    def test_fingerprint_follows_the_agy_version_in_the_ledger(self):
        one = box.fingerprint(CFG)
        with mock.patch.object(box, "agy_version", return_value="9.9.9"):
            self.assertNotEqual(one, box.fingerprint(CFG))

    def test_the_image_pins_the_same_packages_as_the_ledger(self):
        pins = dict(line.split("==") for line in box.context_files()["Dockerfile"].decode().split() if "==" in line)
        ledger = {k.lower(): v for k, v in toolchain.load()["python_packages"].items()}
        self.assertEqual({k.lower(): v for k, v in pins.items() if k.lower() in ledger}, ledger)

    def test_a_missing_image_is_a_box_error(self):
        with mock.patch.object(box, "_call", return_value=(1, b"", "No such image")), self.assertRaises(box.BoxError):
            box.require(CFG)

    def test_build_sends_the_context_on_stdin_with_the_agy_hash(self):
        calls = []

        def fake(args, data=None, ttl=box.TTL):
            calls.append((args, data))
            return 0, b"sha256:abc\n", ""
        with mock.patch.object(box, "_call", fake):
            box.build(CFG)
        builds = [c for c in calls if "build" in c[0]]
        self.assertEqual(len(builds), 2)
        self.assertIn(f"AGY_SHA512={CFG['agy']['sha512']}", builds[0][0])
        self.assertEqual(builds[0][0][-1], "-")
        with tarfile.open(fileobj=io.BytesIO(builds[1][1])) as tar:
            self.assertEqual(sorted(m.name for m in tar), ["Dockerfile", "allow", "tinyproxy.conf"])

    def test_allow_list_has_only_the_package_and_api_hosts(self):
        lines = (box.BOX_DIR / "proxy" / "allow").read_text(encoding="utf-8").split()
        self.assertTrue(lines)
        self.assertFalse([x for x in lines if "github" in x or "play.googleapis" in x or x in (".*", ".")])


class Net(unittest.TestCase):
    def test_a_running_proxy_of_the_right_image_is_left_alone(self):
        calls = []

        def fake(args, data=None, ttl=box.TTL):
            calls.append(args)
            if "{{.Id}}" in args:
                return 0, b"sha256:p\n", ""
            return 0, b"true sha256:p\n", ""
        with mock.patch.object(box, "_call", fake):
            box.ensure_net(CFG)
        self.assertFalse([c for c in calls if "run" in c])

    def test_a_stale_proxy_is_replaced_and_joined_to_the_network(self):
        calls = []

        def fake(args, data=None, ttl=box.TTL):
            calls.append(args)
            if "{{.Id}}" in args:
                return 0, b"sha256:new\n", ""
            if "{{.State.Running}} {{.Image}}" in args:
                return 0, b"true sha256:old\n", ""
            return 0, b"", ""
        with mock.patch.object(box, "_call", fake):
            box.ensure_net(CFG)
        joined = [" ".join(c) for c in calls]
        self.assertTrue(any("rm -f" in c for c in joined))
        started = next(c for c in joined if " run -d " in c or c.startswith("run -d "))
        self.assertIn("--restart always", started)
        self.assertIn("--cap-drop ALL", started)
        self.assertTrue(any(f"network connect {CFG['network']} {CFG['proxy_name']}" in c for c in joined))

    def test_a_missing_network_is_created_internal(self):
        calls = []

        def fake(args, data=None, ttl=box.TTL):
            calls.append(args)
            return (1, b"", "no") if "inspect" in args and "network" in args else (0, b"true x\n", "")
        with mock.patch.object(box, "_call", fake):
            box.ensure_net(CFG)
        self.assertTrue(any("--internal" in c for c in calls))


class Probe(unittest.TestCase):
    TESTS = {"uid": 1000, "mnt": [], "wsl": [], "root_writable": False, "direct": [], "dns": False, "token": False}
    AGENT = {**TESTS, "token": True}

    def test_good_boxes_have_no_problems(self):
        self.assertEqual(box.probe_problems(self.AGENT, True), [])
        self.assertEqual(box.probe_problems(self.TESTS, False), [])

    def test_token_must_be_readable_only_in_the_agent_box(self):
        self.assertIn("認証が読めない", box.probe_problems(self.TESTS, True)[0])
        self.assertIn("認証がある", box.probe_problems(self.AGENT, False)[0])

    def test_each_leak_is_named(self):
        for key, value, word in (("uid", 0, "利用者"), ("mnt", ["c"], "/mnt"), ("wsl", ["/init"], "WSL"), ("root_writable", True, "ルート"),
                                 ("direct", ["8.8.8.8"], "直の接続"), ("dns", True, "名前の解決")):
            problems = box.probe_problems({**self.TESTS, key: value}, False)
            self.assertEqual(len(problems), 1)
            self.assertIn(word, problems[0])

    def test_check_passes_when_both_boxes_and_the_proxy_are_good(self):
        with mock.patch.object(box, "_call", fake_docker(self.AGENT, self.TESTS)):
            self.assertEqual(box.check(CFG), [])

    def test_check_reports_a_proxy_that_lets_everything_through(self):
        with mock.patch.object(box, "_call", fake_docker(self.AGENT, self.TESTS, curl=lambda a: b"200")):
            self.assertTrue(any("example.com" in p for p in box.check(CFG)))

    def test_unreadable_probe_is_a_problem(self):
        with mock.patch.object(box, "_call", fake_docker(b"not json", b"not json", probe_code=1)):
            self.assertEqual(sum("読めません" in p for p in box.check(CFG)), 2)


class Cli(unittest.TestCase):
    def test_main_returns_2_on_a_box_error_and_1_on_problems(self):
        with mock.patch.object(box, "check", side_effect=box.BoxError("no")):
            self.assertEqual(box.main(["check"]), 2)
        with mock.patch.object(box, "check", return_value=["外れ"]):
            self.assertEqual(box.main(["check"]), 1)
        with mock.patch.object(box, "check", return_value=[]):
            self.assertEqual(box.main(["check"]), 0)


if __name__ == "__main__":
    unittest.main()
