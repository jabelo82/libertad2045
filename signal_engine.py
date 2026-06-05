import pandas as pd

from logger import log_event


def detectar_senal(df):
    """
    Detecta señales de entrada basadas en tendencia + pullback + recuperación.

    Condiciones necesarias (todas deben cumplirse):

        1. TENDENCIA PRINCIPAL
           SMA50 > SMA200 y la SMA200 es creciente respecto al día anterior.
           → Confirma que el activo está en tendencia alcista de largo plazo.

        2. PULLBACK REAL
           El cierre del día anterior cayó al menos un 2% por debajo de la SMA50.
           → Confirma que hubo una corrección significativa, no solo ruido.

        3. RECUPERACIÓN
           El cierre de hoy vuelve a superar la SMA50.
           → Confirma que el precio ha retomado la tendencia tras el pullback.

    Nota: el filtro de confirmación de volumen está desactivado.
    Validado en el experimento 10 — sin filtro de volumen los resultados
    son superiores en el universo S&P500 (251 acciones).

    Retorna True si la señal es válida, False en caso contrario.
    """

    if df is None or len(df) < 4:
        return False

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # --------------------------------------------------
    # Verificar que los indicadores necesarios están disponibles
    # --------------------------------------------------

    required = ["close", "SMA50", "SMA200"]

    for col in required:
        if col not in df.columns:
            return False

    for val in [last.close, last.SMA50, last.SMA200,
                prev.close, prev.SMA50, prev.SMA200]:
        if pd.isna(val):
            return False

    if pd.isna(last["ATR"]) or last["ATR"] <= 0:
        return False


    # --------------------------------------------------
    # 1. Tendencia principal
    # --------------------------------------------------

    tendencia = (
        last.SMA50  > last.SMA200 and
        last.SMA200 > prev.SMA200
    )

    if not tendencia:
        return False


    # --------------------------------------------------
    # 2+5. Pullback real — ventana 3 días + umbral ATR-adaptativo
    #
    # Mejora 2: se amplía la detección a los 3 días anteriores.
    # Un pullback que duró varios días es igual de válido que uno de 1 día.
    #
    # Mejora 5: el umbral deja de ser un 2% fijo y pasa a ser proporcional
    # al ATR del activo — exige más corrección en activos volátiles y menos
    # en activos estables. Umbral: SMA50 - 0.75 × ATR
    # --------------------------------------------------

    if len(df) < 5:
        return False

    pullback = False
    for j in range(-4, -1):  # días: -4, -3, -2 (3 días antes de hoy)
        row = df.iloc[j]
        if (pd.isna(row["close"]) or pd.isna(row["SMA50"]) or
                pd.isna(row["ATR"]) or row["ATR"] <= 0):
            continue
        if row["close"] < row["SMA50"] - row["ATR"] * 0.75:
            pullback = True
            break

    if not pullback:
        return False


    # --------------------------------------------------
    # 3. Recuperación sobre SMA50
    # --------------------------------------------------

    recuperacion = (
        last.close > last.SMA50
    )

    if not recuperacion:
        return False


    # --------------------------------------------------
    # Señal válida — registrar valores para trazabilidad
    # --------------------------------------------------

    log_event("INFO",
              f"Señal detectada | "
              f"close={last.close:.2f} | "
              f"SMA50={last.SMA50:.2f} | "
              f"SMA200={last.SMA200:.2f} | "
              f"ATR={last.ATR:.4f} | "
              f"ATR_percentil={last.get('ATR_PERCENTIL', float('nan')):.2f} | "
              f"pullback_umbral={last.SMA50 - last.ATR * 0.75:.2f}")

    return True
