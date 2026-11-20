"""実装役のコンテナの閉じ込め（harness/sandbox.py）の、docker に依らない部品の試験。

    python -m unittest discover -s tests -v
"""
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
