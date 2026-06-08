import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


class DpskClientConfigTest(unittest.TestCase):
    def test_factory_supports_dpsk_and_deepseek_aliases(self) -> None:
        from agents.llm_client import DpskClient, get_llm_client

        self.assertIsInstance(get_llm_client("dpsk"), DpskClient)
        self.assertIsInstance(get_llm_client("deepseek"), DpskClient)
        self.assertEqual(get_llm_client("dpsk").name, "dpsk")

    def test_dpsk_client_reads_dpsk_env_names(self) -> None:
        from agents.llm_client import DpskClient

        with patch.dict(
            os.environ,
            {
                "DPSK_API_KEY": "sk-dpsk",
                "DPSK_BASE_URL": "https://example.test/anthropic",
                "DPSK_MODEL": "deepseek-v4-pro",
                "DPSK_MAX_TOKENS": "1234",
            },
            clear=True,
        ):
            config = DpskClient().config()

        self.assertEqual(config.api_key, "sk-dpsk")
        self.assertEqual(config.base_url, "https://example.test/anthropic")
        self.assertEqual(config.model, "deepseek-v4-pro")
        self.assertEqual(config.max_tokens, 1234)

    def test_dpsk_client_does_not_fall_back_to_longcat_api_key(self) -> None:
        from agents.llm_client import DpskClient

        with patch.dict(os.environ, {"LONGCAT_API_KEY": "sk-longcat"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DPSK_API_KEY"):
                DpskClient().config()


if __name__ == "__main__":
    unittest.main()
