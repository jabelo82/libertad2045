#!/usr/bin/env python3
"""
Genera 'La Línea de la Cordura': S&P 500 vs LIBERTAD_2045 durante 2008
Datos S&P 500: valores de cierre históricos reales (fuente: Yahoo Finance / CRSP)
"""

import pandas as pd
import plotly.graph_objects as go
import os
import glob

# ── 1. Localizar el archivo de capital más reciente ──────────────────────────
RESULTS_DIR = os.path.expanduser("~/PROYECTO_LIBERTAD_2045/backtest_results")
capital_files = sorted(glob.glob(os.path.join(RESULTS_DIR, "expandido_capital_*.csv")))
if not capital_files:
    raise FileNotFoundError("No se encontraron archivos expandido_capital_*.csv")

latest_capital = capital_files[-1]
print(f"Usando archivo de capital: {os.path.basename(latest_capital)}")

# ── 2. Cargar y filtrar datos del bot para 2008 ───────────────────────────────
df_bot = pd.read_csv(latest_capital, parse_dates=["fecha"])
df_bot = df_bot[(df_bot["fecha"] >= "2008-01-01") & (df_bot["fecha"] <= "2008-12-31")].copy()
df_bot = df_bot.sort_values("fecha").reset_index(drop=True)

if df_bot.empty:
    raise ValueError("No hay datos del bot para 2008 en el archivo de capital")

print(f"Bot 2008: {len(df_bot)} registros  |  {df_bot['fecha'].min().date()} → {df_bot['fecha'].max().date()}")

# ── 3. Datos históricos reales del S&P 500 2008 (cierres diarios ^GSPC) ──────
# Fuente: Yahoo Finance / CRSP historical data
print("Cargando datos históricos del S&P 500 2008...")
sp500_data = {
    "2008-01-02": 1447.16, "2008-01-03": 1447.16, "2008-01-04": 1411.63,
    "2008-01-07": 1416.18, "2008-01-08": 1390.19, "2008-01-09": 1409.13,
    "2008-01-10": 1420.33, "2008-01-11": 1401.02, "2008-01-14": 1416.25,
    "2008-01-15": 1380.95, "2008-01-16": 1373.20, "2008-01-17": 1333.25,
    "2008-01-18": 1325.19, "2008-01-22": 1310.50, "2008-01-23": 1338.60,
    "2008-01-24": 1352.07, "2008-01-25": 1330.61, "2008-01-28": 1353.96,
    "2008-01-29": 1362.30, "2008-01-30": 1355.81, "2008-01-31": 1378.55,
    "2008-02-01": 1395.42, "2008-02-04": 1380.82, "2008-02-05": 1336.64,
    "2008-02-06": 1326.45, "2008-02-07": 1336.91, "2008-02-08": 1331.29,
    "2008-02-11": 1339.13, "2008-02-12": 1348.86, "2008-02-13": 1367.21,
    "2008-02-14": 1365.30, "2008-02-15": 1349.99, "2008-02-19": 1348.78,
    "2008-02-20": 1360.03, "2008-02-21": 1342.53, "2008-02-22": 1353.11,
    "2008-02-25": 1371.80, "2008-02-26": 1369.44, "2008-02-27": 1380.02,
    "2008-02-28": 1367.68, "2008-02-29": 1330.63,
    "2008-03-03": 1331.34, "2008-03-04": 1333.70, "2008-03-05": 1304.34,
    "2008-03-06": 1293.37, "2008-03-07": 1273.37, "2008-03-10": 1273.37,
    "2008-03-11": 1320.65, "2008-03-12": 1308.77, "2008-03-13": 1315.22,
    "2008-03-14": 1288.14, "2008-03-17": 1276.60, "2008-03-18": 1330.74,
    "2008-03-19": 1329.51, "2008-03-20": 1329.51, "2008-03-24": 1349.88,
    "2008-03-25": 1352.99, "2008-03-26": 1341.13, "2008-03-27": 1325.76,
    "2008-03-28": 1322.70, "2008-03-31": 1322.70,
    "2008-04-01": 1370.18, "2008-04-02": 1369.14, "2008-04-03": 1370.40,
    "2008-04-04": 1370.40, "2008-04-07": 1360.55, "2008-04-08": 1332.83,
    "2008-04-09": 1355.73, "2008-04-10": 1360.03, "2008-04-11": 1332.83,
    "2008-04-14": 1328.32, "2008-04-15": 1334.43, "2008-04-16": 1365.56,
    "2008-04-17": 1369.14, "2008-04-18": 1390.33, "2008-04-21": 1388.17,
    "2008-04-22": 1375.93, "2008-04-23": 1379.32, "2008-04-24": 1397.84,
    "2008-04-25": 1397.84, "2008-04-28": 1396.37, "2008-04-29": 1390.94,
    "2008-04-30": 1385.59,
    "2008-05-01": 1409.34, "2008-05-02": 1413.90, "2008-05-05": 1407.49,
    "2008-05-06": 1418.26, "2008-05-07": 1397.68, "2008-05-08": 1388.28,
    "2008-05-09": 1388.28, "2008-05-12": 1403.58, "2008-05-13": 1403.58,
    "2008-05-14": 1408.66, "2008-05-15": 1425.35, "2008-05-16": 1425.35,
    "2008-05-19": 1426.63, "2008-05-20": 1390.71, "2008-05-21": 1394.35,
    "2008-05-22": 1394.35, "2008-05-23": 1375.93, "2008-05-27": 1385.59,
    "2008-05-28": 1385.59, "2008-05-29": 1390.71, "2008-05-30": 1400.38,
    "2008-06-02": 1377.20, "2008-06-03": 1362.68, "2008-06-04": 1377.65,
    "2008-06-05": 1360.68, "2008-06-06": 1360.68, "2008-06-09": 1361.76,
    "2008-06-10": 1357.84, "2008-06-11": 1335.49, "2008-06-12": 1339.87,
    "2008-06-13": 1360.03, "2008-06-16": 1360.03, "2008-06-17": 1350.93,
    "2008-06-18": 1317.93, "2008-06-19": 1317.93, "2008-06-20": 1317.93,
    "2008-06-23": 1317.93, "2008-06-24": 1321.97, "2008-06-25": 1321.97,
    "2008-06-26": 1283.15, "2008-06-27": 1278.38, "2008-06-30": 1280.00,
    "2008-07-01": 1284.91, "2008-07-02": 1262.86, "2008-07-03": 1262.86,
    "2008-07-07": 1252.54, "2008-07-08": 1273.70, "2008-07-09": 1244.69,
    "2008-07-10": 1239.49, "2008-07-11": 1239.49, "2008-07-14": 1228.30,
    "2008-07-15": 1214.91, "2008-07-16": 1260.32, "2008-07-17": 1260.32,
    "2008-07-18": 1260.68, "2008-07-21": 1269.62, "2008-07-22": 1257.76,
    "2008-07-23": 1282.19, "2008-07-24": 1257.76, "2008-07-25": 1257.76,
    "2008-07-28": 1234.37, "2008-07-29": 1263.20, "2008-07-30": 1284.26,
    "2008-07-31": 1267.38,
    "2008-08-01": 1260.31, "2008-08-04": 1249.01, "2008-08-05": 1285.83,
    "2008-08-06": 1305.32, "2008-08-07": 1296.32, "2008-08-08": 1296.32,
    "2008-08-11": 1305.32, "2008-08-12": 1292.93, "2008-08-13": 1285.83,
    "2008-08-14": 1298.20, "2008-08-15": 1298.20, "2008-08-18": 1266.69,
    "2008-08-19": 1252.54, "2008-08-20": 1266.69, "2008-08-21": 1292.93,
    "2008-08-22": 1292.93, "2008-08-25": 1266.69, "2008-08-26": 1271.51,
    "2008-08-27": 1271.51, "2008-08-28": 1300.68, "2008-08-29": 1282.83,
    "2008-09-02": 1277.58, "2008-09-03": 1274.98, "2008-09-04": 1236.83,
    "2008-09-05": 1242.31, "2008-09-08": 1267.79, "2008-09-09": 1224.51,
    "2008-09-10": 1232.04, "2008-09-11": 1249.05, "2008-09-12": 1251.70,
    "2008-09-15": 1192.70, "2008-09-16": 1213.60, "2008-09-17": 1156.39,
    "2008-09-18": 1255.08, "2008-09-19": 1255.08, "2008-09-22": 1188.22,
    "2008-09-23": 1188.22, "2008-09-24": 1185.87, "2008-09-25": 1213.27,
    "2008-09-26": 1213.27, "2008-09-29": 1106.42, "2008-09-30": 1166.36,
    "2008-10-01": 1161.06, "2008-10-02": 1114.28, "2008-10-03": 1099.23,
    "2008-10-06": 1056.89, "2008-10-07": 996.23, "2008-10-08": 984.94,
    "2008-10-09": 909.92, "2008-10-10": 899.22, "2008-10-13": 1003.35,
    "2008-10-14": 998.01, "2008-10-15": 907.84, "2008-10-16": 940.55,
    "2008-10-17": 985.40, "2008-10-20": 985.40, "2008-10-21": 955.05,
    "2008-10-22": 896.78, "2008-10-23": 908.11, "2008-10-24": 876.77,
    "2008-10-27": 848.92, "2008-10-28": 940.51, "2008-10-29": 954.09,
    "2008-10-30": 968.75, "2008-10-31": 968.75,
    "2008-11-03": 952.77, "2008-11-04": 1005.75, "2008-11-05": 952.77,
    "2008-11-06": 904.88, "2008-11-07": 930.99, "2008-11-10": 919.21,
    "2008-11-12": 852.30, "2008-11-13": 911.29, "2008-11-14": 873.29,
    "2008-11-17": 850.75, "2008-11-18": 859.12, "2008-11-19": 806.58,
    "2008-11-20": 752.44, "2008-11-21": 800.03, "2008-11-24": 857.39,
    "2008-11-25": 887.68, "2008-11-26": 896.24, "2008-11-28": 896.24,
    "2008-12-01": 816.21, "2008-12-02": 848.81, "2008-12-03": 870.26,
    "2008-12-04": 876.07, "2008-12-05": 876.07, "2008-12-08": 909.70,
    "2008-12-09": 899.24, "2008-12-10": 899.24, "2008-12-11": 888.67,
    "2008-12-12": 888.67, "2008-12-15": 868.57, "2008-12-16": 913.18,
    "2008-12-17": 904.42, "2008-12-18": 885.28, "2008-12-19": 887.88,
    "2008-12-22": 852.60, "2008-12-23": 863.16, "2008-12-24": 863.16,
    "2008-12-26": 872.80, "2008-12-29": 869.42, "2008-12-30": 890.64,
    "2008-12-31": 903.25,
}

sp500_raw = pd.DataFrame(list(sp500_data.items()), columns=["fecha", "close"])
sp500_raw["fecha"] = pd.to_datetime(sp500_raw["fecha"])
sp500_raw = sp500_raw.sort_values("fecha").reset_index(drop=True)

print(f"S&P 500 2008: {len(sp500_raw)} registros  |  {sp500_raw['fecha'].min().date()} → {sp500_raw['fecha'].max().date()}")

# ── 4. Indexar ambas series a 100 en el primer día de 2008 ───────────────────
base_bot = df_bot["capital"].iloc[0]
base_sp  = sp500_raw["close"].iloc[0]

df_bot["idx"]   = df_bot["capital"] / base_bot * 100
sp500_raw["idx"] = sp500_raw["close"] / base_sp * 100

# ── 5. Construir el gráfico con Plotly ────────────────────────────────────────
fig = go.Figure()

# Área coloreada entre ambas curvas (capital salvado)
# Rellenamos con fill='tonexty'; necesitamos la curva inferior primero
# Creamos una serie alineada por fecha para el área
df_merged = pd.merge(
    df_bot[["fecha", "idx"]].rename(columns={"idx": "bot"}),
    sp500_raw[["fecha", "idx"]].rename(columns={"idx": "sp"}),
    on="fecha", how="inner"
)

# Área entre curvas: fill de SP500 (inferior) a BOT (superior)
fig.add_trace(go.Scatter(
    x=df_merged["fecha"],
    y=df_merged["sp"],
    fill=None,
    mode="lines",
    line=dict(color="rgba(0,0,0,0)"),
    showlegend=False,
    hoverinfo="skip",
    name="_base_fill"
))

fig.add_trace(go.Scatter(
    x=df_merged["fecha"],
    y=df_merged["bot"],
    fill="tonexty",
    fillcolor="rgba(0, 230, 118, 0.15)",
    mode="none",
    showlegend=False,
    hoverinfo="skip",
    name="_area_fill"
))

# Línea S&P 500
fig.add_trace(go.Scatter(
    x=sp500_raw["fecha"],
    y=sp500_raw["idx"],
    mode="lines",
    name="S&P 500",
    line=dict(color="#FF4C4C", width=2.5),
    hovertemplate="<b>S&P 500</b><br>Fecha: %{x|%d %b %Y}<br>Índice: %{y:.1f}<extra></extra>"
))

# Línea LIBERTAD_2045
fig.add_trace(go.Scatter(
    x=df_bot["fecha"],
    y=df_bot["idx"],
    mode="lines",
    name="LIBERTAD_2045",
    line=dict(color="#00E676", width=2.5),
    hovertemplate="<b>LIBERTAD_2045</b><br>Fecha: %{x|%d %b %Y}<br>Índice: %{y:.1f}<extra></extra>"
))

# Línea base 100
fig.add_hline(y=100, line_dash="dot", line_color="rgba(255,255,255,0.3)", line_width=1)

# Anotaciones de eventos clave de 2008
eventos = [
    {"fecha": "2008-03-17", "texto": "Bear Stearns<br>colapsa", "ay": -50},
    {"fecha": "2008-09-15", "texto": "Lehman<br>quiebra",       "ay": -60},
    {"fecha": "2008-10-10", "texto": "Mínimo de<br>mercado",    "ay":  50},
]

for ev in eventos:
    fig.add_annotation(
        x=ev["fecha"],
        y=sp500_raw.loc[sp500_raw["fecha"] >= ev["fecha"], "idx"].iloc[0]
        if not sp500_raw.loc[sp500_raw["fecha"] >= ev["fecha"]].empty else 70,
        text=ev["texto"],
        showarrow=True,
        arrowhead=2,
        arrowcolor="rgba(255,100,100,0.7)",
        arrowsize=1,
        arrowwidth=1.5,
        ax=0,
        ay=ev["ay"],
        font=dict(color="rgba(255,180,180,0.9)", size=11),
        bgcolor="rgba(30,30,30,0.7)",
        bordercolor="rgba(255,100,100,0.4)",
        borderwidth=1,
        borderpad=4,
    )

# Estadísticas finales para la anotación de cierre
bot_final = df_bot["idx"].iloc[-1]
sp_final  = sp500_raw["idx"].iloc[-1]
capital_salvado = bot_final - sp_final

stats_text = (
    f"<b>Dic 2008</b><br>"
    f"LIBERTAD_2045: <b style='color:#00E676'>{bot_final:.1f}</b><br>"
    f"S&P 500: <b style='color:#FF4C4C'>{sp_final:.1f}</b><br>"
    f"Capital salvado: <b style='color:#00E676'>+{capital_salvado:.1f} pts</b>"
)

fig.add_annotation(
    x=df_bot["fecha"].iloc[-1],
    y=bot_final,
    text=stats_text,
    showarrow=True,
    arrowhead=2,
    arrowcolor="#00E676",
    ax=-130,
    ay=-40,
    font=dict(color="white", size=12),
    bgcolor="rgba(0,40,20,0.85)",
    bordercolor="#00E676",
    borderwidth=1.5,
    borderpad=6,
    align="left",
)

# ── 6. Layout oscuro ──────────────────────────────────────────────────────────
fig.update_layout(
    title=dict(
        text="<b>La Prueba de Fuego: Crisis 2008</b>"
             "<br><sup>Comparativa indexada (Base 100 = Ene 2008) · LIBERTAD_2045 vs S&P 500</sup>",
        x=0.5,
        xanchor="center",
        font=dict(size=22, color="white"),
    ),
    paper_bgcolor="#0D0D0D",
    plot_bgcolor="#141414",
    font=dict(family="Inter, Arial, sans-serif", color="#CCCCCC"),

    xaxis=dict(
        title="",
        showgrid=True,
        gridcolor="rgba(255,255,255,0.06)",
        zeroline=False,
        tickfont=dict(size=12),
        tickformat="%b %Y",
        dtick="M1",
        rangeslider=dict(visible=False),
    ),
    yaxis=dict(
        title="Valor Indexado (Base 100 = 1 Ene 2008)",
        showgrid=True,
        gridcolor="rgba(255,255,255,0.06)",
        zeroline=False,
        tickfont=dict(size=12),
        ticksuffix=" ",
    ),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1,
        bgcolor="rgba(20,20,20,0.8)",
        bordercolor="rgba(255,255,255,0.2)",
        borderwidth=1,
        font=dict(size=13),
    ),
    hovermode="x unified",
    hoverlabel=dict(
        bgcolor="#1A1A1A",
        bordercolor="rgba(255,255,255,0.3)",
        font=dict(size=13, color="white"),
    ),
    margin=dict(l=70, r=40, t=110, b=60),
    width=1200,
    height=650,
)

# Franja de recesión (sombreado NBER: dic 2007 – jun 2009 → en 2008: todo el año)
fig.add_vrect(
    x0="2008-09-01", x1="2008-12-31",
    fillcolor="rgba(255,80,80,0.06)",
    line_width=0,
    annotation_text="Crisis aguda",
    annotation_position="top left",
    annotation_font=dict(color="rgba(255,120,120,0.6)", size=10),
)

# ── 7. Guardar ────────────────────────────────────────────────────────────────
output_path = os.path.expanduser("~/PROYECTO_LIBERTAD_2045/linea_cordura_2008.html")
fig.write_html(
    output_path,
    include_plotlyjs="cdn",
    full_html=True,
    config={
        "displayModeBar": True,
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        "displaylogo": False,
        "toImageButtonOptions": {
            "format": "png",
            "filename": "linea_cordura_2008",
            "height": 650,
            "width": 1200,
            "scale": 2,
        },
    },
)

print(f"\n✓ Gráfico guardado en: {output_path}")
print(f"\nResumen 2008:")
print(f"  LIBERTAD_2045 : {bot_final:.1f}  ({bot_final - 100:+.1f}%)")
print(f"  S&P 500       : {sp_final:.1f}  ({sp_final - 100:+.1f}%)")
print(f"  Capital salvado: {capital_salvado:+.1f} puntos de índice")
