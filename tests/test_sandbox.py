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
        """境界の検査（netcheck.py）が届くことを確かめる宛先は、許可リスト（config/agy_sandbox.json）と同じ。片方だけ変えると落ちる。"""
        src = (sandbox.ASSETS / "netcheck.py").read_text(encoding="utf-8")
        allowed = re.search(r"ALLOWED = \[(.*?)\]", src, re.S).group(1)
        hosts = sorted({urllib.parse.urlsplit(u).hostname for u in re.findall(r'"(https?://[^"]+)"', allowed)})
        self.assertEqual(hosts, sorted(agy_port.load_config()["network"]["allow"]))


if __name__ == "__main__":
    unittest.main()
