"""FeatureBench の fb infer に agy を足す部品（harness/fb_agy.py）のうち、FeatureBench が無くても確かめられる所。

    python -m unittest tests.test_fb_agy -v

FeatureBench に足す所（register）は、WSL の FeatureBench の venv で偽物の agy を通して確かめる（実験と計画の 0-b）。
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import fb_agy  # noqa: E402
import model_pin  # noqa: E402

BOX = fb_agy.load_json(fb_agy.BOX_CONFIG)
AGENT = fb_agy.load_json(fb_agy.AGENT_CONFIG)


class Install(unittest.TestCase):
    def test_real_install_checks_the_sha512_of_the_pinned_url(self):
        script = fb_agy.install_script(BOX)
        self.assertIn(BOX["agy"]["url"], script)
        self.assertIn(BOX["agy"]["sha512"] + "  /tmp/agy.tar.gz", script)
        self.assertLess(script.index("sha512sum -c"), script.index("tar -xzf"), "照らしてから展開する")
        self.assertIn("/usr/local/bin/agy", script)

    def test_fake_install_writes_a_python_agy_that_touches_only_one_file_in_the_tree(self):
        script = fb_agy.install_script(BOX, fake=True)
        self.assertIn("/usr/local/bin/agy", script)
        self.assertNotIn(BOX["agy"]["url"], script, "偽物は網から取らない")
        compile(fb_agy.FAKE_AGY, "fake_agy", "exec")


class Run(unittest.TestCase):
    def test_the_token_goes_where_agy_reads_it(self):
        self.assertEqual(fb_agy.TOKEN_NAME, "antigravity-oauth-token")
        self.assertIn('"antigravity-cli" / "antigravity-oauth-token"', fb_agy.FAKE_AGY, "偽物も同じ名前を読む")

    def test_the_prompt_and_the_stream_stay_outside_the_testbed(self):
        for path in (fb_agy.PROMPT_PATH, fb_agy.STREAM_PATH, fb_agy.TOKEN_DIR):
            self.assertFalse(path.startswith("/testbed"), path)
        cmd = fb_agy.run_command(fb_agy.agent_settings(AGENT, "gemini-4-hydrogen-medium"), 600)
        self.assertTrue(cmd.startswith("set -o pipefail;"), "tee の後でも agy の終了コードを返す")
        self.assertIn(f"HOME={fb_agy.HOME} agy -p= --input-format stream-json", cmd)
        self.assertIn("--model gemini-4-hydrogen-medium", cmd)
        self.assertIn("--print-timeout 580s", cmd)
        self.assertIn(f"< {fb_agy.PROMPT_PATH} | tee {fb_agy.STREAM_PATH}", cmd)

    def test_the_model_must_be_given_exactly(self):
        with self.assertRaises(ValueError):
            fb_agy.agent_settings(AGENT, "")
        self.assertEqual(fb_agy.agent_settings(AGENT, "gemini-3.8-flash-medium")["model_name"], "gemini-3.8-flash-medium")

    def test_the_setting_pins_the_model_and_is_checked_with_the_others(self):
        self.assertEqual(model_pin.require_implementer(AGENT, "config/fb_agy.json"), "gemini-4-hydrogen-medium")
        self.assertIn("config/fb_agy.json", [where for where, _, _ in model_pin.agent_configs()])


class InferArgs(unittest.TestCase):
    def test_task_ids_are_also_passed_as_force_rerun(self):
        args = ["--agent", "agy", "--task-id", "a", "b", "--timeout", "600"]
        self.assertEqual(fb_agy.infer_args(args), args + ["--force-rerun", "a", "b"])

    def test_an_explicit_force_rerun_or_no_task_id_is_left_alone(self):
        for args in (["--task-id", "a", "--force-rerun", "a"], ["--agent", "agy"]):
            self.assertEqual(fb_agy.infer_args(args), args)


class Network(unittest.TestCase):
    def test_the_hosts_are_the_same_as_the_box_proxy_allow_list(self):
        allow = [re.compile(line) for line in (ROOT / "box" / "proxy" / "allow").read_text(encoding="utf-8").split()]
        for host in fb_agy.AGY_HOSTS:
            self.assertTrue(any(a.search(host) for a in allow), host)
        self.assertNotIn("generativelanguage.googleapis.com", fb_agy.AGY_HOSTS)


class Leaks(unittest.TestCase):
    TOKEN = "ya29.fake-token-value"

    def test_the_token_in_the_patch_or_the_log_is_found(self):
        self.assertEqual(fb_agy.leak_problems("diff --git a/x b/x\n+" + self.TOKEN, "", self.TOKEN), ["パッチにトークンの文字列がある"])
        self.assertEqual(fb_agy.leak_problems("", "log " + self.TOKEN, self.TOKEN), ["ログにトークンの文字列がある"])

    def test_agy_files_in_the_patch_are_found(self):
        patch = "diff --git a/.gemini/antigravity-cli/brain/x b/.gemini/antigravity-cli/brain/x\n+record\n"
        self.assertEqual(len(fb_agy.leak_problems(patch, "", self.TOKEN)), 1)

    def test_a_clean_run_has_no_problems(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "output.jsonl").write_text(json.dumps({"instance_id": "t1", "model_patch": "diff --git a/FAKE b/FAKE\n+x\n"}) + "\n",
                                              encoding="utf-8")
            (run / "run_outputs" / "t1").mkdir(parents=True)
            (run / "run_outputs" / "t1" / "infer.log").write_text("ok\n", encoding="utf-8")
            self.assertEqual(fb_agy.check_run(run, self.TOKEN), [])
            (run / "run_outputs" / "t1" / "infer.log").write_text("leak " + self.TOKEN, encoding="utf-8")
            self.assertEqual(len(fb_agy.check_run(run, self.TOKEN)), 1)


if __name__ == "__main__":
    unittest.main()
