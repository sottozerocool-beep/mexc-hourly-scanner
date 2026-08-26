#!/usr/bin/env python3
"""Core utilities for the Core PCE -> BTC live watcher.

Standard-library only.  The module reads the official BEA release, classifies
its monthly Core PCE value with transparent rules, and samples public BTC/USDT
market data.  It never places trades or accesses an exchange account.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import html
from html.parser import HTMLParser
import json
import re
import statistics
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

UTC = dt.timezone.utc

BEA_API_URL = "https://apps.bea.gov/api/data"
BEA_CURRENT_RELEASES_URL = "https://www.bea.gov/news/current-releases"
BEA_RSS_URL = "https://apps.bea.gov/rss/rss.xml"

MARKET_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("Binance Market Data", "https://data-api.binance.vision"),
    ("Binance Spot", "https://api.binance.com"),
    ("MEXC Spot", "https://api.mexc.com"),
)

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

USER_AGENT = "Core-PCE-BTC-Watcher/1.0"


class WatcherError(RuntimeError):
    """Recoverable watcher failure."""


@dataclass(frozen=True)
class Scenario:
    label: str
    bias: str
    bullish_low: float
    bullish_high: float
    bearish_low: float
    bearish_high: float
    expected_direction: str
    explanation: str


@dataclass(frozen=True)
class MarketSnapshot:
    captured_at: dt.datetime
    price: float
    source: str


@dataclass(frozen=True)
class VolumeStats:
    ratio: float | None
    event_candles: int
    source: str | None


class StructuredTextParser(HTMLParser):
    """Convert HTML into text while retaining rough row boundaries."""

    BLOCK_TAGS = {
        "p", "div", "br", "li", "tr", "table", "section", "article",
        "h1", "h2", "h3", "h4", "h5", "h6",
    }
    CELL_TAGS = {"td", "th"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")
        elif tag.lower() in self.CELL_TAGS:
            self.parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")
        elif tag.lower() in self.CELL_TAGS:
            self.parts.append("\t")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        lines: list[str] = []
        for raw in "".join(self.parts).replace("\xa0", " ").splitlines():
            line = re.sub(r"[ \r\f\v]+", " ", raw)
            line = re.sub(r" *\t+ *", "\t", line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def html_to_text(document: str) -> str:
    parser = StructuredTextParser()
    parser.feed(document)
    parser.close()
    return parser.text()


def append_cache_buster(url: str) -> str:
    parts = list(urlparse(url))
    query = dict(parse_qsl(parts[4], keep_blank_values=True))
    query["_pce_watcher"] = str(int(time.time() * 1000))
    parts[4] = urlencode(query)
    return urlunparse(parts)


def http_request(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 12.0,
    cache_bust: bool = False,
) -> tuple[int, str, bytes]:
    target = append_cache_buster(url) if cache_bust else url
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json,application/xml,text/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if headers:
        request_headers.update(headers)
    request = Request(target, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), response.geturl(), response.read()
    except HTTPError as exc:
        payload = exc.read() if exc.fp else b""
        return int(exc.code), target, payload
    except (URLError, TimeoutError, OSError) as exc:
        raise WatcherError(f"Errore di rete su {url}: {exc}") from exc


def get_text(url: str, *, cache_bust: bool = False, timeout: float = 12.0) -> tuple[int, str, str]:
    status, final_url, payload = http_request(
        url, cache_bust=cache_bust, timeout=timeout
    )
    return status, final_url, payload.decode("utf-8", errors="replace")


def get_json(url: str, *, timeout: float = 12.0, cache_bust: bool = False) -> Any:
    status, _, payload = http_request(url, timeout=timeout, cache_bust=cache_bust)
    if status != 200:
        raise WatcherError(f"HTTP {status} da {url}")
    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise WatcherError(f"JSON non valido da {url}") from exc


def parse_core_pce_mom(document: str) -> float | None:
    """Extract monthly Core PCE without confusing it with year-over-year data."""

    text = html_to_text(document)
    compact = re.sub(r"\s+", " ", text)
    preceding = re.search(r"\bFrom the preceding month\b", compact, re.IGNORECASE)
    if preceding:
        start = preceding.start()
        annual = re.search(
            r"\bFrom the same month one year ago\b",
            compact[preceding.end():],
            re.IGNORECASE,
        )
        end = preceding.end() + annual.start() if annual else min(len(compact), start + 1800)
        segment = compact[start:end]
        match = re.search(
            r"Excluding food and energy,\s*the PCE price index\s*"
            r"(?:(increased|decreased)\s*(?:by\s*)?([0-9]+(?:\.[0-9]+)?)\s*percent|"
            r"(was unchanged|remained unchanged))",
            segment,
            re.IGNORECASE,
        )
        if match:
            direction = (match.group(1) or match.group(3) or "").lower()
            if "unchanged" in direction:
                return 0.0
            value = float(match.group(2))
            return -value if direction.startswith("decreas") else value

    # BEA summary-table fallback: HTML whitespace may split cells across lines,
    # so inspect a short window after the row label and use its final number.
    row_match = re.search(
        r"pce price index excluding food and energy(.{0,180})",
        compact,
        re.IGNORECASE,
    )
    if row_match:
        values = re.findall(
            r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", row_match.group(1)
        )
        if values:
            value = float(values[-1])
            if -3.0 <= value <= 3.0:
                return value
    return None


def document_matches_release(document: str, expected_title: str) -> bool:
    normalized_doc = re.sub(r"\s+", " ", html_to_text(document)).lower()
    normalized_title = re.sub(r"\s+", " ", expected_title).strip().lower()
    return normalized_title in normalized_doc


def discover_release_url(document: str, expected_title: str) -> str | None:
    normalized_title = re.sub(r"\s+", " ", expected_title).strip().lower()

    for item in re.findall(r"<item\b.*?</item>", document, re.IGNORECASE | re.DOTALL):
        title_match = re.search(
            r"<title>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</title>",
            item,
            re.IGNORECASE | re.DOTALL,
        )
        if not title_match:
            continue
        item_title = re.sub(
            r"\s+", " ", html.unescape(title_match.group(1))
        ).strip().lower()
        if normalized_title not in item_title:
            continue
        link_match = re.search(
            r"<link>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</link>",
            item,
            re.IGNORECASE | re.DOTALL,
        )
        if link_match:
            candidate = html.unescape(link_match.group(1)).strip()
            if "personal-income-and-outlays" in candidate.lower():
                return candidate

    title_pattern = re.escape(expected_title)
    for pattern in (
        rf'href=["\']([^"\']*personal-income-and-outlays[^"\']*)["\'][^>]*>[^<]*{title_pattern}',
        rf'{title_pattern}.*?href=["\']([^"\']*personal-income-and-outlays[^"\']*)["\']',
    ):
        match = re.search(pattern, document, re.IGNORECASE | re.DOTALL)
        if match:
            candidate = html.unescape(match.group(1)).strip()
            if candidate.startswith("/"):
                return "https://www.bea.gov" + candidate
            return candidate
    return None


def scenario_for_actual(actual: float) -> Scenario:
    if actual <= 0.149:
        return Scenario(
            label="DOVISH / favorevole agli asset rischiosi",
            bias="RIALZISTA",
            bullish_low=65,
            bullish_high=65,
            bearish_low=35,
            bearish_high=35,
            expected_direction="up",
            explanation=(
                "Inflazione core mensile più debole: in teoria riduce la pressione sui tassi "
                "e favorisce BTC, soprattutto se dollaro e rendimenti scendono."
            ),
        )
    if actual <= 0.249:
        return Scenario(
            label="IN LINEA / alto rischio di falso movimento",
            bias="LIEVE RIALZISTA, QUASI NEUTRALE",
            bullish_low=52,
            bullish_high=52,
            bearish_low=48,
            bearish_high=48,
            expected_direction="up",
            explanation=(
                "Dato vicino al consenso: la prima candela può essere riassorbita. "
                "Revisioni e flussi di mercato diventano decisivi."
            ),
        )
    if actual <= 0.349:
        return Scenario(
            label="HAWKISH / sfavorevole agli asset rischiosi",
            bias="RIBASSISTA",
            bullish_low=30,
            bullish_high=32,
            bearish_low=68,
            bearish_high=70,
            expected_direction="down",
            explanation=(
                "Inflazione core più persistente: in teoria rafforza dollaro e rendimenti "
                "e aumenta la pressione su BTC e Nasdaq."
            ),
        )
    return Scenario(
        label="MOLTO HAWKISH / rischio liquidazioni",
        bias="FORTEMENTE RIBASSISTA",
        bullish_low=20,
        bullish_high=25,
        bearish_low=75,
        bearish_high=80,
        expected_direction="down",
        explanation=(
            "Sorpresa inflazionistica forte: aumenta il rischio di vendita globale degli "
            "asset rischiosi e di liquidazioni con volatilità molto elevata."
        ),
    )


def probability_text(low: float, high: float) -> str:
    return f"{low:.0f}%" if low == high else f"{low:.0f}–{high:.0f}%"


def format_it(value: float, digits: int = 2) -> str:
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def surprise_text(actual: float, forecast: float | None) -> str:
    if forecast is None:
        return "consenso non configurato"
    delta = round(actual - forecast, 4)
    if abs(delta) < 0.0001:
        return "in linea con il consenso"
    side = "sopra" if delta > 0 else "sotto"
    return f"{format_it(abs(delta), 1)} punti percentuali {side} il consenso"


def fetch_core_pce_from_api(api_key: str, data_month: str) -> float | None:
    month_name, year_text = data_month.split()
    target_period = f"{year_text}M{MONTHS[month_name.lower()]:02d}"
    params = {
        "UserID": api_key,
        "method": "GetData",
        "datasetname": "NIPA",
        "TableName": "T20807",
        "Frequency": "M",
        "Year": year_text,
        "ResultFormat": "JSON",
    }
    payload = get_json(f"{BEA_API_URL}?{urlencode(params)}", cache_bust=True)
    results = payload.get("BEAAPI", {}).get("Results", {})
    rows = results.get("Data", []) if isinstance(results, Mapping) else []
    for row in rows:
        description = str(row.get("LineDescription", "")).lower()
        if "excluding food and energy" not in description:
            continue
        if str(row.get("TimePeriod", "")) != target_period:
            continue
        try:
            return float(str(row.get("DataValue", "")).replace(",", "").strip())
        except ValueError:
            continue
    return None


def poll_core_pce(
    *,
    release_url: str,
    expected_title: str,
    data_month: str,
    poll_seconds: float,
    deadline: dt.datetime,
    api_key: str | None = None,
) -> tuple[float, str, str, dt.datetime]:
    current_url = release_url
    attempt = 0
    while utc_now() <= deadline:
        attempt += 1
        try:
            status, final_url, document = get_text(current_url, cache_bust=True, timeout=10)
            if status == 200 and document_matches_release(document, expected_title):
                actual = parse_core_pce_mom(document)
                if actual is not None:
                    return actual, final_url, "BEA news release", utc_now()
        except WatcherError:
            pass

        if api_key:
            try:
                actual = fetch_core_pce_from_api(api_key, data_month)
                if actual is not None:
                    return actual, BEA_API_URL, "BEA Data API", utc_now()
            except WatcherError:
                pass

        if attempt % 5 == 0:
            for discovery_url in (BEA_RSS_URL, BEA_CURRENT_RELEASES_URL):
                try:
                    status, _, document = get_text(discovery_url, cache_bust=True, timeout=10)
                    if status == 200:
                        discovered = discover_release_url(document, expected_title)
                        if discovered:
                            current_url = discovered
                            break
                except WatcherError:
                    continue
        time.sleep(max(0.5, poll_seconds))
    raise WatcherError("Core PCE non rilevato entro la finestra configurata")


def get_btc_price() -> MarketSnapshot:
    errors: list[str] = []
    for name, base_url in MARKET_PROVIDERS:
        try:
            payload = get_json(f"{base_url}/api/v3/ticker/price?symbol=BTCUSDT", timeout=8)
            price = float(payload["price"])
            if price <= 0:
                raise ValueError("prezzo non positivo")
            return MarketSnapshot(utc_now(), price, name)
        except (WatcherError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{name}: {exc}")
    raise WatcherError("Nessun feed BTC disponibile; " + " | ".join(errors))


def get_btc_klines(
    *, start_ms: int | None = None, end_ms: int | None = None, limit: int = 100
) -> tuple[list[list[Any]], str]:
    params: dict[str, Any] = {"symbol": "BTCUSDT", "interval": "1m", "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    if end_ms is not None:
        params["endTime"] = end_ms
    errors: list[str] = []
    for name, base_url in MARKET_PROVIDERS:
        try:
            payload = get_json(f"{base_url}/api/v3/klines?{urlencode(params)}", timeout=10)
            if not isinstance(payload, list) or any(
                not isinstance(row, list) or len(row) < 8 for row in payload
            ):
                raise WatcherError("formato candele inatteso")
            return payload, name
        except WatcherError as exc:
            errors.append(f"{name}: {exc}")
    raise WatcherError("Nessun feed volume disponibile; " + " | ".join(errors))


def historical_price_before(release_at: dt.datetime) -> MarketSnapshot | None:
    try:
        candles, source = get_btc_klines(
            end_ms=int(release_at.timestamp() * 1000) - 1, limit=3
        )
        if not candles:
            return None
        row = candles[-1]
        return MarketSnapshot(
            dt.datetime.fromtimestamp(int(row[6]) / 1000, tz=UTC),
            float(row[4]),
            source,
        )
    except WatcherError:
        return None


def volume_ratio(release_at: dt.datetime, now: dt.datetime) -> VolumeStats:
    try:
        candles, source = get_btc_klines(
            start_ms=int((release_at - dt.timedelta(minutes=25)).timestamp() * 1000),
            end_ms=int(now.timestamp() * 1000),
            limit=100,
        )
    except WatcherError:
        return VolumeStats(None, 0, None)
    release_ms = int(release_at.timestamp() * 1000)
    now_ms = int(now.timestamp() * 1000)
    closed = [row for row in candles if int(row[6]) <= now_ms]
    baseline = [float(row[7]) for row in closed if int(row[0]) < release_ms][-20:]
    event = [float(row[7]) for row in closed if int(row[0]) >= release_ms]
    if not baseline or not event:
        return VolumeStats(None, len(event), source)
    base_avg = statistics.mean(baseline)
    ratio = statistics.mean(event) / base_avg if base_avg > 0 else None
    return VolumeStats(ratio, len(event), source)


def pct_change(current: float, baseline: float | None) -> float | None:
    if baseline is None or baseline == 0:
        return None
    return (current / baseline - 1.0) * 100.0


def confirmation(
    scenario: Scenario, move_pct: float | None, ratio: float | None
) -> tuple[str, str]:
    if move_pct is None:
        return "NON VERIFICABILE", "Prezzo pre-release non disponibile."
    if abs(move_pct) < 0.15:
        label = "INDECISA"
        detail = "BTC è ancora vicino al prezzo pre-release."
    else:
        direction = "up" if move_pct > 0 else "down"
        if direction == scenario.expected_direction:
            label = "CONFERMA"
            detail = "La direzione di BTC coincide con la reazione teorica al dato."
        else:
            label = "SMENTITA / WHIPSAW"
            detail = "BTC si muove nella direzione opposta alla reazione teorica."
    if ratio is not None:
        if ratio >= 2.0:
            detail += f" Volume molto forte ({format_it(ratio, 1)}× la media)."
        elif ratio >= 1.2:
            detail += f" Volume sopra media ({format_it(ratio, 1)}×)."
        else:
            detail += f" Volume non espansivo ({format_it(ratio, 1)}×)."
    return label, detail
