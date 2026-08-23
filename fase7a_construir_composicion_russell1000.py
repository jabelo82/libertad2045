"""
LIBERTAD_2045 -- Fase 7A de la iniciativa Exp.46/48
Parte 1: composicion historica PUNTO-EN-EL-TIEMPO real del Russell 1000
==========================================================================

Corrige el sesgo de anticipacion documentado y sin resolver del Exp.48
(seccion 7 de 00_LIBERTAD2045_CONTEXT.txt): el Exp.48 anadia TODOS los
tickers Russell-incrementales a TODAS las fechas del backtest, como si
la composicion 2026 hubiera sido igual en 2006. Aqui, cada ticker solo
es elegible desde su fecha REAL de entrada en el Russell 1000 (verificada
con un snapshot oficial de FTSE Russell), no antes -- y deja de serlo
desde la fecha real de salida, no antes tampoco.

Fuente de verdad: 6 snapshots oficiales de FTSE Russell/Russell
Investments, verificados al 100% (descargados y leidos) en la
investigacion previa a esta fase -- ver conversacion, no hay documento
separado todavia. Reconstitucion 2017 derivada por diferencia de
conjuntos Russell 3000 - Russell 2000 (misma fecha exacta, verificado);
2018-2022 son listas directas "Russell 1000(R) Index -- Membership
list" (Company + Ticker, no un subindice Value/Growth/sectorial).

Fechas de reconstitucion (confirmadas leyendo la cabecera de cada
documento, no supuestas):
    2017-06-26, 2018-06-25, 2019-07-01, 2020-06-29, 2021-06-28,
    2022-06-24.

Dependencia externa: poppler-utils (binario `pdftotext`) -- igual que
data_manager.py depende de yfinance para precios, este script depende
de pdftotext para extraer texto de los PDFs de FTSE Russell. Si no
esta instalado, falla con un mensaje explicito (ver
_verificar_pdftotext()), no con un traceback opaco.

Salidas:
    russell1000_composicion_historica_2017_2022.csv
        -- mismo formato que sp500_composicion.csv (date, tickers CSV),
        SIN deduplicar contra el S&P500 -- eso vive en el script de la
        Parte 2 (Fase 7B), no aqui, porque depende de con que indice se
        compare.
    fase7a_diagnostico_altas_bajas.csv
        -- altas/bajas reales entre cada par de snapshots consecutivos,
        con deteccion de candidatos a "cambio de ticker/fusion, no
        salida+entrada real" por similitud de nombre de empresa -- para
        revision humana, no aplica ninguna correccion automatica (ver
        razonamiento en el diseño aprobado por Javier, 23/08/2026: el
        motor resuelve elegibilidad por ticker exacto y eso ya es
        correcto tal cual, tratar los renames como continuidad no
        cambiaria el comportamiento del backtest).
"""

import os
import re
import shutil
from difflib import SequenceMatcher

import pandas as pd

SNAPSHOTS_DIR = "data/russell1000_snapshots"  # gitignored, mismo patron que data/ de precios
OUT_COMPOSICION = "russell1000_composicion_historica_2017_2022.csv"
OUT_DIAGNOSTICO = "fase7a_diagnostico_altas_bajas.csv"

# URLs exactas del Wayback Machine -- capturas ya verificadas al 100%
# (descargadas y leidas) en la investigacion previa a esta fase.
# Sufijo "id_" tras el timestamp: fuerza la captura CRUDA sin la pagina
# intermedia/toolbar de Wayback Machine -- sin esto, la descarga es no
# determinista (a veces da el PDF real, a veces un HTML "please wait" /
# timemap, verificado empiricamente durante la construccion de este
# script -- con "id_" fue 100% consistente en las pruebas).
FUENTES = {
    "2017_ru3000": "http://web.archive.org/web/20171113043246id_/http://www.ftserussell.com:80/files/support-documents/2017-ru3000-membership-list",
    "2017_ru2000": "http://web.archive.org/web/20170911071048id_/http://www.ftserussell.com:80/files/support-documents/2017-ru2000-membership-list",
    "2018": "http://web.archive.org/web/20180807160405id_/http://www.ftserussell.com:80/files/support-documents/2018-membership-list-russell-1000",
    "2019": "http://web.archive.org/web/20210624182321id_/https://content.ftserussell.com/sites/default/files/support_document/RU1000_MembershipList_20190701.pdf",
    "2020": "http://web.archive.org/web/20210617222134id_/https://content.ftserussell.com/sites/default/files/ru1000_membershiplist_20200629.pdf",
    "2021": "http://web.archive.org/web/20210625222042id_/https://content.ftserussell.com/sites/default/files/ru1000_membershiplist_20210628.pdf",
    "2022": "http://web.archive.org/web/20230619130650id_/https://content.ftserussell.com/sites/default/files/ru1000_membershiplist_20220624.pdf",
}

FECHAS_RECONSTITUCION = {
    2017: "2017-06-26",
    2018: "2018-06-25",
    2019: "2019-07-01",
    2020: "2020-06-29",
    2021: "2021-06-28",
    2022: "2022-06-24",
}

# Rango real observado del propio indice -- si un año parsea fuera de
# este rango, algo fue mal en el parsing y hay que pararse a revisar,
# no seguir en silencio.
FILAS_MIN_ESPERADAS = 900
FILAS_MAX_ESPERADAS = 1150


def _verificar_pdftotext():
    if shutil.which("pdftotext") is None:
        raise RuntimeError(
            "pdftotext no encontrado (paquete poppler-utils). Instalar con "
            "'sudo apt install poppler-utils' antes de ejecutar este script. "
            "No hay fallback en Python puro -- decision deliberada para no "
            "añadir una dependencia nueva a requirements.txt para un script "
            "de un solo uso (ver docstring)."
        )


def descargar_pdf_cached(nombre, url, intentos=4):
    """
    Usa `curl` en vez de urllib -- verificado en la investigacion previa
    que urllib.request recibe de Wayback Machine una pagina intermedia
    (HTML "please wait"/calendario) en vez del PDF crudo para estas
    URLs concretas, mientras que `curl -sL` con un User-Agent de
    navegador real sí obtiene el fichero real.

    Reintentos con espera creciente: verificado empiricamente que,
    incluso con el sufijo "id_" (captura cruda sin toolbar), Wayback
    Machine sirve intermitentemente esa misma pagina HTML intermedia en
    vez del PDF -- no es determinista, así que un solo intento no basta
    (mismo espiritu que el fix de reintentos ya aplicado en
    data_manager.py para yf.download vacío, 22/08/2026).
    """
    import subprocess
    import time

    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    ruta = os.path.join(SNAPSHOTS_DIR, f"{nombre}.pdf")
    if os.path.exists(ruta) and _es_pdf_valido(ruta):
        return ruta

    # Alterna entre la variante "id_" (captura cruda) y la variante sin
    # "id_" -- verificado empiricamente que cual de las dos funciona es
    # impredecible por documento/momento (para 2022 concretamente, solo
    # la variante SIN "id_" respondio con el PDF real en las pruebas;
    # para el resto, la variante CON "id_" fue la fiable) -- alternar
    # cubre ambos casos sin tener que fijar una sola por documento.
    url_sin_id = url.replace("id_/", "/", 1)
    variantes = [url, url_sin_id] if "id_/" in url else [url]

    for intento in range(1, intentos + 1):
        url_intento = variantes[(intento - 1) % len(variantes)]
        print(f"  Descargando {nombre} desde Wayback Machine... (intento {intento}/{intentos})")
        subprocess.run(
            ["curl", "-sL", "--max-time", "60", "-A",
             "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
             url_intento, "-o", ruta],
            check=True,
        )
        if _es_pdf_valido(ruta):
            return ruta
        print(f"    -> respuesta no es un PDF real (probable pagina intermedia de "
              f"Wayback Machine), reintentando...")
        time.sleep(intento * 2)

    raise RuntimeError(
        f"{nombre}: no se pudo obtener un PDF real tras {intentos} intentos -- "
        f"revisar {ruta} a mano (probablemente Wayback Machine sirviendo la "
        f"pagina intermedia de forma persistente, no un problema del parser)."
    )


def _es_pdf_valido(ruta):
    if not os.path.exists(ruta) or os.path.getsize(ruta) < 10_000:
        return False
    with open(ruta, "rb") as f:
        return f.read(5) == b"%PDF-"


def extraer_texto(ruta_pdf):
    import subprocess
    ruta_txt = ruta_pdf.replace(".pdf", ".txt")
    subprocess.run(["pdftotext", "-layout", ruta_pdf, ruta_txt], check=True)
    with open(ruta_txt, encoding="utf-8", errors="replace") as f:
        return f.read()


# Patron de fila de datos: "EMPRESA...   TICKER   EMPRESA...   TICKER"
# (linea completa, dos entradas) o solo la mitad izquierda (nº impar de
# entradas en la ultima fila de un documento). Company y Ticker
# separados por 2+ espacios (asi es como pdftotext -layout preserva las
# columnas del PDF original).
#
# El ticker DEBE empezar por letra (nunca digito) -- ningun ticker real
# de este universo es numerico puro; esto es ademas lo que evita que un
# pie de pagina "June 26, 2017          5" (fecha + nº de pagina) se
# cuele como fila de datos (empresa="June 26, 2017", ticker="5") --
# bug real, encontrado y corregido durante la construccion de este
# script: sin este requisito, nºs de pagina puros (1, 2, 3...) entraban
# en el CSV de composicion como si fueran tickers reales. Verificado
# tras el fix: 0 entradas puramente numericas en las 6 salidas.
_PATRON_FILA_DOBLE = re.compile(
    r'^(?P<empresa1>\S.{5,38}?)\s{2,}(?P<ticker1>[A-Z][A-Z0-9.!]{0,5})\s{2,}'
    r'(?P<empresa2>\S.{5,38}?)\s{2,}(?P<ticker2>[A-Z][A-Z0-9.!]{0,5})\s*$'
)
_PATRON_FILA_SIMPLE = re.compile(
    r'^(?P<empresa1>\S.{5,38}?)\s{2,}(?P<ticker1>[A-Z][A-Z0-9.!]{0,5})\s*$'
)

# Lineas que NUNCA son datos, aunque coincidan por casualidad con el
# patron de arriba -- cabeceras, pies legales, paginacion, y la propia
# linea de fecha del pie ("Month DD, YYYY") que origino el bug de
# arriba -- se deja como cinturon-y-tirantes ademas del fix del ticker.
_LINEA_IGNORAR = re.compile(
    r'Company\s+Ticker|Copyright|proprietary|photocopying|ftserussell\.com|'
    r'russell\.com|Page \d+ of \d+|Russell US Indexes|Membership list|'
    r'Russell (1000|2000|3000)|As of|First use|CORP-\d|This material|'
    r'advice from|not an offer|unmanaged|licence from|applicable member|'
    r'Indexes are|investment strategy|hindsight|revisions to|'
    r'^(January|February|March|April|May|June|July|August|September|'
    r'October|November|December) \d{1,2},? \d{4}',
    re.IGNORECASE,
)


def parsear_membership_list(texto):
    """
    Devuelve {ticker: nombre_empresa} a partir del texto -layout de un
    "Membership list" de FTSE Russell/Russell Investments (formato de
    dos columnas Company+Ticker, confirmado identico en los 7
    documentos fuente de esta fase durante la investigacion previa).
    """
    resultado = {}
    for linea in texto.splitlines():
        linea = linea.rstrip()
        if not linea.strip() or _LINEA_IGNORAR.search(linea):
            continue
        m = _PATRON_FILA_DOBLE.match(linea)
        if m:
            resultado[m.group("ticker1")] = m.group("empresa1").strip()
            resultado[m.group("ticker2")] = m.group("empresa2").strip()
            continue
        m = _PATRON_FILA_SIMPLE.match(linea)
        if m:
            resultado[m.group("ticker1")] = m.group("empresa1").strip()
    return resultado


def construir_snapshot_2017():
    ruta_3000 = descargar_pdf_cached("2017_ru3000", FUENTES["2017_ru3000"])
    ruta_2000 = descargar_pdf_cached("2017_ru2000", FUENTES["2017_ru2000"])
    ru3000 = parsear_membership_list(extraer_texto(ruta_3000))
    ru2000 = parsear_membership_list(extraer_texto(ruta_2000))
    tickers_1000 = set(ru3000) - set(ru2000)
    # nombres de empresa: se toman del propio ru3000 (superset)
    empresas = {t: ru3000[t] for t in tickers_1000}
    return empresas


def construir_snapshot_directo(anio):
    ruta = descargar_pdf_cached(str(anio), FUENTES[str(anio)])
    return parsear_membership_list(extraer_texto(ruta))


def main():
    _verificar_pdftotext()

    snapshots = {}  # {anio: {ticker: empresa}}

    print("Construyendo snapshot 2017 (Russell 3000 - Russell 2000)...")
    snapshots[2017] = construir_snapshot_2017()

    for anio in [2018, 2019, 2020, 2021, 2022]:
        print(f"Construyendo snapshot {anio} (lista directa Russell 1000)...")
        snapshots[anio] = construir_snapshot_directo(anio)

    # --------------------------------------------------
    # Validacion de calidad -- para en seco si algun año sale del rango
    # plausible, no lo deja pasar en silencio (ver diseño aprobado).
    # --------------------------------------------------
    print("\nValidacion de calidad del parsing:")
    for anio, empresas in snapshots.items():
        n = len(empresas)
        estado = "OK" if FILAS_MIN_ESPERADAS <= n <= FILAS_MAX_ESPERADAS else "*** FUERA DE RANGO ***"
        print(f"  {anio}: {n} tickers  [{estado}]")
        if not (FILAS_MIN_ESPERADAS <= n <= FILAS_MAX_ESPERADAS):
            raise RuntimeError(
                f"{anio}: {n} tickers parseados, fuera del rango plausible "
                f"[{FILAS_MIN_ESPERADAS}, {FILAS_MAX_ESPERADAS}] -- revisar "
                f"el parsing antes de continuar (no generar el CSV con datos "
                f"sospechosos)."
            )

    # --------------------------------------------------
    # Fichero de composicion (crudo, sin deduplicar contra el S&P500 --
    # eso vive en el script de la Parte 2/Fase 7B, no aqui).
    # --------------------------------------------------
    filas = []
    for anio in sorted(snapshots):
        tickers_csv = ",".join(sorted(snapshots[anio]))
        filas.append({"date": FECHAS_RECONSTITUCION[anio], "tickers": tickers_csv})
    comp_df = pd.DataFrame(filas).set_index("date")
    comp_df.to_csv(OUT_COMPOSICION)
    print(f"\nGuardado: {OUT_COMPOSICION} ({len(comp_df)} filas)")

    # --------------------------------------------------
    # Diagnostico altas/bajas entre snapshots consecutivos, con
    # deteccion de candidatos a cambio de ticker/fusion (no corregido
    # automaticamente -- solo señalado para revision humana).
    # --------------------------------------------------
    diagnostico = []
    anios_ordenados = sorted(snapshots)
    for i in range(1, len(anios_ordenados)):
        anio_prev, anio_curr = anios_ordenados[i - 1], anios_ordenados[i]
        prev, curr = snapshots[anio_prev], snapshots[anio_curr]
        bajas = set(prev) - set(curr)
        altas = set(curr) - set(prev)

        candidatos_rename = set()
        for t_baja in bajas:
            nombre_baja = prev[t_baja]
            for t_alta in altas:
                nombre_alta = curr[t_alta]
                similitud = SequenceMatcher(None, nombre_baja, nombre_alta).ratio()
                # Umbral alto deliberado (0.85, no 0.72 como en un primer
                # intento): con 0.72 aparecian falsos positivos entre
                # empresas DISTINTAS que solo comparten una palabra
                # generica de sector (ej. "JUNO THERAPEUTICS" vs "SAGE
                # THERAPEUTICS", similitud 0.81, sin relacion real) --
                # verificado a mano revisando la primera salida del
                # diagnostico. 0.85 sigue capturando renames reales
                # verificados (ej. CBRE GROUP INC, ticker CBG->CBRE,
                # similitud 1.000) sin ese ruido.
                if similitud >= 0.85:
                    candidatos_rename.add((t_baja, t_alta))
                    diagnostico.append({
                        "de_anio": anio_prev, "a_anio": anio_curr,
                        "tipo": "POSIBLE_RENAME_O_FUSION",
                        "ticker_baja": t_baja, "empresa_baja": nombre_baja,
                        "ticker_alta": t_alta, "empresa_alta": nombre_alta,
                        "similitud_nombre": round(similitud, 3),
                    })

        tickers_en_rename = {t for par in candidatos_rename for t in par}
        for t in sorted(bajas - tickers_en_rename):
            diagnostico.append({
                "de_anio": anio_prev, "a_anio": anio_curr, "tipo": "BAJA",
                "ticker_baja": t, "empresa_baja": prev[t],
                "ticker_alta": "", "empresa_alta": "", "similitud_nombre": "",
            })
        for t in sorted(altas - tickers_en_rename):
            diagnostico.append({
                "de_anio": anio_prev, "a_anio": anio_curr, "tipo": "ALTA",
                "ticker_baja": "", "empresa_baja": "",
                "ticker_alta": t, "empresa_alta": curr[t], "similitud_nombre": "",
            })

        print(f"  {anio_prev}->{anio_curr}: {len(bajas)} bajas, {len(altas)} altas, "
              f"{len(candidatos_rename)} posibles renames/fusiones")

    pd.DataFrame(diagnostico).to_csv(OUT_DIAGNOSTICO, index=False)
    print(f"\nGuardado: {OUT_DIAGNOSTICO} ({len(diagnostico)} filas)")


if __name__ == "__main__":
    main()
