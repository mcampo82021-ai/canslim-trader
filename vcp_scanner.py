#!/usr/bin/env python3
"""
vcp_scanner.py — VCP/CAN SLIM scanner via TradingView CDP
Reutiliza la conexión CDP del proyecto SMC-Indices.

Uso:
    python3 vcp_scanner.py                    # watchlist completa
    python3 vcp_scanner.py COCO               # ticker específico
    python3 vcp_scanner.py COCO RSI INOD      # varios tickers
    python3 vcp_scanner.py COCO --tf W1       # timeframe específico
"""

import sys
import os
import json
import time
import argparse
import urllib.request
from datetime import datetime, timezone

# ── Dependencias ──────────────────────────────────────────────────────────────
try:
    import websocket
except ImportError:
    os.system(f"{sys.executable} -m pip install websocket-client --break-system-packages -q")
    import websocket

try:
    import pandas as pd
    import numpy as np
except ImportError:
    os.system(f"{sys.executable} -m pip install pandas numpy --break-system-packages -q")
    import pandas as pd
    import numpy as np

# ── Importar technical_audit del proyecto CAN SLIM ───────────────────────────
CANSLIM_PATH = os.path.expanduser("~/Documents/CAN SLIM")
if CANSLIM_PATH not in sys.path:
    sys.path.insert(0, CANSLIM_PATH)
from technical_audit import audit_bars, normalize_bars

# ── Config ────────────────────────────────────────────────────────────────────
CDP_HOST = "localhost"
CDP_PORT = 9222
TV_APP   = "/Applications/TradingView.app/Contents/MacOS/TradingView"

WATCHLIST = ["COCO", "RSI", "KRYS", "AGX", "INOD", "GOOGL", "NVDA", "AMD", "YOU", "MYRG", "LPG"]

_CHART_API = "window.TradingViewApi._activeChartWidgetWV.value()"
_BARS_PATH = f"{_CHART_API}._chartWidget.model().mainSeries().bars()"

_TF_MAP = {
    "D1": "D", "W1": "W", "H4": "240", "H1": "60",
    "D": "D", "W": "W", "240": "240", "60": "60",
}


# ── CDP Client (basado en SMC-Indices) ────────────────────────────────────────
class CDPClient:
    def __init__(self, host=CDP_HOST, port=CDP_PORT):
        self.host = host
        self.port = port
        self.ws   = None
        self._id  = 0

    def _get_page(self):
        url = f"http://{self.host}:{self.port}/json"
        with urllib.request.urlopen(url, timeout=5) as r:
            pages = json.loads(r.read())
        for p in pages:
            if p.get("type") == "page":
                return p
        return pages[0]

    def connect(self):
        page = self._get_page()
        self.ws = websocket.WebSocket()
        self.ws.connect(page["webSocketDebuggerUrl"], suppress_origin=True)
        self.ws.settimeout(30)
        print(f"✅ CDP conectado: {page.get('title', 'TradingView')[:50]}")

    def execute(self, js: str):
        self._id += 1
        msg = {"id": self._id, "method": "Runtime.evaluate",
               "params": {"expression": js, "returnByValue": True, "awaitPromise": True}}
        self.ws.send(json.dumps(msg))
        while True:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") == self._id:
                result = data.get("result", {}).get("result", {})
                if result.get("type") == "object" and result.get("subtype") == "error":
                    raise RuntimeError(result.get("description", "JS error"))
                return result.get("value")

    def close(self):
        if self.ws:
            self.ws.close()


# ── TradingView helpers ───────────────────────────────────────────────────────
def tv_set_symbol(cdp: CDPClient, symbol: str, tf: str = "D"):
    """Cambia el símbolo y timeframe en TradingView."""
    tv_tf = _TF_MAP.get(tf, tf)
    js = f"""
    (function() {{
        var chart = {_CHART_API};
        if (!chart) return 'NO_CHART';
        chart.setSymbol('{symbol}', '{tv_tf}');
        return 'OK';
    }})()
    """
    result = cdp.execute(js)
    time.sleep(3)  # esperar que cargue el chart
    return result


def tv_get_ohlcv(cdp: CDPClient, count: int = 252) -> list[dict]:
    """Extrae barras OHLCV del chart activo."""
    js = f"""
    (function() {{
        var bars = {_BARS_PATH};
        if (!bars || typeof bars.lastIndex !== 'function') return null;
        var end   = bars.lastIndex();
        var start = Math.max(bars.firstIndex(), end - {count} + 1);
        var result = [];
        for (var i = start; i <= end; i++) {{
            var v = bars.valueAt(i);
            if (v) result.push({{
                time: v[0], open: v[1], high: v[2],
                low: v[3], close: v[4], volume: v[5] || 0
            }});
        }}
        return result;
    }})()
    """
    bars = cdp.execute(js)
    if not bars:
        raise RuntimeError("Sin datos OHLCV — chart puede estar cargando.")
    return bars


# ── Lanzar TradingView con CDP ────────────────────────────────────────────────
def ensure_cdp_running():
    """Verifica si CDP está activo, si no lanza TradingView."""
    try:
        url = f"http://{CDP_HOST}:{CDP_PORT}/json"
        with urllib.request.urlopen(url, timeout=3) as r:
            json.loads(r.read())
        print("✅ TradingView CDP activo en localhost:9222")
        return True
    except Exception:
        print("⚠️  CDP no responde — lanzando TradingView...")
        os.system(f"pkill -a -i 'TradingView' 2>/dev/null; sleep 2")
        os.system(f"'{TV_APP}' --remote-debugging-port=9222 &")
        print("⏳ Esperando 15s que TradingView cargue...")
        time.sleep(15)
        try:
            with urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5) as r:
                json.loads(r.read())
            print("✅ TradingView CDP listo")
            return True
        except Exception as e:
            print(f"❌ CDP no responde: {e}")
            return False


# ── Análisis VCP ──────────────────────────────────────────────────────────────
def escanear_ticker(cdp: CDPClient, ticker: str, tf: str = "D1") -> dict:
    """Extrae barras y corre technical_audit para detectar VCP."""
    print(f"\n🔍 Escaneando {ticker} ({tf})...")

    # Cambiar símbolo en TradingView
    result = tv_set_symbol(cdp, ticker, tf)
    if result == "NO_CHART":
        return {"ticker": ticker, "error": "Chart no disponible"}

    # Extraer barras
    try:
        bars_raw = tv_get_ohlcv(cdp, count=300)
    except RuntimeError as e:
        return {"ticker": ticker, "error": str(e)}

    # Obtener benchmark SPY para RS Line
    print(f"   📡 Obteniendo SPY para RS Line...")
    tv_set_symbol(cdp, "SPY", tf)
    try:
        spy_bars = tv_get_ohlcv(cdp, count=300)
    except Exception:
        spy_bars = None

    # Volver al ticker
    tv_set_symbol(cdp, ticker, tf)

    # Auditoría técnica VCP
    audit = audit_bars(ticker, bars_raw, spy_bars)

    return {
        "ticker":    ticker,
        "tf":        tf,
        "verdict":   audit.verdict,
        "stage":     audit.stage,
        "pattern":   audit.pattern,
        "pivot":     audit.pivot,
        "pct_pivot": audit.pct_from_pivot,
        "rs_high":   audit.rs_line_new_high,
        "vol_valid": audit.pivot_volume_valid,
        "notes":     audit.notes,
    }


def print_resumen(resultados: list):
    print(f"\n{'='*70}")
    print(f"  📊 RESUMEN VCP SCANNER — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}")
    print(f"{'Ticker':<8} {'Verdict':<10} {'Stage':<22} {'Patrón':<28} {'%Pivot':>7} {'RS':>6} {'Vol':>6}")
    print("-" * 90)
    for r in resultados:
        if "error" in r:
            print(f"{r['ticker']:<8} ERROR: {r['error']}")
            continue
        print(
            f"{r['ticker']:<8} {r['verdict']:<10} {r['stage']:<22} "
            f"{r['pattern']:<28} {str(r['pct_pivot']):>7} "
            f"{str(r['rs_high']):>6} {str(r['vol_valid']):>6}"
        )

    validos  = [r for r in resultados if r.get("verdict") == "VALIDO"]
    esperar  = [r for r in resultados if r.get("verdict") == "ESPERAR"]
    print(f"\n  🟢 VALIDO: {len(validos)}  👀 ESPERAR: {len(esperar)}")

    if validos or esperar:
        _enviar_alerta(validos + esperar)


def _enviar_alerta(candidatos: list):
    try:
        from alerts import enviar_telegram
        lineas = ["📐 *VCP Scanner — CAN SLIM*\n"]
        for r in candidatos:
            emoji = "🟢" if r["verdict"] == "VALIDO" else "👀"
            lineas.append(
                f"{emoji} *{r['ticker']}* — {r['verdict']}\n"
                f"  Patrón: {r['pattern']}\n"
                f"  Pivot: {r['pct_pivot']}% | RS: {r['rs_high']} | Vol: {r['vol_valid']}"
            )
        enviar_telegram("\n\n".join(lineas))
    except Exception as e:
        print(f"⚠️  Telegram: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="VCP Scanner via TradingView CDP")
    parser.add_argument("tickers", nargs="*", help="Tickers a escanear")
    parser.add_argument("--tf", default="D1", help="Timeframe (D1, W1, H4)")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers] if args.tickers else WATCHLIST

    if not ensure_cdp_running():
        sys.exit(1)

    cdp = CDPClient()
    try:
        cdp.connect()
        resultados = []
        for ticker in tickers:
            r = escanear_ticker(cdp, ticker, args.tf)
            resultados.append(r)
            time.sleep(1)
        print_resumen(resultados)
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
