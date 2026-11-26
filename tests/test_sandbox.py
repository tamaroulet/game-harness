"""実装役のコンテナの閉じ込め（harness/sandbox.py）の、docker に依らない部品の試験。

    python -m unittest discover -s tests -v
"""
import re
import sys
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import agy_port  # noqa: E402
import sandbox  # noqa: E402


class EgressTests(unittest.TestCase):
    def test_filter_is_anchored_and_escaped(self):
        self.assertEqual(sandbox.filter_lines(["pypi.org", "oauth2.googleapis.com"]),
                         "^pypi\\.org$\n^oauth2\\.googleapis\\.com$\n")

    def test_egress_image_name_follows_inputs(self):
        net = agy_port.load_config()["network"]
        name = sandbox.egress_image(net)
        self.assertTrue(name.startswith(net["proxy_image"] + "-"))
        self.assertEqual(name, sandbox.egress_image(net))                       # 同じ入力なら同じ名前（作り直さない）
        self.assertNotEqual(name, sandbox.egress_image(dict(net, allow=net["allow"] + ["example.org"])))

    def test_proxy_env_points_at_the_proxy(self):
        net = agy_port.load_config()["network"]
        env = sandbox.proxy_env(net)
        self.assertEqual(env["HTTPS_PROXY"], f"http://{net['proxy_name']}:{net['proxy_port']}")
        self.assertEqual(env["https_proxy"], env["HTTPS_PROXY"])

    def test_egress_plan_reuses_a_running_proxy_and_never_cuts_another_run(self):
        img, internal = "scb-egress:tinyproxy-abc", "scb-int"
        up = {"running": True, "image": img, "networks": ["bridge", internal]}
        self.assertEqual(sandbox.egress_plan(up, img, internal, ["scb-other-c1"]), "reuse")
        self.assertEqual(sandbox.egress_plan(up, img, internal, []), "reuse")
        self.assertEqual(sandbox.egress_plan(dict(up, image="scb-egress:tinyproxy-old"), img, internal, ["scb-other-c1"]),
                         "refuse")
        self.assertEqual(sandbox.egress_plan(dict(up, image="scb-egress:tinyproxy-old"), img, internal, []), "replace")
        self.assertEqual(sandbox.egress_plan(dict(up, running=False), img, internal, []), "replace")
        self.assertEqual(sandbox.egress_plan(dict(up, networks=["bridge"]), img, internal, []), "replace")
        self.assertEqual(sandbox.egress_plan(None, img, internal, []), "replace")

    def test_netcheck_probes_follow_the_allow_list(self):
        """境界の検査（netcheck.py）が届くことを確認する宛先は、許可リスト（config/agy_sandbox.json）と同じ。片方だけ変えると落ちる。"""
        src = (sandbox.ASSETS / "netcheck.py").read_text(encoding="utf-8")
        allowed = re.search(r"ALLOWED = \[(.*?)\]", src, re.S).group(1)
        hosts = sorted({urllib.parse.urlsplit(u).hostname for u in re.findall(r'"(https?://[^"]+)"', allowed)})
        self.assertEqual(hosts, sorted(agy_port.load_config()["network"]["allow"]))

    def test_package_registries_are_closed(self):
        """許可はモデルの API だけ。パッケージのレジストリ（PyPI・npm・crates.io・Go のプロキシ・中国のミラー）は許可リストに
        無く、境界の検査が塞がれていることを確認する宛先に入っている（操縦士の決め、2026-11-25）。"""
        allow = agy_port.load_config()["network"]["allow"]
        self.assertEqual(sorted(allow), ["daily-cloudcode-pa.googleapis.com", "oauth2.googleapis.com", "www.googleapis.com"])
        src = (sandbox.ASSETS / "netcheck.py").read_text(encoding="utf-8")
        blocked = re.search(r"BLOCKED = \[(.*?)\]", src, re.S).group(1)
        hosts = {urllib.parse.urlsplit(u).hostname for u in re.findall(r'"(https?://[^"]+)"', blocked)}
        for h in ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "registry.yarnpkg.com", "crates.io",
                  "index.crates.io", "static.crates.io", "proxy.golang.org", "goproxy.cn", "pypi.tuna.tsinghua.edu.cn"):
            self.assertIn(h, hosts)
        self.assertIn("pip_pypi.returncode != 0", src)   # pip の取得も落ちることを合格の条件にする


class BoxTests(unittest.TestCase):
    def test_live_processes_skip_init_and_self_only_for_root(self):
        root = sandbox.live_script(0)
        self.assertIn('[ "${p#/proc/}" = 1 ] && continue;', root)
        self.assertIn('[ "${p#/proc/}" = $$ ] && continue;', root)
        self.assertNotIn("$$", sandbox.live_script(1000))
        self.assertIn('= 1000 ] || continue', sandbox.live_script(1000))

    def test_env_args(self):
        self.assertEqual(sandbox.env_args({"A": "1", "PATH": "/x:/y"}), ["-e", "A=1", "-e", "PATH=/x:/y"])
        self.assertEqual(sandbox.env_args(None), [])


if __name__ == "__main__":
    unittest.main()
