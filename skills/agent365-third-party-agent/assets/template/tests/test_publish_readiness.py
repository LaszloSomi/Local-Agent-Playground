import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "publish_readiness", Path(__file__).resolve().parents[1] / "scripts" / "Test-PublishReadiness.py"
)
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)


class PublishReadinessTests(unittest.TestCase):
    def test_private_values_include_overlay_credentials_but_not_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath(".env").write_text(
                "WEB_CLIENT_SECRET=private-test-secret\nAPP_PORT=8000\n"
                "AGENT365_AGENT_ID=12345678-1234-4234-8234-123456789012\n",
                encoding="utf-8",
            )
            root.joinpath(".env.agent2").write_text(
                "WEB_CLIENT_SECRET=overlay-test-secret\n", encoding="utf-8"
            )
            root.joinpath(".env.example").write_text(
                "WEB_CLIENT_SECRET=example-test-secret\n", encoding="utf-8"
            )
            with patch.object(publish, "ROOT", root):
                values = publish.private_values()
            self.assertIn("private-test-secret", values)
            self.assertIn("overlay-test-secret", values)
            self.assertIn("12345678-", values)
            self.assertNotIn("example-test-secret", values)
            self.assertNotIn("8000", values)

    def test_credential_patterns_detect_synthetic_tokens(self):
        self.assertTrue(publish.PATTERNS["GitHub token"].search("ghp_" + "a" * 30))
        token = "eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c" * 20
        self.assertTrue(publish.PATTERNS["access token"].search(token))
        self.assertFalse(publish.PATTERNS["GitHub token"].search("GITHUB_TOKEN="))
