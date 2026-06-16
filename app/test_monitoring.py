"""
test_monitoring.py — Script de test complet Prometheus + Grafana + métriques OCR
Lancer depuis la racine du projet : python test_monitoring.py
"""
import httpx
import json
import time
import sys

BASE = "http://localhost:8000"
PROMETHEUS = "http://localhost:9090"
GRAFANA = "http://localhost:3000"

# ─── Couleurs terminal ────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):    print(f"  {GREEN}✅ {msg}{RESET}")
def fail(msg):  print(f"  {RED}❌ {msg}{RESET}")
def info(msg):  print(f"  {BLUE}ℹ  {msg}{RESET}")
def warn(msg):  print(f"  {YELLOW}⚠  {msg}{RESET}")
def header(msg):print(f"\n{BOLD}{msg}{RESET}")

results = {"passed": 0, "failed": 0}

def check(condition, msg_ok, msg_fail):
    if condition:
        ok(msg_ok)
        results["passed"] += 1
    else:
        fail(msg_fail)
        results["failed"] += 1
    return condition


# ══════════════════════════════════════════════════════════════════════════════
# 1. VÉRIFICATION DES SERVICES
# ══════════════════════════════════════════════════════════════════════════════
header("1. SERVICES EN LIGNE")

# Backend FastAPI
try:
    r = httpx.get(f"{BASE}/health", timeout=5)
    check(r.status_code == 200, f"Backend FastAPI UP  (status {r.status_code})", "Backend FastAPI DOWN")
except Exception as e:
    fail(f"Backend inaccessible : {e}")
    results["failed"] += 1

# Endpoint /metrics
try:
    r = httpx.get(f"{BASE}/metrics", timeout=5)
    has_metrics = "python_gc_objects_collected_total" in r.text
    check(r.status_code == 200 and has_metrics,
          "/metrics expose bien les métriques Prometheus",
          "/metrics inaccessible ou vide")
    if has_metrics:
        lines = [l for l in r.text.split("\n") if l and not l.startswith("#")]
        info(f"{len(lines)} métriques exposées")
except Exception as e:
    fail(f"/metrics inaccessible : {e}")
    results["failed"] += 1

# Prometheus
try:
    r = httpx.get(f"{PROMETHEUS}/-/healthy", timeout=5)
    check(r.status_code == 200, "Prometheus UP", "Prometheus DOWN")
except Exception as e:
    fail(f"Prometheus inaccessible : {e}")
    results["failed"] += 1

# Prometheus scrape le backend
try:
    r = httpx.get(f"{PROMETHEUS}/api/v1/targets", timeout=5)
    data = r.json()
    targets = data.get("data", {}).get("activeTargets", [])
    backend_target = next((t for t in targets if "8000" in t.get("scrapeUrl", "")), None)
    if backend_target:
        state = backend_target.get("health", "unknown")
        check(state == "up",
              f"Prometheus scrape backend — état : {state}",
              f"Prometheus ne scrape pas le backend (état: {state})")
        last_scrape = backend_target.get("lastScrape", "?")
        info(f"Dernier scrape : {last_scrape[:19] if last_scrape != '?' else '?'}")
    else:
        fail("Aucun target backend trouvé dans Prometheus — vérifier prometheus.yml")
        results["failed"] += 1
except Exception as e:
    fail(f"Impossible de vérifier les targets Prometheus : {e}")
    results["failed"] += 1

# Grafana
try:
    r = httpx.get(f"{GRAFANA}/api/health", timeout=5)
    check(r.status_code == 200, "Grafana UP", "Grafana DOWN")
except Exception as e:
    fail(f"Grafana inaccessible : {e}")
    results["failed"] += 1


# ══════════════════════════════════════════════════════════════════════════════
# 2. MÉTRIQUES PYTHON DÉJÀ PRÉSENTES
# ══════════════════════════════════════════════════════════════════════════════
header("2. MÉTRIQUES PYTHON (déjà actives)")

try:
    r = httpx.get(f"{PROMETHEUS}/api/v1/query?query=python_gc_objects_collected_total", timeout=5)
    data = r.json()
    results_prom = data.get("data", {}).get("result", [])
    check(len(results_prom) > 0,
          f"python_gc_objects_collected_total trouvé ({len(results_prom)} séries)",
          "python_gc_objects_collected_total absent — Prometheus ne scrape pas encore")
except Exception as e:
    fail(f"Erreur requête Prometheus : {e}")
    results["failed"] += 1

try:
    r = httpx.get(f"{PROMETHEUS}/api/v1/query?query=python_info", timeout=5)
    data = r.json()
    res = data.get("data", {}).get("result", [])
    if res:
        version = res[0].get("metric", {}).get("version", "?")
        ok(f"Python version détectée : {version}")
        results["passed"] += 1
except Exception:
    pass


# ══════════════════════════════════════════════════════════════════════════════
# 3. GÉNÉRER DES MÉTRIQUES OCR — appels API réels
# ══════════════════════════════════════════════════════════════════════════════
header("3. GÉNÉRATION DE MÉTRIQUES OCR (appels API)")

# D'abord s'authentifier
token = None
try:
    r = httpx.post(f"{BASE}/auth/login",
                   json={"email": "operator@arabsoft.com.tn", "password": "Operator12345!"},
                   timeout=10)
    if r.status_code == 200:
        token = r.json().get("access_token")
        ok("Authentification réussie (JWT)")
        results["passed"] += 1
    else:
        warn(f"Login échoué (status {r.status_code}) — on essaie avec X-API-Key")
except Exception as e:
    warn(f"Login impossible : {e} — on essaie avec X-API-Key")

# Headers d'auth
headers = {}
if token:
    headers["Authorization"] = f"Bearer {token}"
else:
    headers["X-API-Key"] = "dev-key-123"

# Appel /health pour générer des métriques HTTP
info("Envoi de 5 requêtes /health pour peupler http_requests_total...")
for i in range(5):
    try:
        httpx.get(f"{BASE}/health", headers=headers, timeout=5)
    except Exception:
        pass
time.sleep(2)

# Vérifier que http_requests_total est maintenant peuplé
try:
    r = httpx.get(f"{PROMETHEUS}/api/v1/query?query=http_requests_total", timeout=5)
    data = r.json()
    res = data.get("data", {}).get("result", [])
    check(len(res) > 0,
          f"http_requests_total peuplé ({len(res)} séries)",
          "http_requests_total toujours vide — attendre le prochain scrape (15s)")
    if res:
        for serie in res[:3]:
            handler = serie.get("metric", {}).get("handler", "?")
            value = serie.get("value", [None, "0"])[1]
            info(f"  handler={handler}  count={float(value):.0f}")
except Exception as e:
    fail(f"Erreur : {e}")
    results["failed"] += 1

# Appel /extract avec une vraie image de test si elle existe
import os
sample_images = [
    "app/data/samples/invoices/facture1.jpg",
    "app/data/samples/invoices/facture3.jpg",
]
extracted = False
for img_path in sample_images:
    if os.path.exists(img_path):
        info(f"Test d'extraction avec {img_path}...")
        try:
            with open(img_path, "rb") as f:
                r = httpx.post(
                    f"{BASE}/extract",
                    files={"file": (os.path.basename(img_path), f, "image/jpeg")},
                    headers=headers,
                    timeout=120,
                )
            if r.status_code == 200:
                data = r.json()
                status = data.get("status", "?")
                engine = data.get("engine_used", "?")
                ok(f"Extraction réussie — status={status}  engine={engine}")
                results["passed"] += 1
                extracted = True
                break
            else:
                warn(f"Extraction retournée {r.status_code} : {r.text[:200]}")
        except Exception as e:
            warn(f"Extraction impossible : {e}")
        break

if not extracted:
    warn("Aucune image de test disponible — métriques OCR resteront à 0")
    info("Pour les générer manuellement : POST http://localhost:8000/extract avec une image")


# ══════════════════════════════════════════════════════════════════════════════
# 4. VÉRIFIER LES MÉTRIQUES OCR DANS PROMETHEUS
# ══════════════════════════════════════════════════════════════════════════════
header("4. MÉTRIQUES OCR DANS PROMETHEUS")

# Attendre le prochain scrape
if extracted:
    info("Attente du scrape Prometheus (15s)...")
    time.sleep(16)

ocr_metrics = [
    "ocr_extractions_total",
    "ocr_extraction_confidence",
    "ocr_processing_duration_seconds",
    "ocr_field_extraction_total",
    "ocr_job_queue_depth",
]

for metric in ocr_metrics:
    try:
        r = httpx.get(f"{PROMETHEUS}/api/v1/query?query={metric}", timeout=5)
        data = r.json()
        res = data.get("data", {}).get("result", [])
        if len(res) > 0:
            ok(f"{metric} — {len(res)} série(s)")
            results["passed"] += 1
        else:
            warn(f"{metric} — vide (normal si aucune extraction faite)")
    except Exception as e:
        fail(f"{metric} — erreur : {e}")
        results["failed"] += 1


# ══════════════════════════════════════════════════════════════════════════════
# 5. VÉRIFIER GRAFANA DATASOURCE
# ══════════════════════════════════════════════════════════════════════════════
header("5. GRAFANA DATASOURCE")

grafana_user = "admin"
grafana_pass = "Admin12345"  # Adapter si tu as changé dans .env

try:
    r = httpx.get(
        f"{GRAFANA}/api/datasources",
        auth=(grafana_user, grafana_pass),
        timeout=5,
    )
    if r.status_code == 200:
        datasources = r.json()
        prom_ds = [ds for ds in datasources if ds.get("type") == "prometheus"]
        check(len(prom_ds) > 0,
              f"Datasource Prometheus configurée dans Grafana ({prom_ds[0].get('name', '?') if prom_ds else ''})",
              "Aucune datasource Prometheus dans Grafana — vérifier grafana-datasource.yml")
    else:
        warn(f"Grafana API retourne {r.status_code} — vérifier les credentials dans ce script")
except Exception as e:
    fail(f"Grafana API inaccessible : {e}")
    results["failed"] += 1

try:
    r = httpx.get(
        f"{GRAFANA}/api/search?type=dash-db",
        auth=(grafana_user, grafana_pass),
        timeout=5,
    )
    if r.status_code == 200:
        dashboards = r.json()
        ocr_dash = [d for d in dashboards if "ocr" in d.get("title", "").lower()]
        check(len(ocr_dash) > 0,
              f"Dashboard OCR trouvé : '{ocr_dash[0].get('title', '?')}'",
              f"Dashboard OCR absent — vérifier infra/grafana/dashboards/ ({len(dashboards)} dashboards total)")
        if not ocr_dash and len(dashboards) > 0:
            info(f"Dashboards présents : {[d.get('title') for d in dashboards]}")
except Exception as e:
    fail(f"Impossible de lister les dashboards : {e}")
    results["failed"] += 1


# ══════════════════════════════════════════════════════════════════════════════
# RÉSUMÉ
# ══════════════════════════════════════════════════════════════════════════════
total = results["passed"] + results["failed"]
header(f"RÉSUMÉ — {results['passed']}/{total} tests passés")

if results["failed"] == 0:
    print(f"\n{GREEN}{BOLD}Tout fonctionne ! Ouvre http://localhost:3000 pour voir le dashboard.{RESET}\n")
else:
    print(f"\n{YELLOW}{BOLD}{results['failed']} problème(s) à corriger — voir les ❌ ci-dessus.{RESET}")
    print(f"\n{BOLD}Commandes utiles :{RESET}")
    print("  docker compose logs -f backend     # logs du backend")
    print("  docker compose logs -f prometheus  # logs de Prometheus")
    print("  docker compose ps                  # état de tous les services")
    print()