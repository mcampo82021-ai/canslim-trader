"""
screener_sp500.py — CAN SLIM Mass Screener v3.0 (S&P 500 + Russell 2000)

Arquitectura de filtros (sin Claude hasta el final):
    F1 → Python puro   (~85% descartado, 0 tokens)
    F2 → Python puro   (~70% de restantes descartado, 0 tokens)
    F3 → Python puro   (~60% de restantes descartado, 0 tokens)
    F4 → technical_audit.py (solo finalistas, 0 tokens)
    ↓
    Ranking final → Claude (5-15 tickers máximo)

Uso:
    python3 screener_sp500.py                          # Russell 2000 (default)
    python3 screener_sp500.py --universo sp500
    python3 screener_sp500.py --universo ambos         # ~2500 tickers
    python3 screener_sp500.py --test 10
    python3 screener_sp500.py --resume
    python3 screener_sp500.py --no-claude              # Solo ranking, sin análisis final

Requisitos:
    pip3 install yfinance anthropic python-dotenv openpyxl pandas requests lxml
"""

import sys
import json
import os
import time
import argparse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── Dependencias ──────────────────────────────────────────────────────────────
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

try:
    import pandas as pd
except ImportError:
    print("❌ Falta pandas. Ejecuta: pip3 install pandas lxml")
    sys.exit(1)

try:
    import openpyxl
except ImportError:
    print("❌ Falta openpyxl. Ejecuta: pip3 install openpyxl")
    sys.exit(1)

# Importa desde screener.py (fuente de verdad cuantitativa)
try:
    from screener import (
        extraer_datos, construir_prompt, analizar_con_claude,
        guardar_reporte, calcular_market_direction, evaluar_filtros
    )
except ImportError:
    print("❌ No se encontró screener.py en la misma carpeta.")
    sys.exit(1)

# Importa auditoría técnica
try:
    from technical_audit import audit_bars, fetch_yfinance_bars
    TECHNICAL_AUDIT_DISPONIBLE = True
except ImportError:
    print("⚠️  No se encontró technical_audit.py — F4 técnico desactivado")
    TECHNICAL_AUDIT_DISPONIBLE = False

try:
    from alerts import enviar_telegram, enviar_resumen
except ImportError:
    def enviar_telegram(msg, **kwargs): return False
    def enviar_resumen(resultados): return False


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

ARCHIVO_PROGRESO  = "sp500_progreso.json"
ARCHIVO_GANADORES = "Ganadores_CAN_SLIM.xlsx"
PAUSA_ENTRE_TICKERS = 1.5
PAUSA_TRAS_ERROR    = 5.0


# ══════════════════════════════════════════════════════════════════════════════
# 1. OBTENER TICKERS
# ══════════════════════════════════════════════════════════════════════════════

def obtener_tickers_sp500() -> list[str]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    print("📋 Descargando lista S&P 500 desde Wikipedia...")
    try:
        tablas = pd.read_html(url)
        df = tablas[0]
        col_ticker = next((c for c in df.columns if "symbol" in c.lower() or "ticker" in c.lower()), None)
        if col_ticker is None:
            raise ValueError("No se encontró columna de ticker")
        tickers = df[col_ticker].str.replace(".", "-", regex=False).tolist()
        tickers = [t.strip() for t in tickers if isinstance(t, str) and t.strip()]
        print(f"✅ {len(tickers)} tickers obtenidos del S&P 500")
        return tickers
    except Exception as e:
        print(f"⚠️  Error descargando de Wikipedia: {e} — usando fallback")
        return _fallback_tickers()


def _fallback_tickers() -> list[str]:
    return [
        "AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","TSLA","BRK-B","JPM",
        "V","UNH","XOM","LLY","JNJ","AVGO","MA","PG","HD","MRK","ORCL","CVX",
        "ABBV","COST","ADBE","CRM","AMD","ACN","PEP","NFLX","TMO","MCD","QCOM",
        "CSCO","INTC","WMT","IBM","GE","DIS","BAC","PYPL","UBER","ABNB","SPOT",
        "SQ","ROKU","SNOW","PLTR","ZM","SHOP",
    ]


def obtener_tickers_russell2000() -> list[str]:
    import requests as req
    urls = [
        "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nyse/nyse_full_tickers.json",
        "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nasdaq/nasdaq_full_tickers.json",
    ]
    print("📋 Construyendo universo small+mid cap (NYSE + NASDAQ, market cap $300M-$10B)...")
    try:
        tickers = []
        for url in urls:
            resp = req.get(url, timeout=30)
            resp.raise_for_status()
            for item in resp.json():
                try:
                    mc     = float(item.get("marketCap", 0) or 0)
                    symbol = str(item.get("symbol", "")).strip()
                    if 300_000_000 <= mc <= 10_000_000_000 and symbol.isalpha() and 1 <= len(symbol) <= 6:
                        tickers.append(symbol)
                except Exception:
                    continue
        vistos = set()
        tickers_unicos = [t for t in tickers if not (t in vistos or vistos.add(t))]
        if len(tickers_unicos) < 100:
            raise ValueError(f"Solo {len(tickers_unicos)} tickers — datos incompletos")
        print(f"✅ {len(tickers_unicos)} tickers encontrados")
        return tickers_unicos
    except Exception as e:
        print(f"⚠️  Error: {e} — usando fallback")
        return _fallback_russell2000()


def _fallback_russell2000() -> list[str]:
    return [
        "ORLA","ERO","RELY","IONQ","ALAB","AXON","CELH","RXRX","RDDT","KVYO",
        "APP","SMAR","TMDX","ASTS","LUNR","RKLB","ACHR","JOBY","IREN","CIFR",
        "MARA","RIOT","HUT","CLSK","BTBT","EXAS","NTRA","PCVX","RVMD","KRTX",
        "ARWR","IONS","ALNY","SRPT","RARE","ENPH","SEDG","FSLR","ARRY","NOVA",
        "FTAI","GATX","GNRC","POWL","AAON","MTDR","CIVI","SM","CHRD","VTLE",
    ]


def obtener_tickers(universo: str) -> list[str]:
    if universo == "sp500":
        return obtener_tickers_sp500()
    elif universo == "russell2000":
        return obtener_tickers_russell2000()
    elif universo == "ambos":
        sp500   = obtener_tickers_sp500()
        russell = obtener_tickers_russell2000()
        vistos  = set(sp500)
        combinados = sp500 + [t for t in russell if t not in vistos]
        print(f"📊 Universo combinado: {len(combinados)} tickers únicos")
        return combinados
    else:
        return obtener_tickers_russell2000()


# ══════════════════════════════════════════════════════════════════════════════
# 2. FILTROS F1, F2, F3 EN PYTHON PURO (0 tokens)
# ══════════════════════════════════════════════════════════════════════════════

def pasa_f1(datos: dict) -> tuple[bool, str]:
    """F1: EPS trim YoY ≥25% + EPS revisions positivas + SMA50>SMA200 + mercado OK."""
    razones = []
    eps_trim = datos.get("eps_trimestral_yoy")
    if eps_trim is None:
        razones.append("EPS trim: sin datos")
    elif eps_trim < 25:
        razones.append(f"EPS trim {eps_trim:.1f}% < 25%")

    if datos.get("eps_revision_proxy") != "positivo":
        razones.append(f"EPS revisions: {datos.get('eps_revision_proxy')}")

    if not datos.get("tendencia_alcista"):
        razones.append(f"SMA50 {datos.get('sma50')} < SMA200 {datos.get('sma200')}")

    mercado_en_dist = datos.get("mercado_en_distribucion")
    if mercado_en_dist is not False:
        if mercado_en_dist is None:
            razones.append(f"Mercado desconocido (fail-closed — datos SPY no disponibles)")
        else:
            razones.append(f"Mercado distribución ({datos.get('distribution_days_25d')} days)")

    eps_anual = datos.get("eps_anual_consistente")
    if eps_anual is False:
        razones.append("EPS anual inconsistente (criterio A de O'Neil)")

    return (False, " | ".join(razones)) if razones else (True, "OK")


def pasa_f2(datos: dict) -> tuple[bool, int, list]:
    """F2: Calidad del negocio — mínimo 3/4. Retorna (pasa, score, detalle)."""
    roe    = datos.get("roic_proxy")
    margin = datos.get("net_margin")
    fcf_y  = datos.get("fcf_yield") or 0
    fcf_ni = datos.get("fcf_net_income_ratio") or 0
    de     = datos.get("debt_equity")

    criterios = [
        roe    is not None and roe    >= 17,
        margin is not None and margin >= 10,
        fcf_y > 3 or fcf_ni > 80,
        de     is not None and de     < 1,
    ]
    score = sum(criterios)
    return score >= 3, score, criterios


def pasa_f3(datos: dict) -> tuple[bool, int, list]:
    """F3: Validación institucional — mínimo 2/3. Retorna (pasa, score, detalle)."""
    rs     = datos.get("rs_rating_aprox")
    inst   = datos.get("inst_ownership_pct")
    upside = datos.get("target_upside_pct")

    criterios = [
        rs     is not None and rs     >= 85,
        inst   is not None and inst   >= 40,
        upside is not None and upside >= 30,
    ]
    score = sum(criterios)
    return score >= 2, score, criterios


# ══════════════════════════════════════════════════════════════════════════════
# 3. F4 — AUDITORÍA TÉCNICA (solo para finalistas F1+F2+F3)
# ══════════════════════════════════════════════════════════════════════════════

def ejecutar_f4_tecnico(ticker: str, datos: dict, spy_bars: list) -> dict:
    """
    Corre technical_audit sobre el ticker.
    Retorna dict con verdict, stage, pattern, pivot, pct_from_pivot, notes.
    Si falla, retorna verdict=None para no bloquear el análisis.
    """
    if not TECHNICAL_AUDIT_DISPONIBLE:
        return {"technical_verdict": None, "technical_stage": None,
                "technical_pattern": None, "technical_notes": ["technical_audit no disponible"]}
    try:
        bars = fetch_yfinance_bars(ticker, period="18mo")
        audit = audit_bars(ticker, bars, spy_bars)
        return {
            "technical_verdict":         audit.verdict,
            "technical_stage":           audit.stage,
            "technical_pattern":         audit.pattern,
            "technical_pivot":           audit.pivot,
            "technical_pct_from_pivot":  audit.pct_from_pivot,
            "technical_in_buy_zone":     audit.in_buy_zone,
            "technical_pivot_vol_valid": audit.pivot_volume_valid,
            "technical_rs_new_high":     audit.rs_line_new_high,
            "technical_notes":           audit.notes,
        }
    except Exception as e:
        return {"technical_verdict": None, "technical_stage": None,
                "technical_pattern": None, "technical_notes": [str(e)]}


def veredicto_f4(tech: dict) -> str:
    v = tech.get("technical_verdict")
    if v == "VALIDO":
        return "🟢 OPERAR"
    elif v == "ESPERAR":
        return "👀 WATCHLIST — esperar ruptura técnica"
    elif v == "INVALIDO":
        return "👀 WATCHLIST — patrón técnico INVALIDO"
    else:
        return "👀 WATCHLIST — auditoría técnica no disponible"


# ══════════════════════════════════════════════════════════════════════════════
# 4. PERSISTENCIA DE PROGRESO
# ══════════════════════════════════════════════════════════════════════════════

def cargar_progreso() -> dict:
    if os.path.exists(ARCHIVO_PROGRESO):
        try:
            with open(ARCHIVO_PROGRESO, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"procesados": [], "finalistas": [], "descartados_f2f3": []}


def guardar_progreso(progreso: dict):
    import numpy as np
    class NumpyEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer,)): return int(obj)
            if isinstance(obj, (np.floating,)): return float(obj)
            if isinstance(obj, np.ndarray): return obj.tolist()
            return super().default(obj)
    with open(ARCHIVO_PROGRESO, "w", encoding="utf-8") as f:
        json.dump(progreso, f, ensure_ascii=False, indent=2, cls=NumpyEncoder)


# ══════════════════════════════════════════════════════════════════════════════
# 5. EXPORTAR EXCEL
# ══════════════════════════════════════════════════════════════════════════════

def exportar_excel(ganadores: list, ruta: str = ARCHIVO_GANADORES):
    if not ganadores:
        print("⚠️  No hay ganadores para exportar")
        return

    filas = []
    for g in ganadores:
        d = g.get("datos", {})
        filas.append({
            "Ticker":               g["ticker"],
            "Empresa":              d.get("nombre_empresa", g["ticker"]),
            "Sector":               d.get("sector", "N/D"),
            "Industria":            d.get("industria", "N/D"),
            "Precio ($)":           d.get("precio_actual"),
            "RS Rating":            d.get("rs_rating_aprox"),
            "Retorno 1Y (%)":       d.get("retorno_1y_ticker"),
            "EPS Trim YoY (%)":     d.get("eps_trimestral_yoy"),
            "Sales Trim YoY (%)":   d.get("sales_trimestral_yoy"),
            "ROE (%)":              d.get("roic_proxy"),
            "Net Margin (%)":       d.get("net_margin"),
            "FCF Yield (%)":        d.get("fcf_yield"),
            "FCF/NI (%)":           d.get("fcf_net_income_ratio"),
            "D/E":                  d.get("debt_equity"),
            "Inst. Ownership (%)":  d.get("inst_ownership_pct"),
            "Target Upside (%)":    d.get("target_upside_pct"),
            "Price Target ($)":     d.get("price_target"),
            "SMA50":                d.get("sma50"),
            "SMA200":               d.get("sma200"),
            # Columnas técnicas F4
            "Técnico Veredicto":    d.get("technical_verdict", "N/D"),
            "Etapa Minervini":      d.get("technical_stage", "N/D"),
            "Patrón":               d.get("technical_pattern", "N/D"),
            "Pivot ($)":            d.get("technical_pivot"),
            "% desde Pivot":        d.get("technical_pct_from_pivot"),
            "Zona Compra":          d.get("technical_in_buy_zone"),
            # Veredicto final
            "Veredicto":            g.get("veredicto", "WATCHLIST"),
            "F2 Score":             g.get("f2_score"),
            "F3 Score":             g.get("f3_score"),
            "Fecha":                d.get("fecha_analisis", datetime.now().strftime("%Y-%m-%d")),
        })

    df = pd.DataFrame(filas)

    # Orden de prioridad: OPERAR primero, luego por RS Rating
    orden_veredicto = {"🟢 OPERAR": 0, "👀 WATCHLIST — esperar ruptura técnica": 1,
                       "👀 WATCHLIST — patrón técnico INVALIDO": 2,
                       "👀 WATCHLIST — auditoría técnica no disponible": 3}
    df["_orden"] = df["Veredicto"].map(orden_veredicto).fillna(9)
    df = df.sort_values(["_orden", "RS Rating"], ascending=[True, False]).drop("_orden", axis=1).reset_index(drop=True)

    with pd.ExcelWriter(ruta, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Finalistas", index=False)
        ws = writer.sheets["Finalistas"]

        from openpyxl.styles import PatternFill, Font, Alignment

        # Header
        header_fill = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = Font(color="FFFFFF", bold=True, size=10)
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # Colores por veredicto
        verde    = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        amarillo = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
        naranja  = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")

        col_veredicto = list(df.columns).index("Veredicto")
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            v = str(row[col_veredicto].value or "")
            if "OPERAR" in v and "WATCHLIST" not in v:
                fill = verde
            elif "esperar" in v.lower():
                fill = amarillo
            else:
                fill = naranja
            for cell in row:
                cell.fill = fill

        # Anchos automáticos aproximados
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

        ws.freeze_panes = "A2"

    print(f"\n📊 Excel exportado: {ruta} ({len(filas)} finalistas)")
    return ruta


# ══════════════════════════════════════════════════════════════════════════════
# 6. ANÁLISIS FINAL CON CLAUDE (solo finalistas F1+F2+F3+F4)
# ══════════════════════════════════════════════════════════════════════════════

def analizar_finalistas_con_claude(finalistas: list, sin_claude: bool) -> list:
    """
    Toma la lista de finalistas que pasaron F1+F2+F3+F4 y los analiza con Claude.
    Actualiza el campo 'veredicto' y 'reporte' de cada uno.
    """
    if not finalistas:
        return finalistas

    operables = [f for f in finalistas if f.get("technical_verdict") == "VALIDO"]
    watchlist  = [f for f in finalistas if f.get("technical_verdict") != "VALIDO"]

    print(f"\n{'='*65}")
    print(f"  🤖 ANÁLISIS CLAUDE — {len(operables)} con técnico VALIDO")
    print(f"{'='*65}")

    for item in operables:
        ticker = item["ticker"]
        datos  = item["datos"]
        print(f"\n  Analizando {ticker} con Claude...")

        if sin_claude:
            item["reporte"]   = f"[SIN CLAUDE] {ticker} — F1+F2+F3+F4 VALIDO"
            item["veredicto"] = "🟢 OPERAR — pendiente análisis Claude"
            continue

        try:
            # Inyectar datos técnicos en datos para que el prompt los incluya
            for k, v in item.items():
                if k.startswith("technical_"):
                    datos[k] = v

            filtros  = evaluar_filtros(datos)
            # Forzar pasa_f4=True ya que pasó technical_audit
            filtros["pasa_f4"]   = True
            filtros["veredicto"] = "🟢 OPERAR — F4 técnica válida"

            prompt  = construir_prompt(datos, filtros)
            reporte = analizar_con_claude(prompt, ticker)
            guardar_reporte(ticker, reporte)
            print(f"\n{reporte}")

            # Alerta Telegram para los que pasan todo
            enviar_telegram(f"🟢 *{ticker}* — OPERAR\n{reporte[:2000]}")

            item["reporte"]   = reporte
            item["veredicto"] = "🟢 OPERAR"

        except Exception as e:
            print(f"  ❌ Error Claude para {ticker}: {e}")
            item["veredicto"] = "🟢 OPERAR — error en análisis Claude"

    # Los ESPERAR también los reporta sin Claude (solo datos)
    for item in watchlist:
        ticker = item["ticker"]
        tv = item.get("technical_verdict", "N/D")
        item["veredicto"] = veredicto_f4(item)
        print(f"  👀 {ticker} — técnico {tv} → {item['veredicto']}")

    return operables + watchlist


# ══════════════════════════════════════════════════════════════════════════════
# 7. MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="CAN SLIM Mass Screener v3.0")
    parser.add_argument("--test",      type=int, metavar="N", help="Solo primeros N tickers")
    parser.add_argument("--resume",    action="store_true",   help="Retoma sesión interrumpida")
    parser.add_argument("--no-claude", action="store_true",   help="Sin Claude (solo ranking)")
    parser.add_argument("--universo",  type=str, default="russell2000",
                        choices=["sp500", "russell2000", "ambos"])
    args = parser.parse_args()

    progreso  = cargar_progreso() if args.resume else {"procesados": [], "finalistas": [], "descartados_f2f3": []}
    ya_vistos = set(progreso["procesados"])
    finalistas = progreso.get("finalistas", [])

    # ── Universo ──────────────────────────────────────────────────────────────
    todos_tickers = obtener_tickers(args.universo)
    if args.test:
        todos_tickers = todos_tickers[:args.test]
        print(f"🧪 MODO TEST: {args.test} tickers")

    tickers_pendientes = [t for t in todos_tickers if t not in ya_vistos]
    total      = len(todos_tickers)
    pendientes = len(tickers_pendientes)
    print(f"\n📊 Universo [{args.universo.upper()}]: {total} tickers | Pendientes: {pendientes}")

    # ── SPY — una sola descarga para todo el run ──────────────────────────────
    print("\n📡 Descargando SPY...")
    try:
        spy_hist  = yf.Ticker("SPY").history(period="1y")
        spy_close = spy_hist["Close"]
        ret_spy   = round((spy_close.iloc[-1] / spy_close.iloc[0] - 1) * 100, 1)
        print(f"✅ SPY listo — Retorno 1Y: {ret_spy}%")
    except Exception as e:
        print(f"⚠️  SPY falló: {e}")
        spy_close = None
        ret_spy   = None

    # SPY bars para technical_audit (18 meses)
    spy_bars_tech = None
    if TECHNICAL_AUDIT_DISPONIBLE:
        try:
            spy_bars_tech = fetch_yfinance_bars("SPY", period="18mo")
            print("✅ SPY bars técnicos listos")
        except Exception as e:
            print(f"⚠️  SPY bars técnicos fallaron: {e}")

    # ── Market Direction ──────────────────────────────────────────────────────
    market_data = calcular_market_direction()
    print(f"\n🌐 Market: {market_data.get('market_direction_status', 'N/D')} "
          f"({market_data.get('distribution_days_25d', '?')} dist. days)")

    # ── Contadores ────────────────────────────────────────────────────────────
    procesados_sesion = 0
    desc_f1 = desc_f2 = desc_f3 = errores_count = 0
    inicio  = datetime.now()

    os.makedirs(f"reports/{datetime.now().strftime('%Y-%m-%d')}", exist_ok=True)

    print(f"\n{'='*65}")
    print(f"  🚀 SCREENER v3.0 — {datetime.now().strftime('%H:%M:%S')}")
    print(f"  Flujo: F1→F2→F3 (Python) → F4 (technical_audit) → Claude")
    print(f"{'='*65}")

    # ══════════════════════════════════════════════════════════════════════════
    # BUCLE PRINCIPAL — F1, F2, F3 en Python puro (0 tokens)
    # ══════════════════════════════════════════════════════════════════════════
    for i, ticker in enumerate(tickers_pendientes, start=1):
        num_global = total - pendientes + i
        print(f"\n[{num_global}/{total}] {ticker}", end=" ")

        try:
            datos = extraer_datos(ticker, market_data)

            # Inyectar SPY pre-calculado
            if spy_close is not None and "retorno_1y_ticker" in datos:
                datos["retorno_1y_spy"]  = ret_spy
                ret_t = datos.get("retorno_1y_ticker", 0) or 0
                datos["rs_rating_aprox"] = min(99, max(1, int(50 + (ret_t - ret_spy) / 2)))
                datos["rs_supera_mercado"] = bool(ret_t > ret_spy)

            # ── F1 ────────────────────────────────────────────────────────────
            ok_f1, motivo_f1 = pasa_f1(datos)
            if not ok_f1:
                desc_f1 += 1
                print(f"→ 🚫 F1: {motivo_f1}")
                progreso["procesados"].append(ticker)
                guardar_progreso(progreso)
                time.sleep(PAUSA_ENTRE_TICKERS)
                continue

            # ── F2 ────────────────────────────────────────────────────────────
            ok_f2, score_f2, _ = pasa_f2(datos)
            if not ok_f2:
                desc_f2 += 1
                print(f"→ 🚫 F2: {score_f2}/4")
                progreso["procesados"].append(ticker)
                guardar_progreso(progreso)
                time.sleep(PAUSA_ENTRE_TICKERS)
                continue

            # ── F3 ────────────────────────────────────────────────────────────
            ok_f3, score_f3, _ = pasa_f3(datos)
            if not ok_f3:
                desc_f3 += 1
                print(f"→ 🚫 F3: {score_f3}/3 | RS:{datos.get('rs_rating_aprox')} Inst:{datos.get('inst_ownership_pct')} Up:{datos.get('target_upside_pct')}")
                progreso["procesados"].append(ticker)
                guardar_progreso(progreso)
                time.sleep(PAUSA_ENTRE_TICKERS)
                continue

            # ── F1+F2+F3 OK → F4 technical_audit ─────────────────────────────
            print(f"→ ✅ F1+F2({score_f2}/4)+F3({score_f3}/3) — ejecutando auditoría técnica...")
            tech = ejecutar_f4_tecnico(ticker, datos, spy_bars_tech)
            datos.update(tech)

            tv = tech.get("technical_verdict", "N/D")
            print(f"   📐 Técnico: {tv} | Etapa: {tech.get('technical_stage','?')} | Patrón: {tech.get('technical_pattern','?')}")

            finalistas.append({
                "ticker":    ticker,
                "datos":     datos,
                "f2_score":  score_f2,
                "f3_score":  score_f3,
                "veredicto": veredicto_f4(tech),
                **tech,
            })
            progreso["finalistas"] = finalistas

        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupción — guardando progreso...")
            guardar_progreso(progreso)
            break

        except Exception as e:
            errores_count += 1
            print(f"→ ❌ Error: {e}")
            time.sleep(PAUSA_TRAS_ERROR)

        finally:
            if ticker not in ya_vistos:
                ya_vistos.add(ticker)
                progreso["procesados"].append(ticker)
            procesados_sesion += 1
            guardar_progreso(progreso)

        time.sleep(PAUSA_ENTRE_TICKERS)

    # ══════════════════════════════════════════════════════════════════════════
    # FASE 2 — Claude solo para finalistas
    # ══════════════════════════════════════════════════════════════════════════
    if finalistas:
        finalistas = analizar_finalistas_con_claude(finalistas, args.no_claude)

    # ══════════════════════════════════════════════════════════════════════════
    # RESUMEN FINAL
    # ══════════════════════════════════════════════════════════════════════════
    duracion = datetime.now() - inicio
    mins = int(duracion.total_seconds() // 60)
    segs = int(duracion.total_seconds() % 60)

    operar   = [f for f in finalistas if "OPERAR" in f.get("veredicto","") and "WATCHLIST" not in f.get("veredicto","")]
    watchlist = [f for f in finalistas if "WATCHLIST" in f.get("veredicto","")]

    print(f"\n{'='*65}")
    print(f"  ✅ SCREENER v3.0 COMPLETADO — {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*65}")
    print(f"  📊 Procesados esta sesión : {procesados_sesion}")
    print(f"  🚫 Descartados F1         : {desc_f1}")
    print(f"  🚫 Descartados F2         : {desc_f2}")
    print(f"  🚫 Descartados F3         : {desc_f3}")
    print(f"  📐 Pasaron F1+F2+F3       : {len(finalistas)}")
    print(f"  🟢 OPERAR                 : {len(operar)}")
    print(f"  👀 WATCHLIST              : {len(watchlist)}")
    print(f"  ❌ Errores                : {errores_count}")
    print(f"  ⏱️  Duración               : {mins}m {segs}s")
    print(f"  💰 Tokens Claude usados   : {len(operar)} llamadas (solo finalistas VALIDO)")

    if operar:
        print(f"\n  🟢 CANDIDATOS OPERAR:")
        for f in operar:
            print(f"     {f['ticker']:8} RS:{f['datos'].get('rs_rating_aprox','?'):3} "
                  f"F2:{f['f2_score']}/4 F3:{f['f3_score']}/3 "
                  f"Etapa:{f.get('technical_stage','?')} "
                  f"Patrón:{f.get('technical_pattern','?')}")

    if finalistas:
        exportar_excel(finalistas, ARCHIVO_GANADORES)

    # Limpiar progreso si completó
    if len(progreso["procesados"]) >= total and os.path.exists(ARCHIVO_PROGRESO):
        os.remove(ARCHIVO_PROGRESO)
        print("  🗑️  Progreso limpiado (sesión completa)")

    print(f"\n{'='*65}\n")


if __name__ == "__main__":
    main()
