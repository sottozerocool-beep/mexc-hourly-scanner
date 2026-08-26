#!/usr/bin/env python3
"""Live Core PCE watcher with Italian Telegram alerts and BTC follow-ups."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import html
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Mapping
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from core import (
    UTC,
    MarketSnapshot,
    WatcherError,
    confirmation,
    format_it,
    get_btc_price,
    historical_price_before,
    http_request,
    pct_change,
    poll_core_pce,
    probability_text,
    scenario_for_actual,
    surprise_text,
    utc_now,
    volume_ratio,
)

LOGGER = logging.getLogger("pce_watcher")
ROME = ZoneInfo("Europe/Rome")
NEW_YORK = ZoneInfo("America/New_York")


class Telegram:
    def __init__(self) -> None:
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, message: str) -> bool:
        if not self.enabled:
            LOGGER.warning("Telegram non configurato: messaggio salvato solo nell'output")
            return False
        payload = urlencode(
            {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                status, _, _ = http_request(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    method="POST",
                    body=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=5,
                )
            except WatcherError:
                # The underlying exception includes the request URL, which embeds the
                # bot token. Never copy it into the GitHub Actions log.
                LOGGER.warning(
                    "Telegram non raggiungibile (tentativo %d/%d)", attempt, attempts
                )
            else:
                if status == 200:
                    LOGGER.info("Messaggio Telegram inviato")
                    return True
                LOGGER.warning(
                    "Telegram HTTP %s (tentativo %d/%d)", status, attempt, attempts
                )
            if attempt < attempts:
                time.sleep(0.5)
        return False


def parse_iso(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("L'orario deve includere il fuso")
    return parsed.astimezone(UTC)


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "release_date", "release_title", "release_at_utc", "release_url",
        "data_month", "poll_seconds", "max_wait_minutes", "followup_minutes",
    }
    missing = required.difference(data)
    if missing:
        raise WatcherError("Config incompleta: " + ", ".join(sorted(missing)))
    return data


def sleep_until(target: dt.datetime, heartbeat: int = 60) -> None:
    while True:
        remaining = (target - utc_now()).total_seconds()
        if remaining <= 0:
            return
        LOGGER.info("Attesa: %.0f secondi", remaining)
        time.sleep(min(max(0.5, remaining), heartbeat))


def snapshot_to_dict(value: MarketSnapshot | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "captured_at_utc": value.captured_at.isoformat(),
        "price": value.price,
        "source": value.source,
    }


def probability_lines(actual: float) -> tuple[str, str]:
    scenario = scenario_for_actual(actual)
    return (
        probability_text(scenario.bullish_low, scenario.bullish_high),
        probability_text(scenario.bearish_low, scenario.bearish_high),
    )


def activation_message(config: Mapping[str, Any], release_at: dt.datetime) -> str:
    lines = [
        "🟡 <b>WATCHER CORE PCE ATTIVO</b>",
        f"Rilascio: <b>{release_at.astimezone(ROME):%d/%m/%Y %H:%M} ora italiana</b>",
        "Fonte primaria: <b>BEA ufficiale</b>",
    ]
    if config.get("forecast_core_pce_mom") is not None:
        lines.append(
            f"Forecast: <b>{format_it(float(config['forecast_core_pce_mom']), 1)}%</b>"
        )
    if config.get("previous_core_pce_mom") is not None:
        lines.append(
            f"Previous: <b>{format_it(float(config['previous_core_pce_mom']), 1)}%</b>"
        )
    poll_seconds = float(config["poll_seconds"])
    lines.append(
        "Dal rilascio controllerò la fonte ogni "
        f"{format_it(poll_seconds, 1)} secondi."
    )
    return "\n".join(lines)


def flash_message(
    *,
    config: Mapping[str, Any],
    release_at: dt.datetime,
    actual: float,
    source_url: str,
    source_name: str,
    detected_at: dt.datetime,
) -> str:
    """Build the lowest-latency alert without waiting for any market request."""

    forecast = config.get("forecast_core_pce_mom")
    previous = config.get("previous_core_pce_mom")
    forecast = float(forecast) if forecast is not None else None
    previous = float(previous) if previous is not None else None
    scenario = scenario_for_actual(actual)
    bullish, bearish = probability_lines(actual)
    lag = max(0.0, (detected_at - release_at).total_seconds())

    lines = [
        "🚨 <b>FLASH CORE PCE USA</b>",
        "",
        f"Actual: <b>{format_it(actual, 1)}%</b>",
        (
            f"Forecast: <b>{format_it(forecast, 1)}%</b>"
            if forecast is not None else "Forecast: non configurato"
        ),
        (
            f"Previous: <b>{format_it(previous, 1)}%</b>"
            if previous is not None else "Previous: non configurato"
        ),
        f"Sorpresa: <b>{html.escape(surprise_text(actual, forecast))}</b>",
        "",
        f"Lettura: <b>{html.escape(scenario.label)}</b>",
        f"BTC rialzista: <b>{bullish}</b>",
        f"BTC ribassista: <b>{bearish}</b>",
        f"Rilevato dopo circa <b>{format_it(lag, 1)} secondi</b>.",
        f'<a href="{html.escape(source_url)}">Fonte: {html.escape(source_name)}</a>',
        "",
        "Prezzo e reazione BTC in arrivo subito dopo.",
    ]
    return "\n".join(lines)


def initial_messages(
    *,
    config: Mapping[str, Any],
    release_at: dt.datetime,
    actual: float,
    source_url: str,
    source_name: str,
    detected_at: dt.datetime,
    baseline: MarketSnapshot | None,
    current: MarketSnapshot | None,
) -> tuple[str, str, str]:
    forecast = config.get("forecast_core_pce_mom")
    previous = config.get("previous_core_pce_mom")
    forecast = float(forecast) if forecast is not None else None
    previous = float(previous) if previous is not None else None
    scenario = scenario_for_actual(actual)
    bullish, bearish = probability_lines(actual)
    move = pct_change(current.price, baseline.price) if current and baseline else None
    lag = max(0.0, (detected_at - release_at).total_seconds())

    tg = [
        "🚨 <b>CORE PCE USA PUBBLICATO</b>",
        "",
        f"Actual: <b>{format_it(actual, 1)}%</b>",
        f"Forecast: <b>{format_it(forecast, 1)}%</b>" if forecast is not None else "Forecast: non configurato",
        f"Previous: <b>{format_it(previous, 1)}%</b>" if previous is not None else "Previous: non configurato",
        f"Sorpresa: <b>{html.escape(surprise_text(actual, forecast))}</b>",
        "",
        f"Lettura: <b>{html.escape(scenario.label)}</b>",
        f"BTC rialzista: <b>{bullish}</b>",
        f"BTC ribassista: <b>{bearish}</b>",
    ]
    if current:
        tg.extend(
            [
                f"BTC/USDT: <b>{format_it(current.price, 0)}</b>",
                f"Feed BTC: <b>{html.escape(current.source)}</b>",
            ]
        )
    if move is not None:
        tg.append(f"Reazione iniziale: <b>{format_it(move, 2)}%</b>")
    tg.extend(
        [
            "",
            html.escape(scenario.explanation),
            f"Rilevato dopo circa <b>{format_it(lag, 1)} secondi</b>.",
            f'<a href="{html.escape(source_url)}">Fonte: {html.escape(source_name)}</a>',
            "",
            "⚠️ Probabilità indicative, non segnale di ingresso. Seguiranno verifiche a +5 e +15 minuti.",
        ]
    )

    md = [
        "## Core PCE live",
        "",
        f"- **Actual:** {actual:.1f}%",
        f"- **Forecast:** {forecast:.1f}%" if forecast is not None else "- **Forecast:** non configurato",
        f"- **Previous:** {previous:.1f}%" if previous is not None else "- **Previous:** non configurato",
        f"- **Sorpresa:** {surprise_text(actual, forecast)}",
        f"- **Lettura:** {scenario.label}",
        f"- **BTC rialzista:** {bullish}",
        f"- **BTC ribassista:** {bearish}",
    ]
    if current:
        md.extend(
            [
                f"- **BTC/USDT:** {current.price:,.2f}",
                f"- **Feed BTC:** {current.source}",
            ]
        )
    if move is not None:
        md.append(f"- **Reazione iniziale:** {move:+.2f}%")
    md.extend(
        [
            f"- **Latenza di rilevazione:** {lag:.1f} secondi",
            f"- **Fonte:** [{source_name}]({source_url})",
            "",
            scenario.explanation,
            "",
            "> Analisi automatica probabilistica; non è consulenza finanziaria né ordine di trading.",
        ]
    )

    date = release_at.astimezone(NEW_YORK).date().isoformat()
    title = f"[PCE LIVE] {date} | Core PCE {actual:.1f}%"
    if move is not None:
        title += f" | BTC {move:+.2f}%"
    return "\n".join(tg), "\n".join(md), title


def followup_messages(
    *, minutes: int, actual: float, baseline_price: float | None,
    current: MarketSnapshot | None, release_at: dt.datetime,
) -> tuple[str, str, dict[str, Any]]:
    scenario = scenario_for_actual(actual)
    move = pct_change(current.price, baseline_price) if current else None
    volume = volume_ratio(release_at, utc_now())
    label, detail = confirmation(scenario, move, volume.ratio)

    tg = [
        f"📊 <b>BTC A +{minutes} MINUTI DAL CORE PCE</b>",
        f"Esito: <b>{html.escape(label)}</b>",
    ]
    if current:
        tg.extend(
            [
                f"BTC/USDT: <b>{format_it(current.price, 0)}</b>",
                f"Feed BTC: <b>{html.escape(current.source)}</b>",
            ]
        )
    if move is not None:
        tg.append(f"Variazione dal pre-release: <b>{format_it(move, 2)}%</b>")
    if volume.ratio is not None:
        tg.append(f"Volume medio 1m: <b>{format_it(volume.ratio, 1)}×</b> la media precedente")
    tg.append(html.escape(detail))

    md = [f"### Verifica BTC a +{minutes} minuti", "", f"- **Esito:** {label}"]
    if current:
        md.extend([f"- **BTC/USDT:** {current.price:,.2f}", f"- **Feed BTC:** {current.source}"])
    if move is not None:
        md.append(f"- **Variazione dal pre-release:** {move:+.2f}%")
    if volume.ratio is not None:
        md.append(f"- **Rapporto volume 1m:** {volume.ratio:.2f}x")
    if volume.source:
        md.append(f"- **Feed volume:** {volume.source}")
    md.extend(["", detail])

    payload = {
        "minutes": minutes,
        "captured_at_utc": current.captured_at.isoformat() if current else None,
        "btc_price": current.price if current else None,
        "btc_source": current.source if current else None,
        "btc_move_pct": move,
        "volume_ratio": volume.ratio,
        "volume_source": volume.source,
        "confirmation": label,
        "detail": detail,
    }
    return "\n".join(tg), "\n".join(md), payload


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_live(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    release_at = parse_iso(args.release_at or config["release_at_utc"])
    release_date = dt.date.fromisoformat(config["release_date"])
    if release_at.astimezone(NEW_YORK).date() != release_date:
        raise WatcherError("release_date e release_at_utc non coincidono")

    today_ny = utc_now().astimezone(NEW_YORK).date()
    if (
        today_ny != release_date
        and args.test_actual is None
        and args.confirmed_actual is None
    ):
        LOGGER.info("Oggi non è il giorno configurato: uscita senza errore")
        return 0
    if release_at - utc_now() > dt.timedelta(hours=4):
        LOGGER.info("Rilascio oltre quattro ore: uscita senza errore")
        return 0

    telegram = Telegram()
    # Send this immediately when the job starts. Besides informing the user, it
    # verifies the Telegram path while there is still time to intervene.
    telegram.send(activation_message(config, release_at))
    notice_minutes = int(config.get("pre_release_notice_minutes", 5))
    if utc_now() < release_at - dt.timedelta(minutes=notice_minutes):
        sleep_until(release_at - dt.timedelta(minutes=notice_minutes))

    if utc_now() < release_at - dt.timedelta(seconds=10):
        sleep_until(release_at - dt.timedelta(seconds=10), heartbeat=10)
    baseline: MarketSnapshot | None
    try:
        baseline = get_btc_price() if utc_now() <= release_at + dt.timedelta(seconds=10) else historical_price_before(release_at)
    except WatcherError as exc:
        LOGGER.warning("Baseline BTC non disponibile: %s", exc)
        baseline = historical_price_before(release_at)

    if utc_now() < release_at:
        sleep_until(release_at, heartbeat=2)

    if args.confirmed_actual is not None:
        actual = float(args.confirmed_actual)
        source_url = str(config["release_url"])
        source_name = "BEA ufficiale (valore confermato)"
        detected_at = utc_now()
    elif args.test_actual is not None:
        actual = float(args.test_actual)
        source_url = str(config["release_url"])
        source_name = "simulazione controllata"
        detected_at = utc_now()
    else:
        actual, source_url, source_name, detected_at = poll_core_pce(
            release_url=str(config["release_url"]),
            expected_title=str(config["release_title"]),
            data_month=str(config["data_month"]),
            poll_seconds=float(config["poll_seconds"]),
            deadline=release_at + dt.timedelta(minutes=int(config["max_wait_minutes"])),
            api_key=os.getenv("BEA_API_KEY") or None,
        )

    # This is deliberately before get_btc_price(): the official number and its
    # scenario must reach Telegram without waiting for any exchange feed.
    telegram.send(
        flash_message(
            config=config,
            release_at=release_at,
            actual=actual,
            source_url=source_url,
            source_name=source_name,
            detected_at=detected_at,
        )
    )

    try:
        current = get_btc_price()
    except WatcherError as exc:
        LOGGER.warning("BTC al rilascio non disponibile: %s", exc)
        current = None

    tg, md, title = initial_messages(
        config=config,
        release_at=release_at,
        actual=actual,
        source_url=source_url,
        source_name=source_name,
        detected_at=detected_at,
        baseline=baseline,
        current=current,
    )
    telegram.send(tg)

    output = Path(args.output_dir)
    result = {
        "schema_version": 1,
        "release_title": config["release_title"],
        "release_at_utc": release_at.isoformat(),
        "release_at_italy": release_at.astimezone(ROME).isoformat(),
        "detected_at_utc": detected_at.isoformat(),
        "detection_lag_seconds": max(0.0, (detected_at - release_at).total_seconds()),
        "source_url": source_url,
        "source_name": source_name,
        "actual_core_pce_mom": actual,
        "forecast_core_pce_mom": config.get("forecast_core_pce_mom"),
        "previous_core_pce_mom": config.get("previous_core_pce_mom"),
        "scenario": dataclasses.asdict(scenario_for_actual(actual)),
        "btc_pre_release": snapshot_to_dict(baseline),
        "btc_at_detection": snapshot_to_dict(current),
        "btc_initial_move_pct": pct_change(current.price, baseline.price) if current and baseline else None,
        "followups": [],
    }
    write_json(output / "latest.json", result)
    (output / "initial.md").write_text(md + "\n", encoding="utf-8")
    (output / "title.txt").write_text(title + "\n", encoding="utf-8")
    (output / "telegram_initial.html").write_text(tg + "\n", encoding="utf-8")
    LOGGER.info("Core PCE rilevato: %.1f%%", actual)
    return 0


def run_followup(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    result_path = output / "latest.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    release_at = parse_iso(result["release_at_utc"])
    target = release_at + dt.timedelta(minutes=args.minutes)
    if utc_now() < target:
        sleep_until(target, heartbeat=30)
    try:
        current = get_btc_price()
    except WatcherError as exc:
        LOGGER.warning("BTC follow-up non disponibile: %s", exc)
        current = None
    baseline = result.get("btc_pre_release") or {}
    tg, md, payload = followup_messages(
        minutes=args.minutes,
        actual=float(result["actual_core_pce_mom"]),
        baseline_price=float(baseline["price"]) if baseline.get("price") is not None else None,
        current=current,
        release_at=release_at,
    )
    Telegram().send(tg)
    result.setdefault("followups", []).append(payload)
    write_json(result_path, result)
    (output / f"followup_{args.minutes}.md").write_text(md + "\n", encoding="utf-8")
    (output / f"telegram_followup_{args.minutes}.html").write_text(tg + "\n", encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Core PCE live watcher in italiano")
    sub = parser.add_subparsers(dest="command", required=True)

    live = sub.add_parser("live")
    live.add_argument("--config", default="pce_watcher/config.json")
    live.add_argument("--output-dir", default="output/pce_live")
    live.add_argument("--release-at")
    live.add_argument("--test-actual", type=float)
    live.add_argument("--confirmed-actual", type=float)

    follow = sub.add_parser("followup")
    follow.add_argument("--output-dir", default="output/pce_live")
    follow.add_argument("--minutes", type=int, required=True)
    return parser


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    args = build_parser().parse_args()
    try:
        return run_live(args) if args.command == "live" else run_followup(args)
    except KeyboardInterrupt:
        return 130
    except (WatcherError, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        LOGGER.error("Watcher terminato con errore: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
