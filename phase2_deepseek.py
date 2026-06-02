"""
phase2_deepseek.py — Auditoría DeepSeek de tickers ESPERAR del screener CAN SLIM.

Lee Ganadores_CAN_SLIM.xlsx, filtra Técnico Veredicto == ESPERAR, enriquece con
rs_line_new_high y pivot_volume_valid via technical_audit.py, y audita cada uno
con DeepSeek-chat. Imprime tabla resumen + observaciones.
"""

from __future__ import annotations

import json
import os

import openpyxl
import requests
from dotenv import load_dotenv

from technical_audit import audit_bars, fetch_yfinance_bars

load_dotenv()
KEY = os.getenv("DEEPSEEK_API_KEY")
EXCEL_PATH = "Ganadores_CAN_SLIM.xlsx"

SYSTEM = """Eres auditor técnico CAN SLIM / VCP (Mark Minervini). Analizás el setup de una acción para decidir si vale seguirla de cerca antes de un breakout.

Respondé SOLO con JSON válido, sin texto adicional:
{"pre_veredicto": "APROBADO|REVISAR|RECHAZADO", "puntaje": 0-100, "obs": ["..."], "sugerencias": ["..."]}

Criterios:
- APROBADO (80-100): Stage 2, patrón handle/VCP, rs_line_new_high=true, distancia pivot <5%, volumen secándose
- REVISAR (50-79): Stage 2, buen patrón, pero rs no confirmada o distancia 5-10%
- RECHAZADO (<50): Sin base clara, Stage 3/4, distancia >10% o rs débil
"""


def load_esperar_tickers() -> list[dict]:
    wb = openpyxl.load_workbook(EXCEL_PATH)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    idx = {h: i for i, h in enumerate(headers)}
    rows = []
    for row in list(ws.rows)[1:]:
        vals = [c.value for c in row]
        if vals[idx["Técnico Veredicto"]] == "ESPERAR":
            rows.append({
                "ticker":         vals[idx["Ticker"]],
                "empresa":        vals[idx["Empresa"]],
                "sector":         vals[idx["Sector"]],
                "precio":         vals[idx["Precio ($)"]],
                "rs_rating":      vals[idx["RS Rating"]],
                "pivot":          vals[idx["Pivot ($)"]],
                "pct_from_pivot": vals[idx["% desde Pivot"]],
                "stage":          vals[idx["Etapa Minervini"]],
                "pattern":        vals[idx["Patrón"]],
            })
    return rows


def enrich_with_technical(row: dict) -> dict:
    try:
        bars = fetch_yfinance_bars(row["ticker"])
        spy_bars = fetch_yfinance_bars("SPY")
        audit = audit_bars(row["ticker"], bars, spy_bars)
        row["rs_line_new_high"]       = audit.rs_line_new_high
        row["pivot_volume_valid"]     = audit.pivot_volume_valid
        row["pivot_volume_vs_50d_pct"] = audit.pivot_volume_vs_50d_pct
    except Exception as exc:
        row["rs_line_new_high"]        = None
        row["pivot_volume_valid"]      = None
        row["pivot_volume_vs_50d_pct"] = None
        row["_error"]                  = str(exc)
    return row


def audit_deepseek(row: dict) -> dict:
    payload = {k: v for k, v in row.items() if not k.startswith("_") and k not in ("empresa", "sector")}
    resp = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.1,
        },
        timeout=30,
    )
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def print_table(results: list[dict]) -> None:
    header = (
        f"{'Ticker':<8} {'Pivot':>7} {'%Pivot':>7} {'RS High':>8} "
        f"{'Vol OK':>7} {'Pattern':<28} {'Veredicto':<12} {'Puntaje':>7}"
    )
    print("\n" + header)
    print("-" * len(header))
    for r in results:
        row, ds = r["row"], r["deepseek"]
        print(
            f"{row['ticker']:<8} "
            f"{row['pivot']:>7.2f} "
            f"{row['pct_from_pivot']:>7.1f} "
            f"{str(row.get('rs_line_new_high')):>8} "
            f"{str(row.get('pivot_volume_valid')):>7} "
            f"{row['pattern']:<28} "
            f"{ds.get('pre_veredicto', 'ERROR'):<12} "
            f"{str(ds.get('puntaje', '-')):>7}"
        )

    print()
    for r in results:
        row, ds = r["row"], r["deepseek"]
        veredicto = ds.get("pre_veredicto", "ERROR")
        puntaje = ds.get("puntaje", "-")
        print(f"[{row['ticker']}] {row['empresa']} — {veredicto} ({puntaje}/100)")
        for obs in ds.get("obs", []):
            print(f"  • {obs}")
        for sug in ds.get("sugerencias", []):
            print(f"  → {sug}")
        print()


if __name__ == "__main__":
    print("Leyendo tickers ESPERAR desde Ganadores_CAN_SLIM.xlsx...")
    tickers = load_esperar_tickers()
    print(f"Encontrados: {[r['ticker'] for r in tickers]}\n")

    results = []
    for row in tickers:
        ticker = row["ticker"]
        print(f"[{ticker}] Obteniendo datos técnicos (yfinance + SPY)...")
        row = enrich_with_technical(row)
        print(f"[{ticker}] Auditando con DeepSeek...")
        try:
            ds_result = audit_deepseek(row)
        except Exception as exc:
            ds_result = {"pre_veredicto": "ERROR", "puntaje": 0, "obs": [str(exc)], "sugerencias": []}
        results.append({"row": row, "deepseek": ds_result})
        veredicto = ds_result.get("pre_veredicto", "ERROR")
        puntaje = ds_result.get("puntaje", "-")
        print(f"[{ticker}] → {veredicto} ({puntaje}/100)\n")

    print_table(results)
