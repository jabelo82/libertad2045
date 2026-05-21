"""
LIBERTAD_2045 — GitHub Publisher
==================================
Publica el dashboard actualizado en GitHub Pages después
de cada ciclo nocturno del bot.
"""

import base64
import os
import json
from datetime import datetime
from pathlib import Path

import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO  = os.getenv("GITHUB_REPO", "jabelo82/libertad2045")
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

        sha = None
        r = requests.get(GITHUB_API, headers=headers, timeout=15)
        if r.status_code == 200:
            sha = r.json().get("sha")
        elif r.status_code != 404:
            return False, f"Error obteniendo SHA: {r.status_code}"

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        payload = {"message": f"Dashboard actualizado {timestamp}", "content": contenido_b64}
        if sha:
            payload["sha"] = sha

        r = requests.put(GITHUB_API, headers=headers, json=payload, timeout=30)

        if r.status_code in (200, 201):
            return True, f"Dashboard publicado ({timestamp})"
        else:
            return False, f"Error GitHub: {r.status_code} {r.text[:100]}"

    except Exception as e:
        return False, f"Error: {e}"
