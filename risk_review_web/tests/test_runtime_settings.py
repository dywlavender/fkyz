from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from risk_review_web.environment import load_project_env
from risk_review_web import runtime_settings


class RuntimeSettingsTest(unittest.TestCase):
    def test_project_env_does_not_override_process_environment(self):
        with tempfile.TemporaryDirectory(prefix="risk-env-test-") as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "RISK_REVIEW_AGENT_MODEL=from-file\nRISK_REVIEW_AGENT_PROVIDER=anthropic\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"RISK_REVIEW_AGENT_MODEL": "from-process"},
                clear=True,
            ):
                load_project_env(env_file)
                self.assertEqual(os.environ["RISK_REVIEW_AGENT_MODEL"], "from-process")
                self.assertEqual(os.environ["RISK_REVIEW_AGENT_PROVIDER"], "anthropic")


if __name__ == "__main__":
    unittest.main()
