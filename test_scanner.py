import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import scanner


def synthetic_kline(symbol: str, count: int = 760, bearish: bool = False):
    now = int(time.time())
    current_hour = now - now % 3600
    times = [current_hour - (count - index) * 3600 for index in range(count)]
    close = []
    for index in range(count):
        trend = -0.08 * index if bearish else 0.08 * index
        wave = 2.5 * __import__("math").sin(index / 8.0)
        close.append(100.0 + trend + wave)
    if bearish:
        close = [max(2.0, value) for value in close]
    open_ = [close[0]] + close[:-1]
    high = [max(o, c) + 0.7 for o, c in zip(open_, close)]
    low = [min(o, c) - 0.7 for o, c in zip(open_, close)]
    volume = [1000.0 + (index % 17) * 10 for index in range(count)]
    return {
        "success": True,
        "code": 0,
        "data": {
            "time": times,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "vol": volume,
            "amount": [v * c for v, c in zip(volume, close)],
        },
    }


def contract(symbol: str):
    return {
        "symbol": symbol,
        "quoteCoin": "USDT",
        "settleCoin": "USDT",
        "futureType": 1,
        "state": 0,
        "isHidden": False,
        "preMarket": False,
        "type": 1,
        "createTime": int((time.time() - 365 * 86400) * 1000),
        "contractSize": 1,
    }


def ticker(symbol: str, price: float):
    return {
        "symbol": symbol,
        "lastPrice": price,
        "bid1": price * 0.9999,
        "ask1": price * 1.0001,
        "amount24": 50_000_000,
        "holdVol": 1_000_000,
        "indexPrice": price,
        "fairPrice": price,
        "fundingRate": 0,
        "timestamp": int(time.time() * 1000),
    }


class ScannerTests(unittest.TestCase):
    def test_parse_drops_forming_candle(self):
        now = int(time.time())
        hour = now - now % 3600
        payload = {
            "data": {
                "time": [hour - 7200, hour - 3600, hour],
                "open": [1, 2, 3], "high": [2, 3, 4], "low": [0.5, 1.5, 2.5],
                "close": [1.5, 2.5, 3.5], "vol": [10, 20, 30],
            }
        }
        parsed = scanner.parse_closed_klines("TEST_USDT", payload, now)
        self.assertEqual(parsed["time"], [hour - 7200, hour - 3600])

    def test_btc_regime_on_rising_market(self):
        payload = synthetic_kline("BTC_USDT")
        candles = scanner.parse_closed_klines("BTC_USDT", payload, int(time.time()))
        context = scanner.btc_context(candles, ticker("BTC_USDT", candles["close"][-1]))
        self.assertEqual(context["regime"], "BULLISH")
        self.assertGreaterEqual(context["regime_score"], 55)

    def test_full_scan_with_mocked_public_api(self):
        btc_payload = synthetic_kline("BTC_USDT")
        eth_payload = synthetic_kline("ETH_USDT")
        btc_price = btc_payload["data"]["close"][-1]
        eth_price = eth_payload["data"]["close"][-1]
        contracts_payload = {"success": True, "code": 0, "data": [contract("BTC_USDT"), contract("ETH_USDT")]}
        tickers_payload = {"success": True, "code": 0, "data": [ticker("BTC_USDT", btc_price), ticker("ETH_USDT", eth_price)]}

        def fake_get(path, params=None):
            if path.endswith("/detail/country"):
                return contracts_payload
            if path.endswith("/ticker"):
                return tickers_payload
            if "BTC_USDT" in path:
                return btc_payload
            if "ETH_USDT" in path:
                return eth_payload
            raise AssertionError(path)

        with patch.object(scanner, "get_json", side_effect=fake_get):
            report = scanner.scan_market()
        self.assertTrue(report["scan_ok"])
        self.assertEqual(report["contracts_retrieved"], 2)
        self.assertEqual(report["contracts_analyzed"], 2)
        self.assertIn(report["decision"], {"NO_TRADE", "QUALIFIED_LONG"})
        json.dumps(report)

    def test_error_report_never_fabricates_market_values(self):
        report = scanner.error_report(RuntimeError("network unavailable"))
        self.assertFalse(report["scan_ok"])
        self.assertIsNone(report["btc"]["price"])
        self.assertEqual(report["qualified_setups"], [])
        self.assertEqual(report["decision"], "NO_TRADE")

    def test_outputs_are_valid(self):
        report = scanner.error_report(RuntimeError("test"))
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scanner, "OUTPUT_DIR", Path(tmp)):
                scanner.save_outputs(report)
                loaded = json.loads((Path(tmp) / "latest_report.json").read_text())
                self.assertEqual(loaded["decision"], "NO_TRADE")
                self.assertTrue((Path(tmp) / "latest_report.md").exists())


if __name__ == "__main__":
    unittest.main()
