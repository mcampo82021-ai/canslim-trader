"""
technical_audit.py — CAN SLIM / VCP technical audit helper.

This module turns OHLCV bars into a strict technical verdict. It is designed to
consume TradingView MCP bar exports first, with a yfinance fallback for quick
offline checks.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from statistics import mean
from typing import Any


MIN_BREAKOUT_VOLUME_PCT = 40.0
MAX_BUY_EXTENSION_PCT = 5.0
NEAR_HIGH_PCT = -10.0


@dataclass
class TechnicalAudit:
    ticker: str
    close: float
    sma50: float | None
    sma200: float | None
    trend_ok: bool
    max_52w: float
    pct_from_52w_high: float
    near_high: bool
    pivot: float
    pct_from_pivot: float
    in_buy_zone: bool
    avg_vol_50d: float | None
    latest_volume: float | None
    latest_volume_vs_50d_pct: float | None
    pivot_volume: float | None
    pivot_volume_vs_50d_pct: float | None
    pivot_volume_valid: bool | None
    rs_line_new_high: bool | None
    stage: str
    pattern: str
    verdict: str
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "close": self.close,
            "sma50": self.sma50,
            "sma200": self.sma200,
            "trend_ok": self.trend_ok,
            "max_52w": self.max_52w,
            "pct_from_52w_high": self.pct_from_52w_high,
            "near_high": self.near_high,
            "pivot": self.pivot,
            "pct_from_pivot": self.pct_from_pivot,
            "in_buy_zone": self.in_buy_zone,
            "avg_vol_50d": self.avg_vol_50d,
            "latest_volume": self.latest_volume,
            "latest_volume_vs_50d_pct": self.latest_volume_vs_50d_pct,
            "pivot_volume": self.pivot_volume,
            "pivot_volume_vs_50d_pct": self.pivot_volume_vs_50d_pct,
            "pivot_volume_valid": self.pivot_volume_valid,
            "rs_line_new_high": self.rs_line_new_high,
            "stage": self.stage,
            "pattern": self.pattern,
            "verdict": self.verdict,
            "notes": self.notes,
        }


def _clean_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def normalize_bars(raw_bars: list[dict[str, Any]]) -> list[dict[str, float]]:
    bars: list[dict[str, float]] = []
    for bar in raw_bars:
        close = _clean_float(bar.get("close"))
        high = _clean_float(bar.get("high"))
        low = _clean_float(bar.get("low"))
        volume = _clean_float(bar.get("volume"))
        if close is None or high is None or low is None:
            continue
        bars.append(
            {
                "time": bar.get("time"),
                "open": _clean_float(bar.get("open")) or close,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume or 0.0,
            }
        )
    bars.sort(key=lambda item: item.get("time") or 0)
    return bars


def load_bars_json(path: str) -> list[dict[str, float]]:
    with open(path, "r", encoding="utf-8") as file:
        payload = json.load(file)
    if isinstance(payload, dict):
        raw_bars = payload.get("bars") or payload.get("data") or payload.get("ohlcv")
    else:
        raw_bars = payload
    if not isinstance(raw_bars, list):
        raise ValueError("El JSON debe contener una lista de barras o una clave 'bars'.")
    bars = normalize_bars(raw_bars)
    if len(bars) < 50:
        raise ValueError("Se necesitan al menos 50 barras para una auditoría útil.")
    return bars


def fetch_yfinance_bars(ticker: str, period: str = "18mo") -> list[dict[str, float]]:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("Falta yfinance. Instala yfinance o usa --bars-json.") from exc

    hist = yf.Ticker(ticker).history(period=period)
    if hist is None or hist.empty:
        raise RuntimeError(f"No se pudieron descargar barras para {ticker}.")

    raw_bars: list[dict[str, Any]] = []
    for index, row in hist.iterrows():
        raw_bars.append(
            {
                "time": int(index.timestamp()),
                "open": row.get("Open"),
                "high": row.get("High"),
                "low": row.get("Low"),
                "close": row.get("Close"),
                "volume": row.get("Volume"),
            }
        )
    return normalize_bars(raw_bars)


def simple_sma(values: list[float], length: int) -> float | None:
    if len(values) < length:
        return None
    return mean(values[-length:])


def pct_change(current: float, base: float) -> float | None:
    if base == 0:
        return None
    return (current / base - 1) * 100


def volume_vs_average(volume: float | None, avg_volume: float | None) -> float | None:
    if volume is None or avg_volume is None or avg_volume <= 0:
        return None
    return pct_change(volume, avg_volume)


def detect_pivot(bars: list[dict[str, float]], lookback: int = 252) -> tuple[float, dict[str, float]]:
    window = bars[-min(lookback, len(bars)) :]
    pivot_bar = max(window, key=lambda item: item["high"])
    return pivot_bar["high"], pivot_bar


def detect_stage(closes: list[float], sma50: float | None, sma200: float | None) -> str:
    if sma50 is None or sma200 is None or len(closes) < 220:
        return "UNKNOWN"

    current = closes[-1]
    sma50_prev = mean(closes[-70:-20])
    sma200_prev = mean(closes[-220:-20])

    if current > sma50 > sma200 and sma50 > sma50_prev and sma200 >= sma200_prev * 0.98:
        return "STAGE_2_ADVANCE"
    if current < sma50 < sma200:
        return "STAGE_4_DECLINE"
    if current < sma50 and sma50 > sma200:
        return "STAGE_3_DISTRIBUTION_RISK"
    return "STAGE_1_OR_TRANSITION"


def detect_pattern(bars: list[dict[str, float]], pivot: float) -> str:
    closes = [bar["close"] for bar in bars]
    recent = bars[-65:]
    recent_high = max(bar["high"] for bar in recent)
    recent_low = min(bar["low"] for bar in recent)
    depth_pct = abs((recent_low / recent_high - 1) * 100) if recent_high else 0

    last_15_high = max(bar["high"] for bar in bars[-15:])
    last_15_low = min(bar["low"] for bar in bars[-15:])
    last_15_depth = abs((last_15_low / last_15_high - 1) * 100) if last_15_high else 0

    if depth_pct <= 15 and closes[-1] >= recent_low * 1.08:
        return "FLAT_BASE_OR_TIGHT_AREA"
    if last_15_depth <= 12 and closes[-1] < pivot:
        return "HANDLE_OR_VCP_TIGHTENING"
    if depth_pct <= 33 and closes[-1] > mean(closes[-20:]):
        return "CUP_OR_RECOVERY_BASE"
    return "NO_CLEAR_BASE"


def rs_line_new_high(
    ticker_bars: list[dict[str, float]],
    benchmark_bars: list[dict[str, float]] | None,
    lookback: int = 252,
) -> bool | None:
    if not benchmark_bars:
        return None

    pair_count = min(len(ticker_bars), len(benchmark_bars), lookback)
    if pair_count < 50:
        return None

    rs_values = []
    for stock_bar, bench_bar in zip(ticker_bars[-pair_count:], benchmark_bars[-pair_count:]):
        bench_close = bench_bar["close"]
        if bench_close:
            rs_values.append(stock_bar["close"] / bench_close)

    if len(rs_values) < 50:
        return None
    return rs_values[-1] >= max(rs_values[-64:-1])


def audit_bars(
    ticker: str,
    bars: list[dict[str, float]],
    benchmark_bars: list[dict[str, float]] | None = None,
) -> TechnicalAudit:
    bars = normalize_bars(bars)
    if len(bars) < 50:
        raise ValueError("Se necesitan al menos 50 barras para auditar.")

    closes = [bar["close"] for bar in bars]
    volumes = [bar["volume"] for bar in bars]
    close = closes[-1]
    sma50 = simple_sma(closes, 50)
    sma200 = simple_sma(closes, 200)
    trend_ok = bool(sma50 is not None and sma200 is not None and close > sma50 > sma200)

    high_window = bars[-min(252, len(bars)) :]
    max_52w = max(bar["high"] for bar in high_window)
    pct_from_52w_high = pct_change(close, max_52w) or 0.0
    near_high = pct_from_52w_high >= NEAR_HIGH_PCT

    pivot, pivot_bar = detect_pivot(bars)
    pct_from_pivot = pct_change(close, pivot) or 0.0
    in_buy_zone = 0 <= pct_from_pivot <= MAX_BUY_EXTENSION_PCT

    avg_vol_50d = simple_sma(volumes, 50)
    latest_volume = volumes[-1] if volumes else None
    latest_volume_vs_50d_pct = volume_vs_average(latest_volume, avg_vol_50d)
    pivot_volume = pivot_bar.get("volume")
    pivot_volume_vs_50d_pct = volume_vs_average(pivot_volume, avg_vol_50d)
    pivot_volume_valid = (
        pivot_volume_vs_50d_pct >= MIN_BREAKOUT_VOLUME_PCT
        if pivot_volume_vs_50d_pct is not None
        else None
    )

    rs_new_high = rs_line_new_high(bars, benchmark_bars)
    stage = detect_stage(closes, sma50, sma200)
    pattern = detect_pattern(bars, pivot)

    notes: list[str] = []
    if not trend_ok:
        notes.append("Tendencia no cumple close > SMA50 > SMA200.")
    if not near_high:
        notes.append(f"Precio a {pct_from_52w_high:.1f}% del máximo 52W; no está cerca de nuevos máximos.")
    if pct_from_pivot < 0:
        notes.append(f"Precio {abs(pct_from_pivot):.1f}% debajo del pivot; esperar ruptura.")
    elif pct_from_pivot > MAX_BUY_EXTENSION_PCT:
        notes.append(f"Precio {pct_from_pivot:.1f}% sobre pivot; entrada extendida.")
    if pivot_volume_valid is not True:
        notes.append("Volumen en pivot no confirmado con +40% sobre promedio 50d.")
    if rs_new_high is not True:
        notes.append("RS Line no confirmada en nuevo máximo.")
    if stage != "STAGE_2_ADVANCE":
        notes.append(f"Etapa Minervini aproximada: {stage}.")

    if (
        trend_ok
        and near_high
        and in_buy_zone
        and pivot_volume_valid is True
        and rs_new_high is True
        and stage == "STAGE_2_ADVANCE"
        and pattern != "NO_CLEAR_BASE"
    ):
        verdict = "VALIDO"
    elif trend_ok and near_high and pct_from_pivot < 0 and pattern != "NO_CLEAR_BASE":
        verdict = "ESPERAR"
    else:
        verdict = "INVALIDO"

    return TechnicalAudit(
        ticker=ticker,
        close=round(close, 2),
        sma50=round(sma50, 2) if sma50 is not None else None,
        sma200=round(sma200, 2) if sma200 is not None else None,
        trend_ok=trend_ok,
        max_52w=round(max_52w, 2),
        pct_from_52w_high=round(pct_from_52w_high, 1),
        near_high=near_high,
        pivot=round(pivot, 2),
        pct_from_pivot=round(pct_from_pivot, 1),
        in_buy_zone=in_buy_zone,
        avg_vol_50d=round(avg_vol_50d, 0) if avg_vol_50d is not None else None,
        latest_volume=round(latest_volume, 0) if latest_volume is not None else None,
        latest_volume_vs_50d_pct=round(latest_volume_vs_50d_pct, 1)
        if latest_volume_vs_50d_pct is not None
        else None,
        pivot_volume=round(pivot_volume, 0) if pivot_volume is not None else None,
        pivot_volume_vs_50d_pct=round(pivot_volume_vs_50d_pct, 1)
        if pivot_volume_vs_50d_pct is not None
        else None,
        pivot_volume_valid=pivot_volume_valid,
        rs_line_new_high=rs_new_high,
        stage=stage,
        pattern=pattern,
        verdict=verdict,
        notes=notes,
    )


def print_human(audit: TechnicalAudit) -> None:
    print(f"\nAUDITORÍA TÉCNICA — {audit.ticker}")
    print("=" * 52)
    print(f"Veredicto técnico: {audit.verdict}")
    print(f"Precio: {audit.close} | SMA50: {audit.sma50} | SMA200: {audit.sma200}")
    print(f"Máx 52W: {audit.max_52w} ({audit.pct_from_52w_high}% desde máximo)")
    print(f"Pivot: {audit.pivot} ({audit.pct_from_pivot}% desde pivot)")
    print(
        "Volumen: "
        f"último {audit.latest_volume} ({audit.latest_volume_vs_50d_pct}% vs 50d) | "
        f"pivot {audit.pivot_volume} ({audit.pivot_volume_vs_50d_pct}% vs 50d)"
    )
    print(f"RS Line nuevo máximo: {audit.rs_line_new_high}")
    print(f"Etapa: {audit.stage}")
    print(f"Patrón probable: {audit.pattern}")
    if audit.notes:
        print("\nNotas:")
        for note in audit.notes:
            print(f"- {note}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CAN SLIM technical audit")
    parser.add_argument("ticker", help="Ticker a auditar, por ejemplo PAYS")
    parser.add_argument("--bars-json", help="JSON con barras OHLCV exportadas desde TradingView MCP")
    parser.add_argument("--benchmark-json", help="JSON con barras OHLCV del benchmark para RS Line")
    parser.add_argument("--source", choices=["json", "yfinance"], default="json")
    parser.add_argument("--json", action="store_true", help="Imprime resultado como JSON")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.source == "json":
        if not args.bars_json:
            parser.error("--bars-json es requerido cuando --source=json")
        bars = load_bars_json(args.bars_json)
        benchmark_bars = load_bars_json(args.benchmark_json) if args.benchmark_json else None
    else:
        bars = fetch_yfinance_bars(args.ticker)
        try:
            benchmark_bars = fetch_yfinance_bars("SPY")
        except Exception:
            benchmark_bars = None

    audit = audit_bars(args.ticker.upper(), bars, benchmark_bars)
    if args.json:
        print(json.dumps(audit.as_dict(), ensure_ascii=False, indent=2))
    else:
        print_human(audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
