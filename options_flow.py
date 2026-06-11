"""
options_flow.py — Flujo inusual de opciones via Tradier API (Sandbox)

Detecta calls/puts inusuales en la watchlist CAN SLIM y envía alerta Telegram.
Uso:
    python3 options_flow.py                    # watchlist por defecto
    python3 options_flow.py COCO RSI KRYS      # tickers específicos
"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

TRADIER_TOKEN = os.getenv("TRADIER_API_KEY")
BASE_URL = "https://sandbox.tradier.com/v1"
HEADERS = {
    "Authorization": f"Bearer {TRADIER_TOKEN}",
    "Accept": "application/json"
}

WATCHLIST = ["COCO", "RSI", "KRYS", "AMD", "AGX", "GOOGL", "NVDA", "YOU", "INOD", "MYRG", "LPG"]

# Umbral para considerar flujo inusual (calls/puts > X veces el promedio)
UMBRAL_RATIO_CALL_PUT = 2.0   # calls > 2x puts → alcista
UMBRAL_RATIO_PUT_CALL = 2.0   # puts > 2x calls → bajista


def get_option_chain(ticker: str) -> dict:
    """Obtiene la cadena de opciones más próxima para el ticker."""
    # Primero obtenemos las fechas de vencimiento disponibles
    r = requests.get(
        f"{BASE_URL}/markets/options/expirations",
        headers=HEADERS,
        params={"symbol": ticker, "includeAllRoots": True}
    )
    if r.status_code != 200:
        return {}
    
    expirations = r.json().get("expirations", {}).get("date", [])
    if not expirations:
        return {}
    
    # Usamos el vencimiento más próximo
    exp = expirations[0] if isinstance(expirations, list) else expirations
    
    # Obtenemos la cadena completa
    r2 = requests.get(
        f"{BASE_URL}/markets/options/chains",
        headers=HEADERS,
        params={"symbol": ticker, "expiration": exp, "greeks": False}
    )
    if r2.status_code != 200:
        return {}
    
    return r2.json().get("options", {}).get("option", [])


def analizar_flujo(ticker: str) -> dict:
    """Analiza el flujo de calls vs puts para detectar actividad inusual."""
    opciones = get_option_chain(ticker)
    if not opciones:
        return {"ticker": ticker, "error": "Sin datos"}

    calls = [o for o in opciones if o.get("option_type") == "call"]
    puts  = [o for o in opciones if o.get("option_type") == "put"]

    vol_calls = sum(o.get("volume", 0) or 0 for o in calls)
    vol_puts  = sum(o.get("volume", 0) or 0 for o in puts)
    oi_calls  = sum(o.get("open_interest", 0) or 0 for o in calls)
    oi_puts   = sum(o.get("open_interest", 0) or 0 for o in puts)

    ratio_cp = round(vol_calls / vol_puts, 2) if vol_puts > 0 else None
    ratio_pc = round(vol_puts / vol_calls, 2) if vol_calls > 0 else None

    sesgo = "NEUTRAL"
    if ratio_cp and ratio_cp >= UMBRAL_RATIO_CALL_PUT:
        sesgo = "🟢 ALCISTA"
    elif ratio_pc and ratio_pc >= UMBRAL_RATIO_PUT_CALL:
        sesgo = "🔴 BAJISTA"

    return {
        "ticker":    ticker,
        "vol_calls": vol_calls,
        "vol_puts":  vol_puts,
        "oi_calls":  oi_calls,
        "oi_puts":   oi_puts,
        "ratio_c/p": ratio_cp,
        "sesgo":     sesgo,
    }


def enviar_alerta(resultados: list):
    """Envía alerta Telegram con los tickers con flujo inusual."""
    from alerts import enviar_telegram
    
    inusuales = [r for r in resultados if r.get("sesgo") != "NEUTRAL" and "error" not in r]
    if not inusuales:
        print("Sin flujo inusual detectado.")
        return
    
    lineas = ["📊 *Flujo Inusual de Opciones — CAN SLIM Watchlist*\n"]
    for r in inusuales:
        lineas.append(
            f"*{r['ticker']}* {r['sesgo']}\n"
            f"  Calls: {r['vol_calls']:,} vol | Puts: {r['vol_puts']:,} vol\n"
            f"  Ratio C/P: {r['ratio_c/p']}x"
        )
    enviar_telegram("\n\n".join(lineas))


def main():
    tickers = [t.upper() for t in sys.argv[1:]] if len(sys.argv) > 1 else WATCHLIST
    
    print(f"\n📡 Analizando flujo de opciones — {len(tickers)} tickers\n")
    print(f"{'Ticker':<8} {'Vol Calls':>10} {'Vol Puts':>10} {'Ratio C/P':>10} {'Sesgo'}")
    print("-" * 55)
    
    resultados = []
    for ticker in tickers:
        r = analizar_flujo(ticker)
        resultados.append(r)
        if "error" in r:
            print(f"{ticker:<8} {'ERROR':>10}")
        else:
            print(f"{ticker:<8} {r['vol_calls']:>10,} {r['vol_puts']:>10,} {str(r['ratio_c/p']):>10} {r['sesgo']}")
    
    enviar_alerta(resultados)


if __name__ == "__main__":
    main()
