import unittest

from scanner import luxalgo_strong_weak_levels, strong_level_record


def candles(high, low, close):
    length = len(close)
    return {
        "time": [1_700_000_000 + 3600 * index for index in range(length)],
        "open": list(close),
        "high": list(high),
        "low": list(low),
        "close": list(close),
        "vol": [100.0] * length,
        "amount": [1000.0] * length,
    }


class LuxAlgoStrongWeakTests(unittest.TestCase):
    def test_bullish_bias_marks_trailing_bottom_strong(self):
        data = candles(
            [10, 11, 12, 13, 14, 16, 15, 14, 13, 17, 18],
            [8, 5, 6, 7, 8, 9, 10, 9, 8, 9, 10],
            [9, 10, 11, 12, 13, 14, 14, 13, 14, 17, 18],
        )
        result = luxalgo_strong_weak_levels(data, swing_length=3)

        self.assertEqual(result["swing_bias"], "BULLISH")
        self.assertEqual(result["strong_low"]["classification"], "STRONG_LOW")
        self.assertEqual(result["strong_low"]["level"], 5)
        self.assertIsNone(result["strong_high"])
        self.assertEqual(result["trailing_high"]["classification"], "WEAK_HIGH")

    def test_bearish_bias_marks_trailing_top_strong(self):
        data = candles(
            [10, 11, 12, 13, 14, 16, 15, 14, 13, 12, 11],
            [8, 5, 6, 7, 8, 9, 10, 9, 8, 4, 3],
            [9, 10, 11, 12, 13, 14, 14, 13, 8, 4.5, 4],
        )
        result = luxalgo_strong_weak_levels(data, swing_length=3)

        self.assertEqual(result["swing_bias"], "BEARISH")
        self.assertEqual(result["strong_high"]["classification"], "STRONG_HIGH")
        self.assertEqual(result["strong_high"]["level"], 16)
        self.assertIsNone(result["strong_low"])
        self.assertEqual(result["trailing_low"]["classification"], "WEAK_LOW")

    def test_record_uses_btc_as_preference_not_filter(self):
        length = 120
        high = [12.0] * length
        low = [8.0] * length
        close = [10.0] * length
        low[10] = 5.0
        high[60] = 20.0
        low[111] = 3.5
        close[111:] = [4.0] * (length - 111)
        data = candles(high, low, close)
        ticker = {
            "lastPrice": 4,
            "bid1": 3.99,
            "ask1": 4.01,
            "amount24": 5_000_000,
            "holdVol": 1_000_000,
            "fundingRate": 0.0001,
        }

        record = strong_level_record("TEST_USDT", data, ticker, {"regime": "BULLISH"})

        self.assertIsNotNone(record)
        self.assertEqual(record["classification"], "STRONG_HIGH")
        self.assertEqual(record["btc_preference"], "COUNTER_BIAS")


if __name__ == "__main__":
    unittest.main()
