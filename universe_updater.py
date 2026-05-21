#!/usr/bin/env python3
"""
universe_updater.py — Actualiza universe_sp500.py con la composición real del S&P 500.

Fuente    : Wikipedia (requests + pandas.read_html) — sin API key
Validación: yfinance — mínimo MIN_HISTORY_BARS días de histórico por ticker nuevo
Fallback  : si el scraping falla, el universo actual no se toca

Programado: lunes 21:00 (un ciclo antes del bot)
"""

import importlib.util
import io
import os
import py_compile
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

PROJECT_DIR   = Path(__file__).resolve().parent
UNIVERSE_FILE = PROJECT_DIR / "universe_sp500.py"
WIKI_URL      = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
MIN_HISTORY_BARS = 200
MIN_TICKERS_SANITY = 400   # abortar si Wikipedia devuelve una lista muy corta

# GICS Sector → nombre en español para los comentarios del archivo generado
SECTOR_NOMBRES = {
    "Information Technology":   "Tecnología",
    "Communication Services":   "Comunicaciones",
    "Consumer Discretionary":   "Consumo discrecional",
    "Consumer Staples":         "Consumo básico",
    "Health Care":              "Salud",
    "Financials":               "Financiero",
    "Industrials":              "Industrial",
    "Energy":                   "Energía",
    "Materials":                "Materiales",
    "Utilities":                "Utilities",
    "Real Estate":              "Inmobiliario",
}

SECTOR_ORDEN = list(SECTOR_NOMBRES.keys())


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

def _log(level: str, msg: str):
    print(f"[universe_updater] {msg}")
    try:
        sys.path.insert(0, str(PROJECT_DIR))
        from logger import log_event
        log_event(level, f"UNIVERSE_UPDATER | {msg}")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# FORMATO DE TICKERS
# ─────────────────────────────────────────────────────────────────────────────

def _ib(ticker_wiki: str) -> str:
    """BRK.B → BRK B  (IB usa espacio en lugar de punto para clase-B)"""
    return ticker_wiki.replace(".", " ")


def _yf(ticker_wiki: str) -> str:
    """BRK.B → BRK-B  (yfinance usa guión para clase-B)"""
    return ticker_wiki.replace(".", "-")


# ─────────────────────────────────────────────────────────────────────────────
# SCRAPING WIKIPEDIA
# ─────────────────────────────────────────────────────────────────────────────

def obtener_sp500_wikipedia() -> dict | None:
    """
    Descarga la tabla del S&P500 de Wikipedia.
    Retorna {ticker_ib: (sector_gics, ticker_wiki)} o None si falla.
    """
    try:
        import requests
        import pandas as pd
    except ImportError as e:
        _log("ERROR", f"Dependencia no disponible: {e}")
        return None

    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; LIBERTAD2045-updater/1.0)"}
        resp = requests.get(WIKI_URL, headers=headers, timeout=30)
        resp.raise_for_status()

        tables = pd.read_html(io.StringIO(resp.text))
        df = tables[0]

        if "Symbol" not in df.columns or "GICS Sector" not in df.columns:
            _log("ERROR", f"Columnas inesperadas en Wikipedia: {list(df.columns)}")
            return None

        resultado: dict[str, tuple[str, str]] = {}
        for _, row in df.iterrows():
            t_wiki = str(row["Symbol"]).strip()
            sector  = str(row["GICS Sector"]).strip()
            if t_wiki and sector:
                resultado[_ib(t_wiki)] = (sector, t_wiki)

        n = len(resultado)
        if n < MIN_TICKERS_SANITY:
            _log("ERROR", f"Wikipedia devolvió solo {n} tickers — posible scraping parcial")
            return None

        _log("INFO", f"Wikipedia: {n} tickers en el índice")
        return resultado

    except Exception as e:
        _log("ERROR", f"Error scraping Wikipedia: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# VALIDACIÓN DE HISTÓRICO
# ─────────────────────────────────────────────────────────────────────────────

def tiene_historia_suficiente(ticker_wiki: str) -> bool:
    """
    Devuelve True si el ticker tiene >= MIN_HISTORY_BARS días de cierre disponibles.
    Descarga hasta 2 años de datos para cubrir el requisito de SMA200.
    """
    try:
        import yfinance as yf
        data = yf.download(
            _yf(ticker_wiki), period="2y",
            auto_adjust=True, progress=False,
        )
        return len(data) >= MIN_HISTORY_BARS
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# LECTURA DEL UNIVERSO ACTUAL
# ─────────────────────────────────────────────────────────────────────────────

def leer_universo_actual() -> set:
    """Carga el SP500 actual desde universe_sp500.py sin usar el caché de módulos."""
    try:
        spec = importlib.util.spec_from_file_location("universe_sp500", UNIVERSE_FILE)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return set(mod.SP500)
    except Exception as e:
        _log("ERROR", f"No se pudo leer el universo actual: {e}")
        return set()


# ─────────────────────────────────────────────────────────────────────────────
# GENERACIÓN DEL NUEVO BLOQUE SP500
# ─────────────────────────────────────────────────────────────────────────────

def _generar_bloque_sp500(tickers_por_sector: dict) -> str:
    """Produce el texto Python del bloque SP500 = [...] organizado por sector GICS."""
    lines = ["SP500 = [", ""]
    for sector_gics in SECTOR_ORDEN:
        tickers = sorted(tickers_por_sector.get(sector_gics, []))
        if not tickers:
            continue
        nombre_es = SECTOR_NOMBRES.get(sector_gics, sector_gics)
        lines.append(f"    # {nombre_es}")
        fila: list[str] = []
        for t in tickers:
            fila.append(f'"{t}"')
            if len(fila) == 5:
                lines.append("    " + ",  ".join(fila) + ",")
                fila = []
        if fila:
            lines.append("    " + ",  ".join(fila) + ",")
        lines.append("")
    lines.append("]")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# ESCRITURA SEGURA DEL ARCHIVO
# ─────────────────────────────────────────────────────────────────────────────

def _escribir_archivo(nuevo_content: str) -> bool:
    """
    Escribe en un archivo temporal, valida la sintaxis Python y luego
    reemplaza universe_sp500.py de forma atómica.
    Retorna True si tuvo éxito.
    """
    tmp = UNIVERSE_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(nuevo_content, encoding="utf-8")
        # Verificar que el Python generado es válido antes de reemplazar
        py_compile.compile(str(tmp), doraise=True)
        shutil.move(str(tmp), str(UNIVERSE_FILE))
        return True
    except py_compile.PyCompileError as e:
        _log("ERROR", f"Sintaxis inválida en el archivo generado — universo NO modificado: {e}")
        tmp.unlink(missing_ok=True)
        return False
    except Exception as e:
        _log("ERROR", f"Error escribiendo universe_sp500.py: {e}")
        tmp.unlink(missing_ok=True)
        return False


def actualizar_archivo(tickers_por_sector: dict, n_total: int, fecha: str) -> bool:
    """
    Reescribe universe_sp500.py preservando el header, validar_universo y __main__.
    Solo cambia el bloque SP500 = [...] y los metadatos del comentario de cabecera.
    """
    content = UNIVERSE_FILE.read_text(encoding="utf-8")

    nuevo_bloque  = _generar_bloque_sp500(tickers_por_sector)
    nuevo_content = re.sub(r"SP500 = \[.*?\]", nuevo_bloque, content, flags=re.DOTALL)

    nuevo_content = re.sub(
        r"# Última revisión: \d{4}-\d{2}-\d{2}",
        f"# Última revisión: {fecha}",
        nuevo_content,
    )
    nuevo_content = re.sub(
        r"# ~\d+ empresas",
        f"# ~{n_total} empresas",
        nuevo_content,
    )

    return _escribir_archivo(nuevo_content)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def actualizar_universo(dry_run: bool = False) -> dict:
    """
    Actualiza universe_sp500.py con la composición actual del S&P 500.

    Flujo:
        1. Scraping Wikipedia → lista autorizada de tickers
        2. Diff con el universo actual
        3. Validar historia yfinance solo para tickers nuevos
        4. Construir nuevo universo = (actual − eliminados) + añadidos_validados
        5. Reescribir universe_sp500.py de forma atómica (dry_run lo omite)

    Retorna dict: añadidos, eliminados, sin_historia, total, ok
    """
    resultado = {
        "añadidos":     [],
        "eliminados":   [],
        "sin_historia": [],
        "total":        0,
        "ok":           False,
    }

    _log("INFO", f"Inicio{'  [DRY Run]' if dry_run else ''}")

    # ── 1. Obtener composición de Wikipedia ───────────────────────────────────
    wiki = obtener_sp500_wikipedia()
    if wiki is None:
        _log("ERROR", "Scraping fallido — universo no modificado")
        return resultado

    # ── 2. Universo actual ────────────────────────────────────────────────────
    universo_actual = leer_universo_actual()
    if not universo_actual:
        _log("ERROR", "No se pudo leer el universo actual — abortando")
        return resultado

    tickers_wiki = set(wiki.keys())

    nuevos     = tickers_wiki - universo_actual   # candidatos a añadir
    eliminados = universo_actual - tickers_wiki   # ya no están en el índice

    _log("INFO", f"Candidatos a añadir: {len(nuevos)}  |  A eliminar: {len(eliminados)}")

    if eliminados:
        _log("INFO", f"Salidas del índice: {', '.join(sorted(eliminados))}")

    # ── 3. Validar historia de tickers nuevos ─────────────────────────────────
    añadidos_validados: list[str] = []
    sin_historia:       list[str] = []

    if nuevos:
        _log("INFO", f"Validando {len(nuevos)} tickers contra yfinance…")
        for ticker_ib in sorted(nuevos):
            _, ticker_wiki = wiki[ticker_ib]
            if tiene_historia_suficiente(ticker_wiki):
                añadidos_validados.append(ticker_ib)
                _log("INFO", f"  ✓ {ticker_ib}")
            else:
                sin_historia.append(ticker_ib)
                _log("WARN", f"  ✗ {ticker_ib} — historia insuficiente (< {MIN_HISTORY_BARS} barras)")
            time.sleep(0.5)  # cortesía hacia yfinance

    resultado["añadidos"]     = sorted(añadidos_validados)
    resultado["eliminados"]   = sorted(eliminados)
    resultado["sin_historia"] = sorted(sin_historia)

    # ── 4. Construir nuevo universo ───────────────────────────────────────────
    nuevo_universo = (universo_actual - eliminados) | set(añadidos_validados)

    # Organizar por sector GICS
    tickers_por_sector: dict[str, list[str]] = {s: [] for s in SECTOR_ORDEN}
    sin_sector: list[str] = []

    for ticker_ib in nuevo_universo:
        if ticker_ib in wiki:
            sector_gics, _ = wiki[ticker_ib]
            if sector_gics in tickers_por_sector:
                tickers_por_sector[sector_gics].append(ticker_ib)
            else:
                sin_sector.append(ticker_ib)
                _log("WARN", f"Sector GICS desconocido '{sector_gics}' para {ticker_ib}")

    n_total = sum(len(v) for v in tickers_por_sector.values())
    resultado["total"] = n_total

    # ── 5. Aplicar o mostrar cambios ──────────────────────────────────────────
    if not añadidos_validados and not eliminados:
        _log("INFO", f"Sin cambios — universo ya actualizado ({n_total} tickers)")
        resultado["ok"] = True
        return resultado

    if dry_run:
        _log("INFO", f"[DRY RUN] Añadir    : {resultado['añadidos']}")
        _log("INFO", f"[DRY RUN] Eliminar  : {resultado['eliminados']}")
        _log("INFO", f"[DRY RUN] Sin hist. : {resultado['sin_historia']}")
        _log("INFO", f"[DRY RUN] Total resultante: {n_total} tickers")
        resultado["ok"] = True
        return resultado

    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    ok = actualizar_archivo(tickers_por_sector, n_total, fecha_hoy)

    if not ok:
        return resultado

    _log("INFO",
         f"Universo actualizado: {n_total} tickers  "
         f"(+{len(añadidos_validados)} / -{len(eliminados)})")

    resultado["ok"] = True
    return resultado


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Actualiza universe_sp500.py con la composición actual del S&P 500",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Mostrar cambios sin modificar el archivo",
    )
    args = parser.parse_args()

    res = actualizar_universo(dry_run=args.dry_run)
    sys.exit(0 if res["ok"] else 1)
