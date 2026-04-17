import os
import unittest
from unittest.mock import patch

import bot


class ConfigTests(unittest.TestCase):
    def test_parse_symbols_default_sol_only(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(bot.parse_symbols_from_env(), ("SOL/USDT",))

    def test_parse_symbols_from_env_comma_separated(self) -> None:
        with patch.dict(os.environ, {"TRADING_PAIRS": " sol/usdt , btc/usdt "}, clear=True):
            self.assertEqual(bot.parse_symbols_from_env(), ("SOL/USDT", "BTC/USDT"))

    def test_parse_symbols_raises_for_empty_symbols(self) -> None:
        with patch.dict(os.environ, {"TRADING_PAIRS": " , , "}, clear=True):
            with self.assertRaises(ValueError):
                bot.parse_symbols_from_env()

    def test_load_symbol_rules_uses_defaults_without_json(self) -> None:
        symbols = ("SOL/USDT",)
        with patch.dict(os.environ, {}, clear=True):
            rules = bot.load_symbol_rules(symbols)

        self.assertEqual(rules["SOL/USDT"]["sma_period"], 200)
        self.assertEqual(rules["SOL/USDT"]["rsi_period"], 14)
        self.assertEqual(rules["SOL/USDT"]["buy_rsi_max"], 35.0)
        self.assertEqual(rules["SOL/USDT"]["sell_rsi_min"], 70.0)

    def test_load_symbol_rules_warns_when_symbol_missing_in_json(self) -> None:
        symbols = ("SOL/USDT", "BTC/USDT")
        env = {
            "SYMBOL_RULES_JSON": '{"BTC/USDT":{"sma_period":180,"rsi_period":12,"buy_rsi_max":30,"sell_rsi_min":75}}'
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertLogs("bot", level="WARNING") as logs:
                rules = bot.load_symbol_rules(symbols)

        self.assertIn("No explicit rule override found for SOL/USDT", "\n".join(logs.output))
        self.assertEqual(rules["BTC/USDT"]["sma_period"], 180)
        self.assertEqual(rules["SOL/USDT"]["sma_period"], 200)

    def test_load_symbol_rules_warns_for_unknown_override_symbol(self) -> None:
        symbols = ("SOL/USDT",)
        env = {
            "SYMBOL_RULES_JSON": '{"DOGE/USDT":{"sma_period":100,"rsi_period":10,"buy_rsi_max":25,"sell_rsi_min":80}}'
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertLogs("bot", level="WARNING") as logs:
                bot.load_symbol_rules(symbols)

        self.assertIn("SYMBOL_RULES_JSON contains DOGE/USDT", "\n".join(logs.output))

    def test_load_symbol_rules_rejects_invalid_json(self) -> None:
        symbols = ("SOL/USDT",)
        with patch.dict(os.environ, {"SYMBOL_RULES_JSON": "not-json"}, clear=True):
            with self.assertRaises(ValueError):
                bot.load_symbol_rules(symbols)

    def test_load_symbol_rules_rejects_non_dict_override(self) -> None:
        symbols = ("SOL/USDT",)
        with patch.dict(os.environ, {"SYMBOL_RULES_JSON": '{"SOL/USDT": 1}'}, clear=True):
            with self.assertRaises(ValueError):
                bot.load_symbol_rules(symbols)

    def test_load_symbol_rules_rejects_invalid_threshold_order(self) -> None:
        symbols = ("SOL/USDT",)
        env = {
            "SYMBOL_RULES_JSON": '{"SOL/USDT":{"buy_rsi_max":80,"sell_rsi_min":70}}'
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                bot.load_symbol_rules(symbols)

    def test_build_thresholds_message_includes_symbol_values(self) -> None:
        rules = {
            "SOL/USDT": {
                "sma_period": 150,
                "rsi_period": 12,
                "buy_rsi_max": 33.0,
                "sell_rsi_min": 68.0,
            }
        }
        message = bot.build_thresholds_message(rules)

        self.assertIn("Active Signal Thresholds", message)
        self.assertIn("SOL/USDT", message)
        self.assertIn("SMA period: <b>150</b>", message)
        self.assertIn("RSI period: <b>12</b>", message)
        self.assertIn("Buy RSI max: <b>33.00</b>", message)
        self.assertIn("Sell RSI min: <b>68.00</b>", message)


if __name__ == "__main__":
    unittest.main()
