"""Guard: the release pipeline keeps the old approval gate unless identity is on."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PublishIdentityGuard(unittest.TestCase):
    def test_old_gate_is_the_default(self):
        with open(os.path.join(ROOT, "vm", "bridge", "pipelines", "publish-release.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertIn('if os.environ.get("MUSELINK_IDENTITY") == "1":', src)
        self.assertIn("gate = approval_gate(req, version", src)
        # identity_authorize must only be reachable from the guarded branch
        main_src = src.split("def main():", 1)[1]
        before, after = main_src.split('if os.environ.get("MUSELINK_IDENTITY") == "1":', 1)
        self.assertNotIn("identity_authorize(", before)
        self.assertIn("identity_authorize(", after)


if __name__ == "__main__":
    unittest.main()
