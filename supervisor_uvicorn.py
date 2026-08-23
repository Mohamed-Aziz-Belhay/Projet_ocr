"""
supervisor_uvicorn.py

Lance uvicorn en sous-processus, compte les requêtes POST /extract
terminées en lisant sa sortie en direct, et le relance automatiquement
dès que le seuil MAX_REQUESTS est atteint -- pour contourner la fuite
mémoire déjà diagnostiquée (probablement interne à un moteur OCR),
sans dépendre de Gunicorn (indisponible nativement sous Windows).

Ne modifie AUCUN fichier de votre application -- entièrement externe.

IMPORTANT : lancez ce script AU LIEU DE `uvicorn app.main:app --reload`,
pas en plus. Retirez --reload pendant que vous utilisez ce superviseur
(il gère lui-même le redémarrage, --reload gênerait ce contrôle).

Utilisation :
    python supervisor_uvicorn.py

    Puis, dans un AUTRE terminal, lancez vos scripts de campagne comme
    d'habitude (run_passport_campaign_reste.py, etc.) -- ils continuent
    à taper sur http://localhost:8000 normalement, sans rien changer
    de leur côté.

    Ctrl+C pour arrêter complètement le superviseur (et le serveur).
"""
from __future__ import annotations

import re
import subprocess
import sys
import time

# ============================================================
#  CONFIGURATION
# ============================================================

# Seuil observé avant panne (2 requêtes /extract réussies, puis échec
# sur la 3e). Redémarrer à ce seuil évite d'atteindre la panne.
# Vous pouvez descendre à 1 pour plus de sécurité, au prix d'un
# redémarrage (et donc d'un rechargement des modèles) plus fréquent.
MAX_REQUESTS = 2

# Commande de lancement d'uvicorn, SANS --reload (cf. note ci-dessus).
UVICORN_CMD = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Détecte toute requête /extract terminée (succès ou échec), peu
# importe le code de statut -- toutes consomment potentiellement des
# ressources, pas seulement les succès.
EXTRACT_DONE_PATTERN = re.compile(r'"POST /extract(?:/async)? HTTP/1\.1"\s+\d+')

# Pause après extinction, pour laisser le port 8000 se libérer avant
# de relancer un nouveau processus dessus.
RESTART_DELAY_S = 2

# ============================================================


def run_once() -> None:
    """Lance uvicorn, relaie sa sortie, compte les requêtes /extract,
    et retourne dès que le seuil est atteint (ou si le processus
    s'arrête tout seul, ex. plantage)."""

    import os

    # Force l'UTF-8 pour le sous-processus : sans ça, sur Windows, la
    # sortie redirigée via un tube (PIPE) retombe sur l'encodage cp1252
    # par défaut, qui ne sait pas écrire les emojis (ex. "✅") déjà
    # présents dans les logs de l'application -- provoquant une erreur
    # de logging (non bloquante en soi, mais bruyante) à chaque ligne
    # concernée.
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"

    print(f"[SUPERVISEUR] Démarrage : {' '.join(UVICORN_CMD)}")
    proc = subprocess.Popen(
        UVICORN_CMD,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=child_env,
    )

    count = 0

    try:
        for line in proc.stdout:
            print(line, end="")  # relaie les logs normaux du serveur

            if EXTRACT_DONE_PATTERN.search(line):
                count += 1
                print(f"[SUPERVISEUR] Requête /extract terminée : {count}/{MAX_REQUESTS}")

                if count >= MAX_REQUESTS:
                    print(f"[SUPERVISEUR] Seuil atteint -- redémarrage préventif du serveur...")
                    break

    except KeyboardInterrupt:
        print("\n[SUPERVISEUR] Arrêt demandé (Ctrl+C).")
        _terminate(proc)
        raise

    _terminate(proc)


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return  # déjà arrêté (ex. plantage détecté)

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        print("[SUPERVISEUR] Le serveur ne répond pas à l'arrêt normal, forçage...")
        proc.kill()
        proc.wait()


def main() -> None:
    print("=" * 60)
    print(f"Superviseur uvicorn -- redémarrage automatique tous les {MAX_REQUESTS} appels /extract")
    print("Ctrl+C pour tout arrêter.")
    print("=" * 60 + "\n")

    try:
        while True:
            run_once()
            print(f"[SUPERVISEUR] Pause de {RESTART_DELAY_S}s avant relance...\n")
            time.sleep(RESTART_DELAY_S)
    except KeyboardInterrupt:
        print("[SUPERVISEUR] Terminé proprement.")


if __name__ == "__main__":
    main()