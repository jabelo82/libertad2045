import pandas as pd

from logger import log_event


def detectar_senal(df):
    """
    Detecta señales de entrada basadas en tendencia + pullback + recuperación.

    Condiciones necesarias (todas deben cumplirse):

        1. TENDENCIA PRINCIPAL
           El precio cierra por encima de la SMA200
           y la SMA200 es creciente respecto al día anterior.
           → Confirma que el activo está en tendencia alcista de largo plazo.

        2. PULLBACK REAL
           El cierre del día anterior cayó al menos un 2% por debajo de la SMA50.
           → Confirma que hubo una corrección significativa, no solo ruido.

        3. RECUPERACIÓN
           El cierre de hoy vuelve a superar la SMA50.
           → Confirma que el precio ha retomado la tendencia tras el pullback.

        4. CONFIRMACIÓN DE VOLUMEN
           El volumen del día de recuperación supera la media de volumen de 20 días.
           → Filtra recuperaciones sin convicción (falsas rupturas de bajo volumen).

    Retorna True si la señal es válida, False en caso contrario.
    """

    if df is None or len(df) < 2:
        return False

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # --------------------------------------------------
    # Verificar que los indicadores necesarios están disponibles
    # --------------------------------------------------

    required = ["close", "SMA50", "SMA200", "volume"]

    for col in required:
        if col not in df.columns:
            return False

    for val in [last.close, last.SMA50, last.SMA200,
                prev.close, prev.SMA50, prev.SMA200]:
        if pd.isna(val):
            return False


    # --------------------------------------------------
    # 1. Tendencia principal
    # --------------------------------------------------

    tendencia = (
        last.close  > last.SMA200 and
        last.SMA200 > prev.SMA200
    )

    if not tendencia:
        return False


    # --------------------------------------------------
    # 2. Pullback real
    # --------------------------------------------------

    pullback = (
        prev.close < prev.SMA50 * 0.98
    )

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
    # 4. Confirmación de volumen
    # --------------------------------------------------

    vol_valido = False

    if "volume" in df.columns and not pd.isna(last.volume):

        vol_media_20 = df["volume"].rolling(20).mean().iloc[-1]

        if not pd.isna(vol_media_20) and vol_media_20 > 0:
            vol_valido = last.volume > vol_media_20

    # Si no hay datos de volumen disponibles, no bloquear la señal
    # pero registrar la advertencia
    if not vol_valido:
        if "volume" in df.columns:
            log_event("WARN", "Señal detectada sin confirmación de volumen",
                      symbol=str(df.get("symbol", "")))
        return False


    # --------------------------------------------------
    # Señal válida — registrar valores para trazabilidad
    # --------------------------------------------------

    log_event("INFO",
              f"Señal detectada | "
              f"close={last.close:.2f} | "
              f"SMA50={last.SMA50:.2f} | "
              f"SMA200={last.SMA200:.2f} | "
              f"vol={last.volume:.0f} | "
              f"vol_media20={vol_media_20:.0f}")

    return True