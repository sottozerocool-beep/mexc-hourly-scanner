#!/usr/bin/env python3
"""Strict publication wrapper for the MEXC Strong Level scanner.

The core scanner keeps structural Strong Low/High counts for the whole analyzed
universe.  This wrapper rebuilds the published nearest arrays exclusively from
the complete validated nearby radar so every published nearest item satisfies
the same expected-side, closed-candle and ATR constraints as nearby levels.
"""

from __future__ import annotations

from typing import Any

import scanner


def _publishable_level(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    distance_atr = scanner.finite_float(item.get("distance_atr"))
    return bool(
        distance_atr is not None
        and 0.0 <= distance_atr <= scanner.MAX_NEAR_STRONG_LEVEL_ATR
        and item.get("price_on_expected_side") is True
        and item.get("latest_closed_candle_respected_level") is True
    )


def _nearest_from_nearby(report: dict[str, Any], classification: str) -> list[dict[str, Any]]:
    nearby = report.get("nearby_strong_levels")
    if not isinstance(nearby, list):
        raise scanner.ScanError("nearby_strong_levels must be a list before rebuilding nearest levels")

    candidates = [
        item
        for item in nearby
        if isinstance(item, dict)
        and item.get("classification") == classification
        and _publishable_level(item)
    ]
    return sorted(
        candidates,
        key=lambda item: (
            float(item["distance_atr"]),
            -(scanner.finite_float(item.get("turnover_24h")) or 0.0),
        ),
    )[: scanner.MAX_REPORTED]


def strict_validate_report(report: dict[str, Any]) -> None:
    """Apply core validation plus the fail-closed rules for nearest arrays."""

    scanner.validate_report(report)
    if report.get("scan_ok") is not True:
        return

    expected = {
        "nearest_strong_lows": ("STRONG_LOW", "LONG"),
        "nearest_strong_highs": ("STRONG_HIGH", "SHORT"),
    }
    for key, (classification, side) in expected.items():
        items = report.get(key)
        if not isinstance(items, list):
            raise scanner.ScanError(f"Report field {key} must be a list")
        for item in items:
            if not isinstance(item, dict):
                raise scanner.ScanError(f"{key} records must be objects")
            symbol = item.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                raise scanner.ScanError(f"{key} requires a non-empty symbol")
            if item.get("classification") != classification or item.get("side") != side:
                raise scanner.ScanError(f"{symbol}: nearest side/classification mismatch")
            identity = item.get("asset_identity")
            if not isinstance(identity, dict):
                raise scanner.ScanError(f"{symbol}: missing official MEXC asset identity")
            if identity.get("source") != "Official MEXC Futures contract metadata":
                raise scanner.ScanError(f"{symbol}: invalid asset identity source")
            if identity.get("contract_symbol") != symbol:
                raise scanner.ScanError(f"{symbol}: asset identity/contract symbol mismatch")
            if not _publishable_level(item):
                raise scanner.ScanError(
                    f"{symbol}: nearest Strong level is not publishable on the expected side"
                )


def rebuild_published_nearest(report: dict[str, Any]) -> dict[str, Any]:
    """Rebuild nearest arrays from the complete, already fail-closed nearby set."""

    if report.get("scan_ok") is not True:
        strict_validate_report(report)
        return report

    nearby = report.get("nearby_strong_levels")
    if not isinstance(nearby, list):
        raise scanner.ScanError("nearby_strong_levels must be a list")
    invalid_nearby = [
        str(item.get("symbol") or "UNKNOWN")
        for item in nearby
        if not _publishable_level(item)
    ]
    if invalid_nearby:
        raise scanner.ScanError(
            "Complete nearby radar contains non-publishable levels: " + ", ".join(invalid_nearby)
        )

    report["nearest_strong_lows"] = _nearest_from_nearby(report, "STRONG_LOW")
    report["nearest_strong_highs"] = _nearest_from_nearby(report, "STRONG_HIGH")
    strict_validate_report(report)
    return report


def main() -> int:
    try:
        report = rebuild_published_nearest(scanner.scan_market())
    except Exception as exc:
        report = scanner.error_report(exc)

    strict_validate_report(report)
    scanner.save_outputs(report)
    print(scanner.report_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
