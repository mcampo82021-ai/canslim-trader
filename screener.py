"""
screener.py — CAN SLIM + Trade Like a Stock Market Wizard
Extrae datos de yfinance y genera análisis estructurado vía Claude API.

Uso:
    python3 screener.py AXON
    python3 screener.py AXON NVDA CRWD   ← múltiples tickers

Requisitos:
    pip3 install yfinance anthropic python-dotenv

Configuración:
    Crear archivo .env en la misma carpeta con:
    ANTHROPIC_API_KEY=sk-ant-...
"""

import sys
import json
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── dependencias ──────────────────────────────────────────────────────────────
try:
    import yfinance as yf
except ImportError:
    print("❌ Falta yfinance. Ejecuta: pip3 install yfinance")
    sys.exit(1)

try:
    import anthropic
except ImportError:
    print("❌ Falta anthropic. Ejecuta: pip3 install anthropic")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# 1. EXTRACCIÓN DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

def extraer_datos(ticker_symbol: str) -> dict:
    """Extrae todos los datos necesarios para los 4 filtros CAN SLIM."""

    print(f"\n📡 Descargando datos para {ticker_symbol}...")
    t = yf.Ticker(ticker_symbol)
    info = t.info or {}

    datos = {
        "ticker": ticker_symbol,
        "fecha_analisis": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "errores": []
    }

    # ── FILTRO 1: Obligatorio ─────────────────────────────────────────────────

    # Revenue Growth YoY
    try:
        financials = t.financials
        if financials is not None and not financials.empty and "Total Revenue" in financials.index:
            rev = financials.loc["Total Revenue"].dropna()
            if len(rev) >= 2:
                datos["revenue_growth_yoy"] = round(
                    ((rev.iloc[0] - rev.iloc[1]) / abs(rev.iloc[1])) * 100, 1
                )
            else:
                datos["revenue_growth_yoy"] = None
                datos["errores"].append("Revenue: datos insuficientes")
        else:
            datos["revenue_growth_yoy"] = None
            datos["errores"].append("Revenue: no disponible en financials")
    except Exception as e:
        datos["revenue_growth_yoy"] = None
        datos["errores"].append(f"Revenue: {e}")

    # EPS Revisions — aproximación: comparar EPS estimado vs EPS real últimos 4 trimestres
    try:
        eh = t.earnings_history
        if eh is not None and not eh.empty and "epsEstimate" in eh.columns and "epsActual" in eh.columns:
            ultimos = eh.tail(4).dropna(subset=["epsEstimate", "epsActual"])
            beats = (ultimos["epsActual"] > ultimos["epsEstimate"]).sum()
            datos["eps_beats_ultimos_4q"] = int(beats)
            datos["eps_revision_proxy"] = "positivo" if beats >= 3 else "negativo"
        else:
            datos["eps_beats_ultimos_4q"] = None
            datos["eps_revision_proxy"] = None
            datos["errores"].append("EPS history: no disponible")
    except Exception as e:
        datos["eps_beats_ultimos_4q"] = None
        datos["eps_revision_proxy"] = None
        datos["errores"].append(f"EPS: {e}")

    # SMA50 vs SMA200
    try:
        hist = t.history(period="1y")
        if not hist.empty and len(hist) >= 50:
            close = hist["Close"]
            sma50  = round(close.tail(50).mean(), 2)
            sma200 = round(close.tail(200).mean(), 2) if len(close) >= 200 else None
            precio_actual = round(close.iloc[-1], 2)
            datos["precio_actual"]   = precio_actual
            datos["sma50"]           = sma50
            datos["sma200"]          = sma200
            datos["tendencia_alcista"] = bool(sma50 > sma200) if sma200 else None

            # RS aproximado vs SPY
            spy_hist = yf.Ticker("SPY").history(period="1y")["Close"]
            ret_ticker = round((close.iloc[-1] / close.iloc[0] - 1) * 100, 1)
            ret_spy    = round((spy_hist.iloc[-1] / spy_hist.iloc[0] - 1) * 100, 1)
            datos["retorno_1y_ticker"] = ret_ticker
            datos["retorno_1y_spy"]    = ret_spy
            datos["rs_supera_mercado"] = bool(ret_ticker > ret_spy)
            # RS percentil aproximado (0-99)
            datos["rs_rating_aprox"]   = min(99, max(1, int(50 + (ret_ticker - ret_spy) / 2)))
        else:
            datos["errores"].append("Historia de precios insuficiente")
    except Exception as e:
        datos["errores"].append(f"Precios/SMA: {e}")

    # ── FILTRO 2: Calidad del negocio ─────────────────────────────────────────

    datos["roe"]           = round(info.get("returnOnEquity", 0) * 100, 1) if info.get("returnOnEquity") else None
    datos["roic_proxy"]    = datos["roe"]   # ROE como proxy de ROIC

    datos["net_margin"]    = round(info.get("profitMargins", 0) * 100, 1) if info.get("profitMargins") else None

    fcf        = info.get("freeCashflow")
    market_cap = info.get("marketCap")
    net_income = info.get("netIncomeToCommon")

    datos["fcf_yield"] = round((fcf / market_cap) * 100, 1) if fcf and market_cap else None
    datos["fcf_net_income_ratio"] = round((fcf / net_income) * 100, 1) if fcf and net_income and net_income > 0 else None

    de = info.get("debtToEquity")
    datos["debt_equity"] = round(de / 100, 2) if de is not None else None

    # ── FILTRO 3: Validación institucional ────────────────────────────────────

    inst = info.get("institutionPercentHeld")
    datos["inst_ownership_pct"] = round(inst * 100, 1) if inst else None

    target_mean = info.get("targetMeanPrice")
    precio      = datos.get("precio_actual") or info.get("currentPrice")
    if target_mean and precio:
        datos["price_target"]    = round(target_mean, 2)
        datos["target_upside_pct"] = round(((target_mean - precio) / precio) * 100, 1)
    else:
        datos["price_target"]    = None
        datos["target_upside_pct"] = None

    # ── FILTRO 4: Operativo ───────────────────────────────────────────────────

    datos["avg_volume_20d"]  = info.get("averageVolume")
    datos["volume_hoy"]      = info.get("regularMarketVolume")
    datos["market_cap"]      = market_cap
    datos["nombre_empresa"]  = info.get("longName", ticker_symbol)
    datos["sector"]          = info.get("sector", "N/D")
    datos["industria"]       = info.get("industry", "N/D")

    print(f"✅ Datos extraídos para {ticker_symbol}")
    return datos


# ══════════════════════════════════════════════════════════════════════════════
# 2. PROMPT MASTER → CLAUDE API
# ══════════════════════════════════════════════════════════════════════════════

MASTER_PROMPT = """Actúa como analista CAN SLIM + Trade Like a Stock Market Wizard.
Analiza los datos estructurados recibidos y aplica los 4 filtros exactamente en este formato.
No busques información adicional. Solo usa los datos provistos.

DATOS DEL TICKER:
{datos_json}

CUENTA: $1,000 USD | RIESGO POR OPERACIÓN: 2% = $20

---
ANÁLISIS CAN SLIM — {ticker}
Empresa: {nombre} | Sector: {sector} | Fecha: {fecha}

FILTRO 1 — OBLIGATORIO (falla uno → DESCARTAR)
[{f1_rev}] Revenue Growth YoY: {revenue_growth_yoy}% (mínimo >20%)
[{f1_eps}] EPS Revisions proxy: {eps_revision_proxy} ({eps_beats_ultimos_4q}/4 beats recientes)
[{f1_tend}] Tendencia alcista SMA50>SMA200: {tendencia_alcista} (SMA50: {sma50} | SMA200: {sma200})
→ RESULTADO F1: [PASA / DESCARTADA — indicar cuál falló]

FILTRO 2 — CALIDAD DEL NEGOCIO
[{f2_roic}] ROIC proxy (ROE): {roic_proxy}% (mínimo >15%)
[{f2_margin}] Net Margin: {net_margin}% (mínimo >10%)
[{f2_fcf}] FCF Yield: {fcf_yield}% | FCF/Net Income: {fcf_net_income_ratio}% (FCF Yield >3% O FCF/NI >80%)
[{f2_de}] Debt/Equity: {debt_equity} (máximo <1)
→ RESULTADO F2: X/4 [Excelente=4/4 | Aceptable=3/4 | Cuidado=2/4 | Descartar=0-1/4]

FILTRO 3 — VALIDACIÓN INSTITUCIONAL
[{f3_rs}] RS Rating aprox: {rs_rating_aprox}/99 — retorno 1Y: {retorno_1y_ticker}% vs SPY: {retorno_1y_spy}% (mínimo >80)
[{f3_inst}] Institutional Ownership: {inst_ownership_pct}% (mínimo >40%)
[{f3_target}] 1Y Price Target Upside: {target_upside_pct}% → Target: ${price_target} (mínimo >30%)
→ RESULTADO F3: X/3 [Válida=2-3 | Dudosa=1 | No operar=0]

FILTRO 4 — OPERATIVO
[{f4_avgvol}] Avg Volume 20d: {avg_volume_20d_fmt} (mínimo >1M)
[{f4_volvol}] Volume hoy: {volume_hoy_fmt} (mínimo >500K)
[{f4_tend}] Tendencia técnica: SMA50 {sma50} {direcc} SMA200 {sma200}
→ RESULTADO F4: [OPERABLE / NO OPERAR]

SIZING (cuenta $1,000)
Precio actual:      ${precio_actual}
Riesgo 2% = $20
Stop Loss (−7%):    ${stop_loss}
Acciones posibles:  {num_acciones} acciones (${capital_usado} capital usado = {pct_capital}% del portafolio)
Target 1 (+20%):    ${target1}
Target 2 (analistas): ${price_target}
R/R ratio:          {rr_ratio}x

VEREDICTO FINAL: [OPERAR / MONITOREAR / DESCARTAR]
Razón en 2 líneas máximo: [explicación concisa]

PRÓXIMA REVISIÓN: [indicar cuándo revisar según contexto]
"""

def construir_prompt(datos: dict) -> str:
    """Rellena el master prompt con los datos extraídos."""

    precio = datos.get("precio_actual", 0) or 0
    stop   = round(precio * 0.93, 2)
    num_acc = int(20 / (precio - stop)) if precio > stop else 0
    capital = round(num_acc * precio, 0)
    pct_cap = round((capital / 1000) * 100, 1)
    t1      = round(precio * 1.20, 2)
    rr      = round((t1 - precio) / (precio - stop), 1) if precio > stop else 0

    def fmt_vol(v):
        if v is None: return "N/D"
        if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
        if v >= 1_000: return f"{v/1_000:.0f}K"
        return str(v)

    def check(val, umbral, mayor=True):
        if val is None: return "?"
        return "✅" if (val > umbral if mayor else val < umbral) else "❌"

    direcc = ">" if datos.get("tendencia_alcista") else "<"
    return MASTER_PROMPT.format(
        datos_json          = json.dumps(datos, ensure_ascii=False, indent=2),
        ticker              = datos["ticker"],
        nombre              = datos.get("nombre_empresa", datos["ticker"]),
        sector              = datos.get("sector", "N/D"),
        fecha               = datos["fecha_analisis"],

        # Filtro 1
        f1_rev              = check(datos.get("revenue_growth_yoy"), 20),
        f1_eps              = "✅" if datos.get("eps_revision_proxy") == "positivo" else "❌",
        f1_tend             = "✅" if datos.get("tendencia_alcista") else "❌",
        revenue_growth_yoy  = datos.get("revenue_growth_yoy", "N/D"),
        eps_revision_proxy  = datos.get("eps_revision_proxy", "N/D"),
        eps_beats_ultimos_4q= datos.get("eps_beats_ultimos_4q", "N/D"),
        tendencia_alcista   = datos.get("tendencia_alcista", "N/D"),
        sma50               = datos.get("sma50", "N/D"),
        sma200              = datos.get("sma200", "N/D"),

        # Filtro 2
        f2_roic             = check(datos.get("roic_proxy"), 15),
        f2_margin           = check(datos.get("net_margin"), 10),
        f2_fcf              = "✅" if (datos.get("fcf_yield") or 0) > 3 or (datos.get("fcf_net_income_ratio") or 0) > 80 else "❌",
        f2_de               = check(datos.get("debt_equity"), 1, mayor=False),
        roic_proxy          = datos.get("roic_proxy", "N/D"),
        net_margin          = datos.get("net_margin", "N/D"),
        fcf_yield           = datos.get("fcf_yield", "N/D"),
        fcf_net_income_ratio= datos.get("fcf_net_income_ratio", "N/D"),
        debt_equity         = datos.get("debt_equity", "N/D"),

        # Filtro 3
        f3_rs               = check(datos.get("rs_rating_aprox"), 80),
        f3_inst             = check(datos.get("inst_ownership_pct"), 40),
        f3_target           = check(datos.get("target_upside_pct"), 30),
        rs_rating_aprox     = datos.get("rs_rating_aprox", "N/D"),
        retorno_1y_ticker   = datos.get("retorno_1y_ticker", "N/D"),
        retorno_1y_spy      = datos.get("retorno_1y_spy", "N/D"),
        inst_ownership_pct  = datos.get("inst_ownership_pct", "N/D"),
        target_upside_pct   = datos.get("target_upside_pct", "N/D"),
        price_target        = datos.get("price_target", "N/D"),

        # Filtro 4
        f4_avgvol           = "✅" if (datos.get("avg_volume_20d") or 0) > 1_000_000 else "❌",
        f4_volvol           = "✅" if (datos.get("volume_hoy") or 0) > 500_000 else "❌",
        f4_tend             = "✅" if datos.get("tendencia_alcista") else "❌",
        avg_volume_20d_fmt  = fmt_vol(datos.get("avg_volume_20d")),
        volume_hoy_fmt      = fmt_vol(datos.get("volume_hoy")),

        # Sizing
        precio_actual       = precio,
        stop_loss           = stop,
        num_acciones        = num_acc,
        capital_usado       = int(capital),
        pct_capital         = pct_cap,
        target1             = t1,
        rr_ratio            = rr,
        direcc              = direcc,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. LLAMADA A CLAUDE API
# ══════════════════════════════════════════════════════════════════════════════

def analizar_con_claude(prompt: str, ticker: str) -> str:
    """Envía los datos a Claude y devuelve el análisis formateado."""

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("⚠️  Sin ANTHROPIC_API_KEY — mostrando datos crudos sin análisis Claude")
        return prompt

    print(f"🤖 Analizando {ticker} con Claude...")
    client = anthropic.Anthropic(api_key=api_key)

    mensaje = client.messages.create(
        model      = "claude-sonnet-4-5",
        max_tokens = 1000,
        system     = (
            "Eres un analista experto en CAN SLIM y Trade Like a Stock Market Wizard. "
            "Completa el análisis con los datos provistos. "
            "Sé conciso y directo. No agregues información no solicitada. "
            "Siempre mantén exactamente el mismo formato de salida."
        ),
        messages   = [{"role": "user", "content": prompt}]
    )

    return mensaje.content[0].text


# ══════════════════════════════════════════════════════════════════════════════
# 4. GUARDAR REPORTE
# ══════════════════════════════════════════════════════════════════════════════

def guardar_reporte(ticker: str, reporte: str):
    """Guarda el reporte en reports/YYYY-MM-DD/TICKER_reporte.txt"""

    fecha = datetime.now().strftime("%Y-%m-%d")
    carpeta = f"reports/{fecha}"
    os.makedirs(carpeta, exist_ok=True)
    path = f"{carpeta}/{ticker}_reporte.txt"

    with open(path, "w", encoding="utf-8") as f:
        f.write(reporte)

    print(f"💾 Reporte guardado: {path}")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# 5. MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Uso: python3 screener.py TICKER [TICKER2 TICKER3 ...]")
        print("Ejemplo: python3 screener.py AXON")
        sys.exit(1)

    tickers = [t.upper() for t in sys.argv[1:]]
    resultados = []

    for ticker in tickers:
        print(f"\n{'='*60}")
        print(f"  ANALIZANDO: {ticker}")
        print(f"{'='*60}")

        try:
            datos   = extraer_datos(ticker)
            prompt  = construir_prompt(datos)
            reporte = analizar_con_claude(prompt, ticker)
            path    = guardar_reporte(ticker, reporte)

            print(f"\n{reporte}")
            resultados.append({"ticker": ticker, "ok": True, "path": path})

        except Exception as e:
            print(f"❌ Error procesando {ticker}: {e}")
            resultados.append({"ticker": ticker, "ok": False, "error": str(e)})

    # Resumen si son múltiples tickers
    if len(tickers) > 1:
        print(f"\n{'='*60}")
        print("  RESUMEN")
        print(f"{'='*60}")
        for r in resultados:
            estado = "✅" if r["ok"] else "❌"
            print(f"  {estado} {r['ticker']}")


if __name__ == "__main__":
    main()