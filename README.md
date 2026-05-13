# CAN SLIM Trader — Analizador automático

Stack: Python + yfinance + Claude API | Costo: $0 (salvo tokens Claude)

## Instalación (Mac — Terminal)

```bash
# 1. Clonar / crear carpeta
mkdir canslim-trader && cd canslim-trader

# 2. Instalar dependencias
pip3 install yfinance anthropic python-dotenv

# 3. Configurar API key
cp .env.template .env
# Editar .env y pegar tu ANTHROPIC_API_KEY

# 4. Primer análisis
python3 screener.py AXON
```

## Uso diario

```bash
# Un ticker
python3 screener.py AXON

# Varios tickers (tu watchlist del día desde TradingView)
python3 screener.py AXON NVDA CRWD NET

# Los reportes se guardan automáticamente en:
# reports/2026-05-13/AXON_reporte.txt
```

## Flujo de trabajo

```
1. TradingView Premium → screener visual → identificas 3-5 candidatas
2. python3 screener.py TICKER1 TICKER2 TICKER3
3. Lees los reportes → decides manualmente en IBKR
```

## Estructura de carpetas

```
canslim-trader/
├── screener.py          ← script principal
├── .env                 ← tu API key (NO subir a Git)
├── .env.template        ← plantilla sin keys (sí subir)
├── .gitignore           ← ignora .env y reports/
├── README.md
└── reports/
    └── 2026-05-13/
        └── AXON_reporte.txt
```

## Filtros implementados

| Filtro | Criterio | Fuente |
|--------|----------|--------|
| Revenue Growth YoY | >20% | yfinance financials |
| EPS Revisions proxy | ≥3/4 beats recientes | yfinance earnings_history |
| Tendencia SMA50>200 | Alcista | yfinance history |
| ROIC proxy (ROE) | >15% | yfinance info |
| Net Margin | >10% | yfinance info |
| FCF Yield / FCF/NI | >3% o >80% | yfinance info |
| Debt/Equity | <1 | yfinance info |
| RS Rating aprox | >80 vs SPY | calculado |
| Inst. Ownership | >40% | yfinance info |
| Price Target Upside | >30% | yfinance info |
| Avg Volume | >1M | yfinance info |
| Volume hoy | >500K | yfinance info |

## Notas sobre aproximaciones

- **EPS Revisions**: usa beats históricos como proxy (sin datos de revisiones en tiempo real)
- **RS Rating**: retorno relativo vs SPY 1Y (IBD oficial requiere FMP ~$14/mes)
- **ROIC**: usa ROE como proxy (mismo concepto, diferente denominador)
- Para cuenta >$50K considerar FMP API para mayor precisión institucional

## Git — qué ignorar

Crear `.gitignore`:
```
.env
reports/
__pycache__/
*.pyc
```
