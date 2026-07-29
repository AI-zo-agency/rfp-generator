"""LangSmith tracing bootstrap tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.core.langsmith_tracing import configure_langsmith_tracing


class LangsmithTracingConfigTests(unittest.TestCase):
    def test_disabled_when_flag_false(self) -> None:
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.langsmith_tracing = False
            mock_settings.langsmith_api_key = "lsv2_pt_test"
            mock_settings.langsmith_project = "proposal generation"
            mock_settings.langsmith_endpoint = "https://api.smith.langchain.com"
            self.assertFalse(configure_langsmith_tracing())

    def test_enabled_syncs_os_environ(self) -> None:
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.langsmith_tracing = True
            mock_settings.langsmith_api_key = "lsv2_pt_test"
            mock_settings.langsmith_project = '"proposal generation"'
            mock_settings.langsmith_endpoint = "https://api.smith.langchain.com"
            env_before = {
                k: os.environ.get(k)
                for k in (
                    "LANGSMITH_TRACING",
                    "LANGSMITH_API_KEY",
                    "LANGSMITH_PROJECT",
                    "LANGSMITH_ENDPOINT",
                    "LANGCHAIN_TRACING_V2",
                )
            }
            try:
                self.assertTrue(configure_langsmith_tracing())
                self.assertEqual(os.environ["LANGSMITH_TRACING"], "true")
                self.assertEqual(os.environ["LANGCHAIN_TRACING_V2"], "true")
                self.assertEqual(os.environ["LANGSMITH_API_KEY"], "lsv2_pt_test")
                self.assertEqual(os.environ["LANGSMITH_PROJECT"], "proposal generation")
                self.assertEqual(
                    os.environ["LANGSMITH_ENDPOINT"],
                    "https://api.smith.langchain.com",
                )
            finally:
                for key, value in env_before.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_missing_api_key_returns_false(self) -> None:
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.langsmith_tracing = True
            mock_settings.langsmith_api_key = ""
            mock_settings.langsmith_project = "proposal generation"
            mock_settings.langsmith_endpoint = "https://api.smith.langchain.com"
            self.assertFalse(configure_langsmith_tracing())


if __name__ == "__main__":
    unittest.main()
