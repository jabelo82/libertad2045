"""
LIBERTAD_2045 — GitHub Publisher
==================================
Publica el dashboard actualizado en GitHub Pages después
de cada ciclo nocturno del bot.
"""

import base64
import os
import json
import time
from datetime import datetime
from pathlib import Path

import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO  = os.getenv("GITHUB_REPO", "jabelo82/libertad2045")

MAX_RETRIES  = 3
RETRY_DELAY  = 5
GITHUB_FILE  = "index.html"
GITHUB_API   = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"

PROJECT_DIR    = Path(os.getenv("PROJECT_DIR", "/home/jabelo/PROYECTO_LIBERTAD_2045"))
DASHBOARD_FILE = PROJECT_DIR / "dashboard.html"


def publicar_dashboard():
    if not GITHUB_TOKEN:
        return False, "GITHUB_TOKEN no configurado"

    if not DASHBOARD_FILE.exists():
        return False, f"dashboard.html no encontrado"

    try:
        contenido    = DASHBOARD_FILE.read_bytes()
        contenido_b64 = base64.b64encode(contenido).decode("utf-8")

        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept":        "application/vnd.github.v3+json",
            "Content-Type":  "application/json",
        }

        ultimo_error = ""
        for intento in range(1, MAX_RETRIES + 1):
            try:
                sha = None
                r = requests.get(GITHUB_API, headers=headers, timeout=15)
                if r.status_code == 200:
                    sha = r.json().get("sha")
                elif r.status_code != 404:
                    ultimo_error = f"Error obteniendo SHA: {r.status_code}"
                    if intento < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
                    continue

                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                payload = {"message": f"Dashboard actualizado {timestamp}", "content": contenido_b64}
                if sha:
                    payload["sha"] = sha

                r = requests.put(GITHUB_API, headers=headers, json=payload, timeout=30)

                if r.status_code in (200, 201):
                    return True, f"Dashboard publicado ({timestamp})"
                else:
                    ultimo_error = f"Error GitHub: {r.status_code}"
                    if intento < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
            except Exception as e:
                ultimo_error = f"Error: {e}"
                if intento < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)

        return False, ultimo_error

    except Exception as e:
        return False, f"Error: {e}"


def publicar_pagina(nombre_archivo):
    """
    Sube un archivo HTML del proyecto a GitHub Pages conservando su nombre original.

    El archivo queda accesible en:
        https://jabelo82.github.io/libertad2045/<nombre_archivo>

    Parámetros:
        nombre_archivo : str — nombre del archivo en PROJECT_DIR, p.ej. "exp40_dashboard.html"

    Retorna (ok: bool, mensaje: str).
    """
    if not GITHUB_TOKEN:
        return False, "GITHUB_TOKEN no configurado"

    ruta_local = PROJECT_DIR / nombre_archivo
    if not ruta_local.exists():
        return False, f"{nombre_archivo} no encontrado en {PROJECT_DIR}"

    try:
        contenido_b64 = base64.b64encode(ruta_local.read_bytes()).decode("utf-8")

        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept":        "application/vnd.github.v3+json",
            "Content-Type":  "application/json",
        }

        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{nombre_archivo}"

        ultimo_error = ""
        for intento in range(1, MAX_RETRIES + 1):
            try:
                sha = None
                r = requests.get(api_url, headers=headers, timeout=15)
                if r.status_code == 200:
                    sha = r.json().get("sha")
                elif r.status_code != 404:
                    ultimo_error = f"Error obteniendo SHA: {r.status_code}"
                    if intento < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
                    continue

                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                payload   = {"message": f"Publicar {nombre_archivo} {timestamp}", "content": contenido_b64}
                if sha:
                    payload["sha"] = sha

                r = requests.put(api_url, headers=headers, json=payload, timeout=30)

                if r.status_code in (200, 201):
                    url = f"https://jabelo82.github.io/libertad2045/{nombre_archivo}"
                    return True, f"Publicado: {url} ({timestamp})"
                else:
                    ultimo_error = f"Error GitHub: {r.status_code}"
                    if intento < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
            except Exception as e:
                ultimo_error = f"Error: {e}"
                if intento < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)

        return False, ultimo_error

    except Exception as e:
        return False, f"Error: {e}"


if __name__ == "__main__":
    ok, msg = publicar_dashboard()
    estado = "✓" if ok else "✗"
    print(f"  {estado} {msg}")
    paginas = ["exp40_dashboard.html", "montecarlo_075.html"]
    for pagina in paginas:
        ok, msg = publicar_pagina(pagina)
        estado = "✓" if ok else "✗"
        print(f"  {estado} {msg}")
