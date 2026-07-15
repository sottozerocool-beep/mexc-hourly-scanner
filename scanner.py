#!/usr/bin/env python3
"""MEXC Futures Strong Low / Strong High scanner.

Uses only official, public MEXC Futures market-data endpoints. It never reads
account data and never places orders.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = os.getenv("MEXC_BASE_URL", "https://api.mexc.com")
TIMEFRAME = "Min60"
MAX_CANDLES = 760
MIN_CANDLES = 300
BTC_MIN_CANDLES = 720
MIN_TURNOVER = 2_000_000.0
MAX_SPREAD = 0.0020
MAX_FAIR_INDEX_DEVIATION = 0.0035
MIN_LISTING_AGE_SECONDS = 14 * 24 * 3600
MAX_TICKER_AGE_SECONDS = 15 * 60
MIN_QUALIFIED_SCORE = 80
MIN_WATCHLIST_SCORE = 72
MAX_REPORTED = 5
MIN_REWARD_RISK = 2.0
HTTP_TIMEOUT = 40
HTTP_ATTEMPTS = 5
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
USER_AGENT = "MEXC-Hourly-Strong-Level-Scanner/1.0"


class ScanError(RuntimeError):
    pass


class RateLimiter:
    """Stay below the documented 20 kline requests per two seconds."""

    def __init__(self, calls: int = 16, window: float = 2.05) -> None:
        self.calls = calls
        self.window = window
        self.timestamps: deque[float] = deque()
        self.lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while self.timestamps and now - self.timestamps[0] >= self.window:
                    self.timestamps.popleft()
                if len(self.timestamps) < self.calls:
                    self.timestamps.append(now)
                    return
                delay = self.window - (now - self.timestamps[0]) + 0.01
            time.sleep(max(0.01, delay))


RATE_LIMITER = RateLimiter()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    return (value or utc_now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    query = "?" + urllib.parse.urlencode(params) if params else ""
    url = BASE_URL + path + query
    last_error: Exception | None = None

    for attempt in range(1, HTTP_ATTEMPTS + 1):
        RATE_LIMITER.wait()
        try:
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ScanError("MEXC returned a non-object JSON response")
            if payload.get("success") is False or payload.get("code") not in (None, 0):
                raise ScanError(
                    f"MEXC error code={payload.get('code')}: "
                    f"{payload.get('message', 'message unavailable')}"
                )
            return payload
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                json.JSONDecodeError, ScanError) as exc:
            last_error = exc
            if attempt < HTTP_ATTEMPTS:
                time.sleep(min(8, 2 ** (attempt - 1)))

    raise ScanError(f"Public MEXC request failed after retries: {url}: {last_error}")


def percentile(values: list[float], quantile: float) -> float:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return 0.0
    if len(clean) == 1:
        return clean[0]
    position = (len(clean) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[lower]
    weight = position - lower
    return clean[lower] * (1 - weight) + clean[upper] * weight


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])
    return result


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result
    gains = [max(0.0, values[i] - values[i - 1]) for i in range(1, len(values))]
    losses = [max(0.0, values[i - 1] - values[i]) for i in range(1, len(values))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = value(avg_gain, avg_loss)
    for index in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[index - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index - 1]) / period
        result[index] = value(avg_gain, avg_loss)
    return result


def true_ranges(high: list[float], low: list[float], close: list[float]) -> list[float]:
    result = [high[0] - low[0]]
    for index in range(1, len(close)):
        result.append(max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        ))
    return result


def wilder(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    average = sum(values[:period]) / period
    result[period - 1] = average
    for index in range(period, len(values)):
        average = (average * (period - 1) + values[index]) / period
        result[index] = average
    return result


def atr_series(high: list[float], low: list[float], close: list[float], period: int = 14) -> list[float | None]:
    return wilder(true_ranges(high, low, close), period)


def dmi_adx(high: list[float], low: list[float], close: list[float], period: int = 14) -> tuple[list[float | None], list[float | None], list[float | None]]:
    length = len(close)
    plus_dm = [0.0] * length
    minus_dm = [0.0] * length
    for index in range(1, length):
        up = high[index] - high[index - 1]
        down = low[index - 1] - low[index]
        plus_dm[index] = up if up > down and up > 0 else 0.0
        minus_dm[index] = down if down > up and down > 0 else 0.0

    atr_values = wilder(true_ranges(high, low, close), period)
    plus_smoothed = wilder(plus_dm, period)
    minus_smoothed = wilder(minus_dm, period)
    plus_di: list[float | None] = [None] * length
    minus_di: list[float | None] = [None] * length
    dx: list[float | None] = [None] * length
    for index in range(length):
        atr_value = atr_values[index]
        if not atr_value or atr_value <= 0:
            continue
        plus_di[index] = 100.0 * (plus_smoothed[index] or 0.0) / atr_value
        minus_di[index] = 100.0 * (minus_smoothed[index] or 0.0) / atr_value
        denominator = plus_di[index] + minus_di[index]
        dx[index] = 0.0 if denominator == 0 else 100.0 * abs(plus_di[index] - minus_di[index]) / denominator

    adx: list[float | None] = [None] * length
    valid_indices = [index for index, value in enumerate(dx) if value is not None]
    if len(valid_indices) >= period:
        seed_indices = valid_indices[:period]
        current = sum(float(dx[index]) for index in seed_indices) / period
        adx[seed_indices[-1]] = current
        for index in valid_indices[period:]:
            current = (current * (period - 1) + float(dx[index])) / period
            adx[index] = current
    return adx, plus_di, minus_di


def horizon_return(close: list[float], hours: int) -> float | None:
    if len(close) <= hours or close[-hours - 1] <= 0:
        return None
    return close[-1] / close[-hours - 1] - 1.0


def pivot_indices(values: list[float], mode: str, lookback: int = 168, wing: int = 2) -> list[int]:
    start = max(wing, len(values) - lookback)
    result = []
    for index in range(start, len(values) - wing):
        neighbourhood = values[index - wing:index + wing + 1]
        if mode == "low" and values[index] == min(neighbourhood) and neighbourhood.count(values[index]) == 1:
            result.append(index)
        elif mode == "high" and values[index] == max(neighbourhood) and neighbourhood.count(values[index]) == 1:
            result.append(index)
    return result


def structure_state(high: list[float], low: list[float]) -> dict[str, Any]:
    lows = pivot_indices(low, "low")
    highs = pivot_indices(high, "high")
    higher_low = len(lows) >= 2 and low[lows[-1]] > low[lows[-2]]
    lower_low = len(lows) >= 2 and low[lows[-1]] < low[lows[-2]]
    higher_high = len(highs) >= 2 and high[highs[-1]] > high[highs[-2]]
    lower_high = len(highs) >= 2 and high[highs[-1]] < high[highs[-2]]
    return {
        "pivot_lows": lows,
        "pivot_highs": highs,
        "higher_low": higher_low,
        "lower_low": lower_low,
        "higher_high": higher_high,
        "lower_high": lower_high,
        "bullish": higher_low and higher_high,
        "bearish": lower_low and lower_high,
    }


def parse_closed_klines(symbol: str, payload: dict[str, Any], now_epoch: int) -> dict[str, list[float] | list[int]]:
    data = payload.get("data")
    required = ("time", "open", "high", "low", "close", "vol")
    if not isinstance(data, dict) or any(not isinstance(data.get(key), list) for key in required):
        raise ScanError(f"{symbol}: invalid kline schema")
    length = len(data["time"])
    if length == 0 or any(len(data[key]) != length for key in required):
        raise ScanError(f"{symbol}: inconsistent kline array lengths")

    indices = []
    for index, value in enumerate(data["time"]):
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            raise ScanError(f"{symbol}: invalid candle timestamp")
        if timestamp + 3600 <= now_epoch:
            indices.append(index)
    indices = indices[-MAX_CANDLES:]

    result: dict[str, list[float] | list[int]] = {"time": []}
    for key in ("open", "high", "low", "close", "vol", "amount"):
        result[key] = []

    for index in indices:
        timestamp = int(data["time"][index])
        o = finite_float(data["open"][index])
        h = finite_float(data["high"][index])
        lo = finite_float(data["low"][index])
        c = finite_float(data["close"][index])
        volume = finite_float(data["vol"][index])
        if None in (o, h, lo, c, volume) or min(o, h, lo, c) <= 0 or volume < 0:
            raise ScanError(f"{symbol}: invalid OHLCV value")
        if h < max(o, c, lo) or lo > min(o, c, h):
            raise ScanError(f"{symbol}: impossible OHLC relationship")
        result["time"].append(timestamp)
        result["open"].append(o)
        result["high"].append(h)
        result["low"].append(lo)
        result["close"].append(c)
        result["vol"].append(volume)
        amounts = data.get("amount")
        amount = finite_float(amounts[index]) if isinstance(amounts, list) and len(amounts) == length else None
        result["amount"].append(amount if amount is not None else 0.0)

    times = result["time"]
    if len(times) != len(set(times)) or any(times[index] <= times[index - 1] for index in range(1, len(times))):
        raise ScanError(f"{symbol}: duplicated or unordered candles")
    if any(times[index] - times[index - 1] != 3600 for index in range(1, len(times))):
        raise ScanError(f"{symbol}: missing hourly candles")
    return result


def build_indicators(candles: dict[str, Any]) -> dict[str, Any]:
    close = candles["close"]
    high = candles["high"]
    low = candles["low"]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    ema200 = ema(close, 200)
    rsi14 = rsi(close, 14)
    atr14 = atr_series(high, low, close, 14)
    adx14, plus_di, minus_di = dmi_adx(high, low, close, 14)
    latest_atr = atr14[-1]
    if latest_atr is None or latest_atr <= 0:
        raise ScanError("ATR unavailable")
    slope = (ema50[-1] - ema50[-11]) / (10.0 * latest_atr) if len(ema50) >= 11 else 0.0
    return {
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "rsi14": rsi14,
        "atr14": atr14,
        "adx14": adx14,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "ema50_slope_normalized": slope,
        "structure": structure_state(high, low),
    }


def btc_context(candles: dict[str, Any], ticker: dict[str, Any]) -> dict[str, Any]:
    indicators = build_indicators(candles)
    close = candles["close"]
    high = candles["high"]
    low = candles["low"]
    ema20_value = indicators["ema20"][-1]
    ema50_value = indicators["ema50"][-1]
    ema200_value = indicators["ema200"][-1]
    atr_value = float(indicators["atr14"][-1])
    adx_value = float(indicators["adx14"][-1] or 0.0)
    plus_value = float(indicators["plus_di"][-1] or 0.0)
    minus_value = float(indicators["minus_di"][-1] or 0.0)
    slope = indicators["ema50_slope_normalized"]
    return6 = horizon_return(close, 6) or 0.0
    return24 = horizon_return(close, 24) or 0.0
    structure = indicators["structure"]

    score = 0
    components: dict[str, int] = {}

    def add(name: str, bullish: bool, value: int) -> None:
        nonlocal score
        points = value if bullish else -value
        components[name] = points
        score += points

    add("close_vs_ema200", close[-1] > ema200_value, 25)
    add("ema20_vs_ema50", ema20_value > ema50_value, 15)
    add("ema50_vs_ema200", ema50_value > ema200_value, 10)
    if slope > 0:
        components["ema50_slope"] = 15
        score += 15
    elif slope < 0:
        components["ema50_slope"] = -15
        score -= 15
    else:
        components["ema50_slope"] = 0
    if adx_value >= 18 and plus_value > minus_value:
        components["adx_dmi"] = 15
        score += 15
    elif adx_value >= 18 and minus_value > plus_value:
        components["adx_dmi"] = -15
        score -= 15
    else:
        components["adx_dmi"] = 0
    if return24 > 0:
        components["return_24h"] = 10
        score += 10
    elif return24 < 0:
        components["return_24h"] = -10
        score -= 10
    else:
        components["return_24h"] = 0
    if return6 > 0:
        components["return_6h"] = 5
        score += 5
    elif return6 < 0:
        components["return_6h"] = -5
        score -= 5
    else:
        components["return_6h"] = 0
    if structure["higher_low"]:
        components["swing_structure"] = 5
        score += 5
    elif structure["lower_high"]:
        components["swing_structure"] = -5
        score -= 5
    else:
        components["swing_structure"] = 0
    score = max(-100, min(100, score))
    regime = "BULLISH" if score >= 55 else "BEARISH" if score <= -55 else "NEUTRAL"

    atr_percent_series = []
    for index, value in enumerate(indicators["atr14"]):
        if value is not None and close[index] > 0:
            atr_percent_series.append(100.0 * float(value) / close[index])
    current_atr_percent = 100.0 * atr_value / close[-1]
    previous_atr_percent = atr_percent_series[:-1][-720:]
    p95 = percentile(previous_atr_percent, 0.95) if previous_atr_percent else current_atr_percent
    p90 = percentile(previous_atr_percent, 0.90) if previous_atr_percent else current_atr_percent

    veto_reasons = []
    prior_atr = indicators["atr14"][-2] or atr_value
    if high[-1] - low[-1] > 2.5 * prior_atr:
        veto_reasons.append("latest_completed_candle_range_above_2_5_atr")
    if current_atr_percent > p95:
        veto_reasons.append("atr_percent_above_95th_percentile")
    fair_price = finite_float(ticker.get("fairPrice"))
    index_price = finite_float(ticker.get("indexPrice"))
    fair_index_deviation = None
    if fair_price and index_price:
        fair_index_deviation = abs(fair_price - index_price) / index_price
        if fair_index_deviation > MAX_FAIR_INDEX_DEVIATION:
            veto_reasons.append("abnormal_fair_index_dislocation")

    last_extreme = None
    for index in range(max(1, len(close) - 4), len(close)):
        reference_atr = indicators["atr14"][index - 1]
        if reference_atr and high[index] - low[index] > 2.5 * reference_atr:
            last_extreme = index
    if last_extreme is not None:
        following = list(range(last_extreme + 1, len(close)))
        normalized = all(
            indicators["atr14"][index - 1]
            and high[index] - low[index] <= 1.5 * indicators["atr14"][index - 1]
            for index in following
        )
        if len(following) < 2 or not normalized:
            veto_reasons.append("two_normalized_recovery_candles_not_completed")

    sign_conflict = (
        (slope > 0 and return24 < 0 and structure["lower_high"])
        or (slope < 0 and return24 > 0 and structure["higher_low"])
    )
    if current_atr_percent > p90 and sign_conflict:
        veto_reasons.append("conflicting_high_volatility_structure")

    return {
        "symbol": "BTC_USDT",
        "price": finite_float(ticker.get("lastPrice")) or close[-1],
        "close": close[-1],
        "regime": regime,
        "regime_score": score,
        "score_components": components,
        "volatility_veto": bool(veto_reasons),
        "volatility_veto_reasons": sorted(set(veto_reasons)),
        "permitted_direction": "LONG" if regime == "BULLISH" else "SHORT" if regime == "BEARISH" else "NONE",
        "ema20": ema20_value,
        "ema50": ema50_value,
        "ema200": ema200_value,
        "ema50_slope_normalized": slope,
        "rsi14": indicators["rsi14"][-1],
        "atr14": atr_value,
        "atr_percent": current_atr_percent,
        "atr_percent_95th": p95,
        "adx14": adx_value,
        "plus_di": plus_value,
        "minus_di": minus_value,
        "return_6h": return6,
        "return_24h": return24,
        "higher_low": structure["higher_low"],
        "lower_high": structure["lower_high"],
        "fair_index_deviation": fair_index_deviation,
        "indicators": indicators,
        "candles": candles,
    }


def reaction_count(candles: dict[str, Any], side: str, zone_low: float, zone_high: float, atr: float) -> tuple[int, int | None]:
    high = candles["high"]
    low = candles["low"]
    touches = []
    last_touch = None
    for index in range(max(0, len(high) - 168), len(high) - 3):
        touched = low[index] <= zone_high + 0.10 * atr if side == "LONG" else high[index] >= zone_low - 0.10 * atr
        if not touched:
            continue
        future_high = max(high[index + 1:min(len(high), index + 13)])
        future_low = min(low[index + 1:min(len(low), index + 13)])
        reacted = future_high - zone_high >= atr if side == "LONG" else zone_low - future_low >= atr
        if reacted and (not touches or index - touches[-1] >= 6):
            touches.append(index)
            last_touch = index
    return len(touches), last_touch


def build_zone(candles: dict[str, Any], indicators: dict[str, Any], side: str) -> dict[str, Any] | None:
    high = candles["high"]
    low = candles["low"]
    close = candles["close"]
    volume = candles["vol"]
    atr = float(indicators["atr14"][-1])
    structure = indicators["structure"]
    levels: list[tuple[float, str]] = []

    if side == "LONG":
        for index in structure["pivot_lows"][-5:]:
            levels.append((low[index], "swing_low"))
        levels.extend([
            (indicators["ema50"][-1], "ema50"),
            (indicators["ema200"][-1], "ema200"),
            (min(low[-72:]), "lower_72h"),
            (min(low[-168:]), "lower_168h"),
        ])
        for index in structure["pivot_highs"][-6:]:
            level = high[index]
            if index < len(close) - 4 and max(close[index + 1:]) > level + 0.10 * atr and close[-1] >= level - 0.50 * atr:
                levels.append((level, "breakout_retest"))
        start = max(0, len(close) - 120)
        impulse_low_index = min(range(start, len(low)), key=lambda i: low[i])
        if impulse_low_index < len(high) - 2:
            impulse_high = max(high[impulse_low_index:])
            impulse_low = low[impulse_low_index]
            if impulse_high - impulse_low >= 2 * atr:
                for ratio in (0.382, 0.5, 0.618):
                    levels.append((impulse_high - ratio * (impulse_high - impulse_low), f"fib_{ratio}"))
    else:
        for index in structure["pivot_highs"][-5:]:
            levels.append((high[index], "swing_high"))
        levels.extend([
            (indicators["ema50"][-1], "ema50"),
            (indicators["ema200"][-1], "ema200"),
            (max(high[-72:]), "upper_72h"),
            (max(high[-168:]), "upper_168h"),
        ])
        for index in structure["pivot_lows"][-6:]:
            level = low[index]
            if index < len(close) - 4 and min(close[index + 1:]) < level - 0.10 * atr and close[-1] <= level + 0.50 * atr:
                levels.append((level, "breakdown_retest"))
        start = max(0, len(close) - 120)
        impulse_high_index = max(range(start, len(high)), key=lambda i: high[i])
        if impulse_high_index < len(low) - 2:
            impulse_high = high[impulse_high_index]
            impulse_low = min(low[impulse_high_index:])
            if impulse_high - impulse_low >= 2 * atr:
                for ratio in (0.382, 0.5, 0.618):
                    levels.append((impulse_low + ratio * (impulse_high - impulse_low), f"fib_{ratio}"))

    start = max(0, len(volume) - 120)
    volume_index = max(range(start, len(volume)), key=lambda i: volume[i])
    levels.append(((high[volume_index] + low[volume_index] + close[volume_index]) / 3.0, "high_volume_reaction"))

    pivots = structure["pivot_lows"] if side == "LONG" else structure["pivot_highs"]
    values = [low[index] if side == "LONG" else high[index] for index in pivots[-8:]]
    repeated = []
    for value in values:
        group = [other for other in values if abs(other - value) <= 0.40 * atr]
        if len(group) >= 2:
            repeated = group
            break
    if repeated:
        levels.append((statistics.median(repeated), "repeated_level"))

    current = close[-1]
    if side == "LONG":
        levels = [(value, source) for value, source in levels if value <= current + 0.30 * atr]
    else:
        levels = [(value, source) for value, source in levels if value >= current - 0.30 * atr]
    if len(levels) < 2:
        return None

    tolerance = 0.45 * atr
    candidates = []
    for center, _ in levels:
        cluster = [(value, source) for value, source in levels if abs(value - center) <= tolerance]
        sources = sorted(set(source for _, source in cluster))
        zone_low = min(value for value, _ in cluster) - 0.12 * atr
        zone_high = max(value for value, _ in cluster) + 0.12 * atr
        distance = max(0.0, current - zone_high) / atr if side == "LONG" else max(0.0, zone_low - current) / atr
        reactions, last_touch = reaction_count(candles, side, zone_low, zone_high, atr)
        candidates.append({
            "zone_low": zone_low,
            "zone_high": zone_high,
            "sources": sources,
            "confluences": len(sources),
            "distance_atr": distance,
            "reactions": reactions,
            "last_reaction_index": last_touch,
        })
    candidates.sort(key=lambda item: (item["confluences"], item["reactions"], -item["distance_atr"]), reverse=True)
    return candidates[0]


def confirmation_patterns(candles: dict[str, Any], side: str, zone: dict[str, Any], atr: float) -> tuple[list[str], bool]:
    o, h, lo, c = candles["open"], candles["high"], candles["low"], candles["close"]
    body = max(abs(c[-1] - o[-1]), atr * 0.02)
    patterns = []
    if side == "LONG":
        if c[-2] < o[-2] and c[-1] > o[-1] and o[-1] <= c[-2] and c[-1] >= o[-2]:
            patterns.append("bullish_engulfing")
        lower_wick = min(o[-1], c[-1]) - lo[-1]
        if c[-1] > o[-1] and lower_wick >= 1.5 * body and c[-1] >= lo[-1] + 0.60 * (h[-1] - lo[-1]):
            patterns.append("strong_lower_wick_rejection")
        if c[-1] > h[-2]:
            patterns.append("close_above_previous_high")
        if lo[-1] < zone["zone_low"] and c[-1] > zone["zone_high"]:
            patterns.append("failed_breakdown_reclaim")
        if c[-1] > max(h[-4:-1]):
            patterns.append("minor_lower_high_break")
        tested = min(lo[-3:]) <= zone["zone_high"] + 0.10 * atr
    else:
        if c[-2] > o[-2] and c[-1] < o[-1] and o[-1] >= c[-2] and c[-1] <= o[-2]:
            patterns.append("bearish_engulfing")
        upper_wick = h[-1] - max(o[-1], c[-1])
        if c[-1] < o[-1] and upper_wick >= 1.5 * body and c[-1] <= lo[-1] + 0.40 * (h[-1] - lo[-1]):
            patterns.append("strong_upper_wick_rejection")
        if c[-1] < lo[-2]:
            patterns.append("close_below_previous_low")
        if h[-1] > zone["zone_high"] and c[-1] < zone["zone_low"]:
            patterns.append("failed_breakout_rejection")
        if c[-1] < min(lo[-4:-1]):
            patterns.append("minor_higher_low_break")
        tested = max(h[-3:]) >= zone["zone_low"] - 0.10 * atr
    return sorted(set(patterns)), tested


def divergence(candles: dict[str, Any], rsi_values: list[float | None], side: str, structure: dict[str, Any]) -> bool:
    indices = structure["pivot_lows"] if side == "LONG" else structure["pivot_highs"]
    if len(indices) < 2:
        return False
    first, second = indices[-2], indices[-1]
    if rsi_values[first] is None or rsi_values[second] is None:
        return False
    if side == "LONG":
        return candles["low"][second] < candles["low"][first] and rsi_values[second] > rsi_values[first]
    return candles["high"][second] > candles["high"][first] and rsi_values[second] < rsi_values[first]


def relative_performance(candles: dict[str, Any], atr_percent: float, btc: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    btc_close = btc["candles"]["close"]
    btc_atr_percent = max(0.000001, btc["atr_percent"] / 100.0)
    candidate_volatility = max(0.000001, atr_percent / 100.0)
    for hours in (3, 6, 24, 72):
        candidate_return = horizon_return(candles["close"], hours)
        btc_return = horizon_return(btc_close, hours)
        normalized = None
        if candidate_return is not None and btc_return is not None:
            normalized = candidate_return / candidate_volatility - btc_return / btc_atr_percent
        result[f"{hours}h"] = {
            "candidate_return": candidate_return,
            "btc_return": btc_return,
            "volatility_normalized_difference": normalized,
        }
    return result


def target_plan(candles: dict[str, Any], indicators: dict[str, Any], ticker: dict[str, Any], zone: dict[str, Any], side: str) -> dict[str, Any]:
    close = candles["close"]
    high = candles["high"]
    low = candles["low"]
    atr = float(indicators["atr14"][-1])
    confirmation_close = close[-1]
    current_price = finite_float(ticker.get("lastPrice")) or confirmation_close
    if side == "LONG":
        entry_low = max(zone["zone_high"], confirmation_close - 0.15 * atr)
        entry_high = confirmation_close + 0.10 * atr
        invalidation = zone["zone_low"] - 0.25 * atr
    else:
        entry_low = confirmation_close - 0.10 * atr
        entry_high = min(zone["zone_low"], confirmation_close + 0.15 * atr)
        if entry_high < entry_low:
            entry_high = confirmation_close + 0.10 * atr
        invalidation = zone["zone_high"] + 0.25 * atr
    entry = (entry_low + entry_high) / 2.0
    risk = entry - invalidation if side == "LONG" else invalidation - entry
    if risk <= 0:
        return {"valid": False, "reason": "non_positive_structural_risk"}

    structure = indicators["structure"]
    if side == "LONG":
        levels = [high[index] for index in structure["pivot_highs"] if high[index] > entry]
        levels.extend([max(high[-72:]), max(high[-168:])])
        levels = sorted(set(level for level in levels if level > entry))
        rr = [(level - entry) / risk for level in levels]
    else:
        levels = [low[index] for index in structure["pivot_lows"] if low[index] < entry]
        levels.extend([min(low[-72:]), min(low[-168:])])
        levels = sorted(set(level for level in levels if level < entry), reverse=True)
        rr = [(entry - level) / risk for level in levels]

    tp1 = next((level for level, ratio in zip(levels, rr) if ratio >= 1.20), None)
    tp2 = next((level for level, ratio in zip(levels, rr) if ratio >= MIN_REWARD_RISK), None)
    rr_tp2 = None if tp2 is None else ((tp2 - entry) / risk if side == "LONG" else (entry - tp2) / risk)
    late_distance_atr = abs(current_price - confirmation_close) / atr
    risk_atr = risk / atr
    invalidation_distance_percent = 100.0 * risk / entry
    valid = (
        tp1 is not None
        and tp2 is not None
        and rr_tp2 is not None
        and rr_tp2 >= MIN_REWARD_RISK
        and 0.30 <= risk_atr <= 2.50
        and late_distance_atr <= 0.75
    )
    reasons = []
    if tp2 is None or rr_tp2 is None or rr_tp2 < MIN_REWARD_RISK:
        reasons.append("no_structural_target_at_or_above_2R")
    if risk_atr < 0.30:
        reasons.append("invalidation_inside_normal_1h_noise")
    if risk_atr > 2.50:
        reasons.append("invalidation_farther_than_2_5_atr")
    if late_distance_atr > 0.75:
        reasons.append("current_price_moved_more_than_0_75_atr_from_confirmation")
    return {
        "valid": valid,
        "reasons": reasons,
        "entry_zone_low": entry_low,
        "entry_zone_high": entry_high,
        "entry_reference": entry,
        "invalidation_price": invalidation,
        "invalidation_distance_percent": invalidation_distance_percent,
        "invalidation_distance_atr": risk_atr,
        "tp1": tp1,
        "tp2": tp2,
        "reward_risk_tp2": rr_tp2,
        "late_distance_atr": late_distance_atr,
    }


def setup_score(
    side: str,
    candles: dict[str, Any],
    indicators: dict[str, Any],
    ticker: dict[str, Any],
    contract: dict[str, Any],
    btc: dict[str, Any],
    zone: dict[str, Any],
    patterns: list[str],
    tested: bool,
    targets: dict[str, Any],
    relative: dict[str, Any],
) -> tuple[int, dict[str, int], dict[str, Any]]:
    close = candles["close"]
    volume = candles["vol"]
    atr = float(indicators["atr14"][-1])
    rsi_value = float(indicators["rsi14"][-1] or 50.0)
    rsi_previous = float(indicators["rsi14"][-2] or rsi_value)
    structure = indicators["structure"]
    slope = indicators["ema50_slope_normalized"]
    funding = finite_float(ticker.get("fundingRate")) or 0.0
    turnover = finite_float(ticker.get("amount24")) or 0.0
    bid = finite_float(ticker.get("bid1")) or close[-1]
    ask = finite_float(ticker.get("ask1")) or close[-1]
    spread = max(0.0, ask - bid) / ((ask + bid) / 2.0)
    index_price = finite_float(ticker.get("indexPrice")) or close[-1]
    fair_price = finite_float(ticker.get("fairPrice")) or close[-1]
    fair_deviation = abs(fair_price - index_price) / index_price

    breakdown: dict[str, int] = {}
    a = 0
    regime_aligned = (side == "LONG" and btc["regime"] == "BULLISH") or (side == "SHORT" and btc["regime"] == "BEARISH")
    if regime_aligned:
        a += 14
    if abs(btc["regime_score"]) >= 70 and regime_aligned:
        a += 4
    if not btc["volatility_veto"]:
        a += 2
    breakdown["A_btc_alignment"] = min(20, a)

    bullish = side == "LONG"
    b = 0
    correct_ema = close[-1] > indicators["ema200"][-1] and indicators["ema20"][-1] >= indicators["ema50"][-1] if bullish else close[-1] < indicators["ema200"][-1] and indicators["ema20"][-1] <= indicators["ema50"][-1]
    correct_slope = slope > 0 if bullish else slope < 0
    correct_structure = structure["higher_low"] if bullish else structure["lower_high"]
    if correct_ema:
        b += 5
    if correct_slope:
        b += 4
    if correct_structure:
        b += 4
    if not structure["bearish"] if bullish else not structure["bullish"]:
        b += 2
    breakdown["B_candidate_structure"] = min(15, b)

    c_score = min(8, max(0, zone["confluences"] - 1) * 3)
    c_score += min(4, zone["reactions"] * 2)
    c_score += 4 if zone["distance_atr"] <= 0.25 else 3 if zone["distance_atr"] <= 0.50 else 1 if zone["distance_atr"] <= 0.75 else 0
    c_score += 2 if zone["last_reaction_index"] is None or len(close) - zone["last_reaction_index"] > 12 else 1
    c_score += 2
    breakdown["C_zone_quality"] = min(20, c_score)

    d = 0
    if tested:
        d += 3
    if patterns:
        d += 7
        d += min(3, 2 * (len(patterns) - 1))
    candle_range = candles["high"][-1] - candles["low"][-1]
    if candle_range > 0:
        close_position = (close[-1] - candles["low"][-1]) / candle_range
        strong_close = close_position >= 0.70 if bullish else close_position <= 0.30
        if strong_close:
            d += 2
    breakdown["D_closed_candle_confirmation"] = min(15, d)

    e = 0
    preferred_rsi = 30 <= rsi_value <= 48 if bullish else 52 <= rsi_value <= 72
    turning = rsi_value > rsi_previous if bullish else rsi_value < rsi_previous
    has_divergence = divergence(candles, indicators["rsi14"], side, structure)
    momentum3 = horizon_return(close, 3) or 0.0
    prior3 = close[-4] / close[-7] - 1.0 if len(close) >= 7 else 0.0
    improving = momentum3 > prior3 if bullish else momentum3 < prior3
    if preferred_rsi:
        e += 3
    if turning:
        e += 2
    if has_divergence:
        e += 3
    if improving:
        e += 2
    breakdown["E_momentum_divergence"] = min(10, e)

    median_volume = statistics.median(volume[-21:-1]) if len(volume) >= 21 else statistics.median(volume[:-1])
    volume_ratio = volume[-1] / median_volume if median_volume > 0 else 0.0
    pullback_declining = statistics.median(volume[-5:]) <= statistics.median(volume[-15:-5]) if len(volume) >= 15 else False
    hold = finite_float(ticker.get("holdVol")) or 0.0
    contract_size = finite_float(contract.get("contractSize")) or 0.0
    oi_notional = hold * contract_size * close[-1]
    f_score = 0
    if volume_ratio >= 1.20:
        f_score += 4
    elif volume_ratio >= 0.90:
        f_score += 2
    if pullback_declining:
        f_score += 2
    if oi_notional >= 2_000_000:
        f_score += 2
    elif oi_notional > 0:
        f_score += 1
    breakdown["F_volume_open_interest"] = min(8, f_score)

    relative_values = [relative[key]["volatility_normalized_difference"] for key in ("3h", "6h", "24h", "72h")]
    relative_values = [float(value) for value in relative_values if value is not None]
    favourable = sum(value >= 0 for value in relative_values) if bullish else sum(value <= 0 for value in relative_values)
    latest_favourable = relative.get("3h", {}).get("volatility_normalized_difference")
    g = min(5, favourable + (1 if favourable >= 3 else 0))
    if latest_favourable is not None and ((bullish and latest_favourable > 0) or (not bullish and latest_favourable < 0)):
        g += 2
    breakdown["G_relative_performance"] = min(7, g)

    h_score = 0
    if spread <= 0.0005:
        h_score += 2
    elif spread <= MAX_SPREAD:
        h_score += 1
    if turnover >= 10_000_000:
        h_score += 1
    if fair_deviation <= 0.0015:
        h_score += 1
    funding_ok = funding <= 0.001 if bullish else funding >= -0.001
    if funding_ok:
        h_score += 1
    breakdown["H_execution_quality"] = min(5, h_score)

    diagnostics = {
        "rsi14": rsi_value,
        "rsi_turning": turning,
        "divergence": has_divergence,
        "confirmation_volume_ratio_to_20_median": volume_ratio,
        "pullback_or_rally_volume_declining": pullback_declining,
        "open_interest_notional_estimate": oi_notional,
        "spread": spread,
        "fair_index_deviation": fair_deviation,
        "funding_rate": funding,
        "turnover_24h": turnover,
        "correct_ema_alignment": correct_ema,
        "correct_ema50_slope": correct_slope,
        "correct_structure": correct_structure,
    }
    return sum(breakdown.values()), breakdown, diagnostics


def evaluate_setup(
    symbol: str,
    side: str,
    candles: dict[str, Any],
    contract: dict[str, Any],
    ticker: dict[str, Any],
    btc: dict[str, Any],
) -> dict[str, Any] | None:
    indicators = build_indicators(candles)
    close = candles["close"]
    high = candles["high"]
    low = candles["low"]
    atr = float(indicators["atr14"][-1])
    atr_percent = 100.0 * atr / close[-1]
    structure = indicators["structure"]
    slope = indicators["ema50_slope_normalized"]
    bullish = side == "LONG"

    structural_opposition = (
        indicators["ema20"][-1] < indicators["ema50"][-1] < indicators["ema200"][-1]
        and slope < 0 and structure["lower_high"]
    ) if bullish else (
        indicators["ema20"][-1] > indicators["ema50"][-1] > indicators["ema200"][-1]
        and slope > 0 and structure["higher_low"]
    )
    trend_resumption = close[-1] > indicators["ema20"][-1] and close[-1] > high[-2] if bullish else close[-1] < indicators["ema20"][-1] and close[-1] < low[-2]
    trend_valid = not structural_opposition and ((slope > 0 if bullish else slope < 0) or trend_resumption)

    zone = build_zone(candles, indicators, side)
    if zone is None:
        return None
    patterns, tested = confirmation_patterns(candles, side, zone, atr)
    range72_low, range72_high = min(low[-72:]), max(high[-72:])
    range_position = (close[-1] - range72_low) / (range72_high - range72_low) if range72_high > range72_low else 0.5
    near_ema = min(abs(close[-1] - indicators["ema50"][-1]), abs(close[-1] - indicators["ema200"][-1])) <= 0.50 * atr
    breakout_source = "breakout_retest" in zone["sources"] if bullish else "breakdown_retest" in zone["sources"]
    location_valid = (
        zone["distance_atr"] <= 0.50
        or (range_position <= 0.25 if bullish else range_position >= 0.75)
        or breakout_source
        or near_ema
    ) and zone["distance_atr"] <= 0.75
    zone_valid = zone["confluences"] >= 2 and zone["reactions"] >= 1
    not_invalidated = close[-1] >= zone["zone_low"] if bullish else close[-1] <= zone["zone_high"]
    targets = target_plan(candles, indicators, ticker, zone, side)
    relative = relative_performance(candles, atr_percent, btc)

    relative_core = [relative[key]["volatility_normalized_difference"] for key in ("6h", "24h", "72h")]
    relative_core = [float(value) for value in relative_core if value is not None]
    relative3 = relative["3h"]["volatility_normalized_difference"]
    severe_relative = (
        len(relative_core) == 3 and all(value < -3 for value in relative_core) and (relative3 is None or relative3 <= 0)
    ) if bullish else (
        len(relative_core) == 3 and all(value > 3 for value in relative_core) and (relative3 is None or relative3 >= 0)
    )
    funding = finite_float(ticker.get("fundingRate")) or 0.0
    funding_extreme = funding > 0.003 if bullish else funding < -0.003
    regime_aligned = (bullish and btc["regime"] == "BULLISH") or ((not bullish) and btc["regime"] == "BEARISH")

    raw_score, breakdown, diagnostics = setup_score(
        side, candles, indicators, ticker, contract, btc, zone, patterns, tested, targets, relative
    )
    mandatory = {
        "btc_regime_alignment": regime_aligned,
        "btc_volatility_stable": not btc["volatility_veto"],
        "candidate_trend_valid": trend_valid,
        "zone_valid": zone_valid,
        "price_location_valid": location_valid,
        "zone_not_invalidated": not_invalidated,
        "closed_candle_confirmation": bool(patterns) and tested,
        "reward_risk_valid": bool(targets.get("valid")),
        "relative_performance_not_severe": not severe_relative,
        "funding_not_extreme": not funding_extreme,
    }
    missing = [name for name, passed in mandatory.items() if not passed]
    all_mandatory = not missing
    final_score = raw_score if all_mandatory else min(raw_score, 79)
    if final_score < MIN_WATCHLIST_SCORE:
        return None
    status = "QUALIFIED" if all_mandatory and final_score >= MIN_QUALIFIED_SCORE else "WATCHLIST"

    reasons = []
    if zone["confluences"] >= 3:
        reasons.append(f"Zona con {zone['confluences']} confluenze: {', '.join(zone['sources'][:4])}")
    if patterns:
        reasons.append(f"Conferma 1H chiusa: {', '.join(patterns[:2])}")
    if diagnostics["confirmation_volume_ratio_to_20_median"] >= 1.2:
        reasons.append("Volume di conferma almeno 1,20x la mediana a 20 candele")
    rel24 = relative["24h"]["volatility_normalized_difference"]
    if rel24 is not None and ((bullish and rel24 >= 0) or ((not bullish) and rel24 <= 0)):
        reasons.append("Performance relativa a BTC favorevole su 24H")
    if diagnostics["correct_structure"]:
        reasons.append("Struttura swing coerente con la direzione")
    reasons = reasons[:3]

    risks = []
    if zone["distance_atr"] > 0.35:
        risks.append("Prezzo non perfettamente aderente alla zona strutturale")
    if diagnostics["confirmation_volume_ratio_to_20_median"] < 1.2:
        risks.append("Volume di conferma inferiore a 1,20x la mediana")
    if abs(funding) > 0.001:
        risks.append("Funding relativamente affollato nella direzione del setup")
    if not diagnostics["correct_ema_alignment"]:
        risks.append("Allineamento EMA non completo")
    if targets.get("late_distance_atr", 0.0) > 0.50:
        risks.append("Prezzo vicino al limite massimo di ingresso tardivo")
    risks.append("Una chiusura 1H oltre l'invalidazione strutturale cancella il setup")
    risks = risks[:3]

    current_price = finite_float(ticker.get("lastPrice")) or close[-1]
    cancellation = (
        f"Chiusura 1H sotto {targets.get('invalidation_price'):.10g} oppure ingresso oltre 0,75 ATR dalla conferma"
        if bullish and targets.get("invalidation_price") is not None
        else f"Chiusura 1H sopra {targets.get('invalidation_price'):.10g} oppure ingresso oltre 0,75 ATR dalla conferma"
        if targets.get("invalidation_price") is not None
        else "Mancanza di un target strutturale da almeno 2R"
    )
    return {
        "symbol": symbol,
        "side": side,
        "classification": "STRONG_LOW" if bullish else "STRONG_HIGH",
        "score": final_score,
        "raw_score": raw_score,
        "score_breakdown": breakdown,
        "status": status,
        "current_price": current_price,
        "confirmation": ", ".join(patterns) if patterns else "missing_closed_candle_confirmation",
        "confirmation_patterns": patterns,
        "entry_zone_low": targets.get("entry_zone_low"),
        "entry_zone_high": targets.get("entry_zone_high"),
        "invalidation_price": targets.get("invalidation_price"),
        "invalidation_distance_percent": targets.get("invalidation_distance_percent"),
        "invalidation_distance_atr": targets.get("invalidation_distance_atr"),
        "tp1": targets.get("tp1"),
        "tp2": targets.get("tp2"),
        "reward_risk_tp2": targets.get("reward_risk_tp2"),
        "turnover_24h": diagnostics["turnover_24h"],
        "spread": diagnostics["spread"],
        "open_interest": finite_float(ticker.get("holdVol")),
        "open_interest_notional_estimate": diagnostics["open_interest_notional_estimate"],
        "funding_rate": diagnostics["funding_rate"],
        "fair_index_deviation": diagnostics["fair_index_deviation"],
        "relative_performance_vs_btc": relative,
        "zone": zone,
        "rsi14": diagnostics["rsi14"],
        "atr14": atr,
        "atr_percent": atr_percent,
        "volume_confirmation_ratio": diagnostics["confirmation_volume_ratio_to_20_median"],
        "mandatory_conditions": mandatory,
        "missing_requirements": missing,
        "strongest_reasons": reasons,
        "main_risks": risks,
        "cancel_condition": cancellation,
        "correlation_status": "PRIMARY",
        "candles_72_returns": [candles["close"][i] / candles["close"][i - 1] - 1.0 for i in range(len(candles["close"]) - 71, len(candles["close"]))],
    }


def pearson(values_a: list[float], values_b: list[float]) -> float | None:
    length = min(len(values_a), len(values_b))
    if length < 20:
        return None
    a = values_a[-length:]
    b = values_b[-length:]
    mean_a = statistics.mean(a)
    mean_b = statistics.mean(b)
    numerator = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    denominator = math.sqrt(sum((x - mean_a) ** 2 for x in a) * sum((y - mean_b) ** 2 for y in b))
    return None if denominator == 0 else numerator / denominator


def apply_correlation_filter(setups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(setups, key=lambda item: item["score"], reverse=True)
    primaries: list[dict[str, Any]] = []
    for setup in ordered:
        correlated_to = None
        correlation_value = None
        for primary in primaries:
            correlation = pearson(setup["candles_72_returns"], primary["candles_72_returns"])
            if correlation is not None and correlation > 0.85:
                correlated_to = primary["symbol"]
                correlation_value = correlation
                break
        if correlated_to:
            setup["correlation_status"] = "CORRELATED_ALTERNATIVE"
            setup["correlated_to"] = correlated_to
            setup["correlation_72h"] = correlation_value
        else:
            primaries.append(setup)
    return ordered


def contract_static_reason(contract: dict[str, Any], now_ms: int) -> str | None:
    if contract.get("quoteCoin") != "USDT" or contract.get("settleCoin") != "USDT":
        return "not_usdt_margined_and_settled"
    if contract.get("futureType") != 1:
        return "not_linear_perpetual"
    if contract.get("state") != 0:
        return "contract_not_enabled"
    if contract.get("isHidden") is True:
        return "hidden_contract"
    if contract.get("preMarket") is True:
        return "pre_market_contract"
    if contract.get("type") == 2:
        return "suspended_contract"
    created = finite_float(contract.get("createTime"))
    if created is None or created <= 0:
        return "listing_age_unavailable"
    if now_ms - created < MIN_LISTING_AGE_SECONDS * 1000:
        return "listing_younger_than_14_days"
    if not isinstance(contract.get("symbol"), str) or not contract.get("symbol"):
        return "invalid_symbol"
    return None


def ticker_reason(ticker: dict[str, Any], now_ms: int) -> str | None:
    bid = finite_float(ticker.get("bid1"))
    ask = finite_float(ticker.get("ask1"))
    last = finite_float(ticker.get("lastPrice"))
    fair = finite_float(ticker.get("fairPrice"))
    index = finite_float(ticker.get("indexPrice"))
    hold = finite_float(ticker.get("holdVol"))
    timestamp = finite_float(ticker.get("timestamp"))
    if None in (bid, ask, last, fair, index, hold, timestamp) or min(bid, ask, last, fair, index) <= 0:
        return "invalid_ticker_fields"
    if hold <= 0:
        return "zero_open_interest"
    if ask < bid:
        return "crossed_bid_ask"
    spread = (ask - bid) / ((ask + bid) / 2.0)
    if spread > MAX_SPREAD:
        return "spread_above_0_20_percent"
    if abs(fair - index) / index > MAX_FAIR_INDEX_DEVIATION:
        return "fair_index_deviation_above_0_35_percent"
    age_seconds = (now_ms - timestamp) / 1000.0
    if age_seconds > MAX_TICKER_AGE_SECONDS or age_seconds < -300:
        return "stale_or_future_ticker_timestamp"
    return None


def fetch_candles(symbols: list[str], now_epoch: int) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    start = now_epoch - (MAX_CANDLES + 40) * 3600
    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}

    def one(symbol: str) -> tuple[str, dict[str, Any]]:
        encoded = urllib.parse.quote(symbol, safe="_")
        payload = get_json(
            f"/api/v1/contract/kline/{encoded}",
            {"interval": TIMEFRAME, "start": start, "end": now_epoch},
        )
        return symbol, parse_closed_klines(symbol, payload, now_epoch)

    workers = min(8, max(1, len(symbols)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(one, symbol): symbol for symbol in symbols}
        for future in as_completed(future_map):
            symbol = future_map[future]
            try:
                key, candles = future.result()
                results[key] = candles
            except Exception as exc:
                errors[symbol] = str(exc)
    return results, errors


def scan_market() -> dict[str, Any]:
    started = utc_now()
    now_epoch = int(started.timestamp())
    now_ms = now_epoch * 1000
    skip_counts: Counter[str] = Counter()
    skip_examples: dict[str, list[str]] = {}

    def skip(reason: str, symbol: str) -> None:
        skip_counts[reason] += 1
        skip_examples.setdefault(reason, [])
        if len(skip_examples[reason]) < 5:
            skip_examples[reason].append(symbol)

    contracts_payload = get_json("/api/v1/contract/detail/country")
    tickers_payload = get_json("/api/v1/contract/ticker")
    contracts = as_list(contracts_payload.get("data"))
    tickers = as_list(tickers_payload.get("data"))
    if not contracts or not tickers:
        raise ScanError("Complete MEXC contract universe or ticker universe is unavailable")
    ticker_map = {item.get("symbol"): item for item in tickers if isinstance(item.get("symbol"), str)}

    static_contracts: dict[str, dict[str, Any]] = {}
    pre_liquidity: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for contract in contracts:
        symbol = str(contract.get("symbol") or "UNKNOWN")
        reason = contract_static_reason(contract, now_ms)
        if reason:
            skip(reason, symbol)
            continue
        static_contracts[symbol] = contract
        ticker = ticker_map.get(symbol)
        if ticker is None:
            skip("ticker_missing", symbol)
            continue
        reason = ticker_reason(ticker, now_ms)
        if reason:
            skip(reason, symbol)
            continue
        pre_liquidity.append((symbol, contract, ticker))

    turnovers = [finite_float(ticker.get("amount24")) or 0.0 for _, _, ticker in pre_liquidity]
    turnover_p35 = percentile(turnovers, 0.35)
    eligible: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for symbol, contract, ticker in pre_liquidity:
        turnover = finite_float(ticker.get("amount24")) or 0.0
        if turnover < MIN_TURNOVER and turnover < turnover_p35:
            skip("turnover_below_2m_and_35th_percentile", symbol)
            continue
        eligible[symbol] = (contract, ticker)

    if "BTC_USDT" not in eligible:
        raise ScanError("BTC_USDT failed eligibility or data-quality filters")

    candles_map, candle_errors = fetch_candles(sorted(eligible), now_epoch)
    for symbol, error in candle_errors.items():
        skip("kline_request_or_schema_error", symbol)
        skip_examples.setdefault("kline_error_details", [])
        if len(skip_examples["kline_error_details"]) < 5:
            skip_examples["kline_error_details"].append(f"{symbol}: {error}")

    analyzed: dict[str, dict[str, Any]] = {}
    current_hour = now_epoch - now_epoch % 3600
    for symbol, candles in candles_map.items():
        required_count = BTC_MIN_CANDLES if symbol == "BTC_USDT" else MIN_CANDLES
        if len(candles["close"]) < required_count:
            skip("insufficient_closed_1h_candles", symbol)
            continue
        if candles["time"][-1] < current_hour - 2 * 3600:
            skip("stale_latest_closed_candle", symbol)
            continue
        try:
            indicators = build_indicators(candles)
        except Exception:
            skip("indicator_calculation_failed", symbol)
            continue
        atr_value = indicators["atr14"][-2] or indicators["atr14"][-1]
        if symbol != "BTC_USDT" and candles["high"][-1] - candles["low"][-1] > 3.0 * atr_value:
            skip("latest_candle_range_above_3_atr", symbol)
            continue
        analyzed[symbol] = candles

    if "BTC_USDT" not in analyzed:
        raise ScanError("BTC_USDT does not have at least 720 valid closed 1H candles")
    btc = btc_context(analyzed["BTC_USDT"], eligible["BTC_USDT"][1])

    if btc["regime"] == "BULLISH":
        sides = ["LONG"]
    elif btc["regime"] == "BEARISH":
        sides = ["SHORT"]
    else:
        sides = ["LONG", "SHORT"]

    all_setups = []
    for symbol, candles in analyzed.items():
        if symbol == "BTC_USDT":
            continue
        contract, ticker = eligible[symbol]
        for side in sides:
            try:
                setup = evaluate_setup(symbol, side, candles, contract, ticker, btc)
                if setup:
                    all_setups.append(setup)
            except Exception:
                skip("candidate_evaluation_failed", symbol)

    qualified = apply_correlation_filter([item for item in all_setups if item["status"] == "QUALIFIED"])
    watchlist = sorted([item for item in all_setups if item["status"] == "WATCHLIST"], key=lambda item: item["score"], reverse=True)
    qualified = qualified[:MAX_REPORTED]
    watchlist = watchlist[:MAX_REPORTED]
    for setup in qualified + watchlist:
        setup.pop("candles_72_returns", None)

    if btc["regime"] == "NEUTRAL" or btc["volatility_veto"]:
        qualified = []
    best = next((item for item in qualified if item["correlation_status"] == "PRIMARY"), qualified[0] if qualified else None)
    if best and best["side"] == "LONG":
        decision = "QUALIFIED_LONG"
        final_statement = "QUALIFIED LONG STRONG LOW OPPORTUNITY FOUND"
    elif best:
        decision = "QUALIFIED_SHORT"
        final_statement = "QUALIFIED SHORT STRONG HIGH OPPORTUNITY FOUND"
    else:
        decision = "NO_TRADE"
        final_statement = "NO QUALIFYING SETUP — STAY FLAT AND WAIT FOR A NEW CLOSED-CANDLE CONFIRMATION"

    completed = utc_now()
    btc_public = {key: value for key, value in btc.items() if key not in ("indicators", "candles")}
    report = {
        "scan_ok": True,
        "scan_timestamp_utc": utc_iso(completed),
        "scan_started_utc": utc_iso(started),
        "scan_duration_seconds": round((completed - started).total_seconds(), 3),
        "timeframe": "1H",
        "closed_candles_only": True,
        "data_source": "Official MEXC Futures public market-data endpoints only",
        "contracts_retrieved": len(contracts),
        "contracts_eligible": len(eligible),
        "contracts_analyzed": len(analyzed),
        "contracts_skipped": len(contracts) - len(analyzed),
        "turnover_35th_percentile": turnover_p35,
        "btc": btc_public,
        "decision": decision,
        "final_statement": final_statement,
        "best_setup": best,
        "qualified_setups": qualified,
        "watchlist": watchlist,
        "skipped_reasons": dict(skip_counts),
        "skipped_examples": skip_examples,
    }
    return report


def error_report(exc: Exception) -> dict[str, Any]:
    return {
        "scan_ok": False,
        "scan_timestamp_utc": utc_iso(),
        "timeframe": "1H",
        "closed_candles_only": True,
        "data_source": "Official MEXC Futures public market-data endpoints only",
        "contracts_retrieved": None,
        "contracts_eligible": None,
        "contracts_analyzed": 0,
        "contracts_skipped": None,
        "btc": {
            "symbol": "BTC_USDT",
            "price": None,
            "regime": None,
            "regime_score": None,
            "volatility_veto": None,
            "permitted_direction": "NONE",
        },
        "decision": "NO_TRADE",
        "final_statement": "NO QUALIFYING SETUP — STAY FLAT AND WAIT FOR A NEW CLOSED-CANDLE CONFIRMATION",
        "best_setup": None,
        "qualified_setups": [],
        "watchlist": [],
        "skipped_reasons": {"MEXC_PUBLIC_DATA_UNAVAILABLE": str(exc)},
        "skipped_examples": {},
    }


def format_price(value: Any) -> str:
    number = finite_float(value)
    if number is None:
        return "n/d"
    if number >= 1000:
        return f"{number:,.2f}"
    if number >= 1:
        return f"{number:.5f}".rstrip("0").rstrip(".")
    return f"{number:.10f}".rstrip("0").rstrip(".")


def percent(value: Any, digits: int = 2) -> str:
    number = finite_float(value)
    return "n/d" if number is None else f"{100 * number:.{digits}f}%"


def report_markdown(report: dict[str, Any]) -> str:
    btc = report["btc"]
    lines = [
        "# MEXC Futures Scan",
        "",
        f"**Scan timestamp:** {report['scan_timestamp_utc']}",
        "**Primary timeframe:** 1H",
        f"**Contracts retrieved:** {report.get('contracts_retrieved')}",
        f"**Contracts eligible:** {report.get('contracts_eligible')}",
        f"**Contracts analyzed:** {report.get('contracts_analyzed')}",
        f"**Contracts skipped:** {report.get('contracts_skipped')}",
        f"**Data freshness:** {'Valida' if report.get('scan_ok') else 'Non disponibile'}",
        "",
        "## BTC Regime",
        "",
        f"**BTC_USDT price:** {format_price(btc.get('price'))}",
        f"**BTC regime:** {btc.get('regime') or 'Non determinabile'}",
        f"**BTC Regime Score:** {btc.get('regime_score')}",
        f"**EMA 20 / EMA 50 / EMA 200:** {format_price(btc.get('ema20'))} / {format_price(btc.get('ema50'))} / {format_price(btc.get('ema200'))}",
        f"**ADX / +DI / -DI:** {format_price(btc.get('adx14'))} / {format_price(btc.get('plus_di'))} / {format_price(btc.get('minus_di'))}",
        f"**6H return:** {percent(btc.get('return_6h'))}",
        f"**24H return:** {percent(btc.get('return_24h'))}",
        f"**ATR volatility:** {format_price(btc.get('atr_percent'))}% | veto={btc.get('volatility_veto')}",
        f"**Permitted direction:** {btc.get('permitted_direction')}",
        "",
    ]
    if btc.get("regime") == "NEUTRAL":
        lines.append("NO QUALIFYING DIRECTIONAL SETUP — BTC REGIME IS NEUTRAL.")
        lines.append("")
    if btc.get("volatility_veto"):
        lines.append("Veto di volatilità BTC attivo: " + ", ".join(btc.get("volatility_veto_reasons", [])))
        lines.append("")

    lines.extend(["## Best Available Opportunity", ""])
    best = report.get("best_setup")
    if best:
        lines.extend([
            f"**{best['symbol']} — {best['side']} {best['classification']} — {best['score']}/100**",
            "",
            f"- Prezzo MEXC: {format_price(best['current_price'])}",
            f"- Conferma: {best['confirmation']}",
            f"- Entry-reference: {format_price(best['entry_zone_low'])} – {format_price(best['entry_zone_high'])}",
            f"- Invalidazione: {format_price(best['invalidation_price'])} ({best.get('invalidation_distance_percent', 0):.2f}%, {best.get('invalidation_distance_atr', 0):.2f} ATR)",
            f"- TP1 / TP2: {format_price(best['tp1'])} / {format_price(best['tp2'])}",
            f"- R:R TP2: {best.get('reward_risk_tp2', 0):.2f}",
            f"- Turnover 24H: {best.get('turnover_24h', 0):,.0f} USDT",
            f"- Spread: {100 * best.get('spread', 0):.4f}%",
            f"- Open interest: {format_price(best.get('open_interest'))}",
            f"- Funding: {percent(best.get('funding_rate'), 4)}",
            f"- Score: {best['score_breakdown']}",
            "- Motivi: " + "; ".join(best.get("strongest_reasons", [])),
            "- Rischi: " + "; ".join(best.get("main_risks", [])),
            f"- Cancellazione: {best['cancel_condition']}",
            "",
        ])
    else:
        lines.extend(["Nessuna opportunità qualificata.", ""])

    lines.extend(["## Ranked Qualified Opportunities", ""])
    qualified = report.get("qualified_setups", [])
    if qualified:
        lines.append("| Rank | Symbol | Side | Type | Score | Entry Zone | Invalidation | TP1 | TP2 | R:R | Turnover | Spread | Funding | Status |")
        lines.append("|---:|---|---|---|---:|---|---|---|---|---:|---:|---:|---:|---|")
        for rank, setup in enumerate(qualified, start=1):
            lines.append(
                f"| {rank} | {setup['symbol']} | {setup['side']} | {setup['classification']} | {setup['score']} | "
                f"{format_price(setup['entry_zone_low'])}–{format_price(setup['entry_zone_high'])} | {format_price(setup['invalidation_price'])} | "
                f"{format_price(setup['tp1'])} | {format_price(setup['tp2'])} | {setup.get('reward_risk_tp2', 0):.2f} | "
                f"{setup.get('turnover_24h', 0):,.0f} | {100 * setup.get('spread', 0):.4f}% | {percent(setup.get('funding_rate'), 4)} | {setup['correlation_status']} |"
            )
        lines.append("")
        lines.append("Spiegazione punteggi:")
        for setup in qualified:
            lines.append(f"- **{setup['symbol']} {setup['score']}/100:** {setup['score_breakdown']}")
        lines.append("")
    else:
        lines.extend(["Nessun setup qualificato.", ""])

    lines.extend(["## Watchlist", ""])
    watchlist = report.get("watchlist", [])
    if watchlist:
        for setup in watchlist:
            missing = ", ".join(setup.get("missing_requirements", []))
            lines.append(f"- **{setup['symbol']} {setup['side']} — {setup['score']}/100:** manca `{missing}`. Score: {setup['score_breakdown']}")
        lines.append("")
    else:
        lines.extend(["Nessun candidato utile in watchlist.", ""])

    lines.extend([
        "## Final Decision",
        "",
        f"**{report['final_statement']}.**",
        "",
        "> Analisi tecnica automatizzata, non consulenza finanziaria e non istruzione di esecuzione. Nessun ordine viene preparato o inviato.",
        "",
    ])
    return "\n".join(lines)


def save_outputs(report: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    latest_json = OUTPUT_DIR / "latest_report.json"
    if latest_json.exists():
        shutil.copyfile(latest_json, OUTPUT_DIR / "previous_report.json")
    latest_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "latest_report.md").write_text(report_markdown(report), encoding="utf-8")
    status = {
        "scan_ok": report.get("scan_ok"),
        "scan_timestamp_utc": report.get("scan_timestamp_utc"),
        "decision": report.get("decision"),
        "btc_regime": report.get("btc", {}).get("regime"),
        "qualified_count": len(report.get("qualified_setups", [])),
        "watchlist_count": len(report.get("watchlist", [])),
    }
    (OUTPUT_DIR / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    try:
        report = scan_market()
    except Exception as exc:
        report = error_report(exc)
    save_outputs(report)
    print(report_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
