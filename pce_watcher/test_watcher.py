import argparse
import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import MarketSnapshot, UTC, WatcherError
import watcher


class FlashMessageTests(unittest.TestCase):
    def test_flash_contains_critical_values_without_waiting_for_btc(self) -> None:
        release_at = dt.datetime(2026, 8, 26, 12, 30, tzinfo=UTC)
        message = watcher.flash_message(
            config={
                "forecast_core_pce_mom": 0.2,
                "previous_core_pce_mom": 0.1,
            },
            release_at=release_at,
            actual=0.3,
            source_url="https://www.bea.gov/release",
            source_name="BEA news release",
            detected_at=release_at + dt.timedelta(seconds=1.4),
        )

        self.assertIn("FLASH CORE PCE USA", message)
        self.assertIn("Actual: <b>0,3%</b>", message)
        self.assertIn("Forecast: <b>0,2%</b>", message)
        self.assertIn("Previous: <b>0,1%</b>", message)
        self.assertIn("BTC rialzista", message)
        self.assertIn("BTC ribassista", message)
        self.assertIn("1,4 secondi", message)
        self.assertNotIn("BTC/USDT:", message)


class TelegramSafetyTests(unittest.TestCase):
    def test_network_error_never_logs_token_or_stops_watcher(self) -> None:
        token = "dummy-token-that-must-not-be-logged"
        error = WatcherError(f"Errore di rete su https://api.telegram.org/bot{token}")

        with (
            patch.dict(
                os.environ,
                {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "123"},
            ),
            patch("watcher.http_request", side_effect=error) as request,
            patch("watcher.time.sleep"),
            self.assertLogs("pce_watcher", level="WARNING") as logs,
        ):
            sent = watcher.Telegram().send("prova")

        self.assertFalse(sent)
        self.assertEqual(request.call_count, 2)
        self.assertNotIn(token, "\n".join(logs.output))


class LiveOrderingTests(unittest.TestCase):
    def test_flash_is_sent_before_post_release_btc_request(self) -> None:
        release_at = dt.datetime(2026, 8, 26, 12, 30, tzinfo=UTC)
        now = release_at + dt.timedelta(seconds=1)
        config = {
            "release_date": "2026-08-26",
            "release_title": "Personal Income and Outlays, July 2026",
            "release_at_utc": release_at.isoformat(),
            "release_url": "https://www.bea.gov/release",
            "data_month": "July 2026",
            "poll_seconds": 2.0,
            "max_wait_minutes": 20,
            "followup_minutes": [5, 15],
            "pre_release_notice_minutes": 5,
            "forecast_core_pce_mom": 0.2,
            "previous_core_pce_mom": 0.1,
        }
        events: list[tuple[str, str]] = []

        class FakeTelegram:
            def send(self, message: str) -> bool:
                events.append(("send", message))
                return True

        def fake_price() -> MarketSnapshot:
            events.append(("btc", "price"))
            return MarketSnapshot(now, 100_000.0, "test feed")

        def fake_poll(**_: object) -> tuple[float, str, str, dt.datetime]:
            events.append(("poll", "bea"))
            return 0.3, "https://www.bea.gov/release", "BEA news release", now

        args = argparse.Namespace(
            config="unused.json",
            output_dir="",
            release_at=None,
            test_actual=None,
        )

        with tempfile.TemporaryDirectory() as output_dir:
            args.output_dir = output_dir
            with (
                patch("watcher.load_config", return_value=config),
                patch("watcher.utc_now", return_value=now),
                patch("watcher.Telegram", return_value=FakeTelegram()),
                patch("watcher.poll_core_pce", side_effect=fake_poll),
                patch("watcher.get_btc_price", side_effect=fake_price),
            ):
                self.assertEqual(watcher.run_live(args), 0)
            self.assertTrue((Path(output_dir) / "latest.json").exists())

        poll_index = next(i for i, event in enumerate(events) if event[0] == "poll")
        flash_index = next(
            i for i, event in enumerate(events)
            if event[0] == "send" and "FLASH CORE PCE USA" in event[1]
        )
        post_release_btc_index = next(
            i for i, event in enumerate(events)
            if i > poll_index and event[0] == "btc"
        )
        self.assertLess(poll_index, flash_index)
        self.assertLess(flash_index, post_release_btc_index)


if __name__ == "__main__":
    unittest.main()
