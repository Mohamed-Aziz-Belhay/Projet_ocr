# ─────────────────────────────────────────────────────────────────────────────
# OCR Microservice Enterprise — Makefile
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: help install dev test lint build up down logs clean keys setup-enterprise migrate

PYTHON   := python3
DOCKER   := docker compose
DOCKER_E := docker compose -f docker-compose.enterprise.yml
API_URL  := http://localhost:8000

help:
	@echo ""
	@echo "  OCR Microservice — Enterprise Commands"
	@echo "  ──────────────────────────────────────"
	@echo "  Dev:"
	@echo "    make install          Install Python deps"
	@echo "    make dev              Run in dev mode"
	@echo "    make test             Run all tests"
	@echo "    make lint             Ruff + mypy"
	@echo ""
	@echo "  Enterprise:"
	@echo "    make keys             Generate SECRET_KEY + ENCRYPTION_KEY"
	@echo "    make setup-enterprise First-time enterprise setup"
	@echo "    make migrate          Run Alembic migrations"
	@echo "    make up-enterprise    Start full enterprise stack"
	@echo "    make down-enterprise  Stop enterprise stack"
	@echo "    make worker           Start Celery worker (standalone)"
	@echo "    make beat             Start Celery beat (scheduler)"
	@echo ""
	@echo "  Docker (dev):"
	@echo "    make build            Build Docker image"
	@echo "    make up               Start dev stack"
	@echo "    make down             Stop dev stack"
	@echo "    make logs             Tail API logs"
	@echo ""
	@echo "  Benchmark:"
	@echo "    make benchmark        Run extraction benchmark"
	@echo "    make clean            Clean temp files"
	@echo ""

install:
	$(PYTHON) -m pip install -r requirements.txt

dev:
	@cp -n .env.example .env 2>/dev/null || true
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --log-level info

test:
	$(PYTHON) -m pytest tests/ -v --tb=short

test-fast:
	$(PYTHON) -m pytest tests/ -v -x --no-cov -k "not enterprise"

lint:
	@$(PYTHON) -m ruff check app/ tests/ --fix || true
	@$(PYTHON) -m mypy app/ --ignore-missing-imports || true

# ── Enterprise ─────────────────────────────────────────────────────────────────
keys:
	$(PYTHON) scripts/generate_encryption_key.py

setup-enterprise:
	$(PYTHON) scripts/setup_enterprise.py

migrate:
	alembic upgrade head

migrate-new:
	@read -p "Migration name: " name; alembic revision --autogenerate -m "$$name"

migrate-downgrade:
	alembic downgrade -1

# ── Docker dev ─────────────────────────────────────────────────────────────────
build:
	$(DOCKER) build

up:
	@cp -n .env.example .env 2>/dev/null || true
	$(DOCKER) up -d

down:
	$(DOCKER) down

logs:
	$(DOCKER) logs -f api

# ── Docker enterprise ──────────────────────────────────────────────────────────
up-enterprise:
	@cp -n .env.example .env 2>/dev/null || true
	$(DOCKER_E) up -d --build

down-enterprise:
	$(DOCKER_E) down

logs-enterprise:
	$(DOCKER_E) logs -f api worker

worker:
	celery -A app.worker.celery_app worker --loglevel=info --concurrency=4 -Q ocr,default

beat:
	celery -A app.worker.celery_app beat --loglevel=info

flower:
	celery -A app.worker.celery_app flower --port=5555

# ── Benchmark ─────────────────────────────────────────────────────────────────
benchmark:
	$(PYTHON) app/evaluation/benchmark_runner.py app/evaluation/example_benchmark_cases.json

# ── API shortcuts ──────────────────────────────────────────────────────────────
health:
	curl -s $(API_URL)/health | python3 -m json.tool

templates:
	curl -s -H "X-API-Key: $(API_KEY)" $(API_URL)/templates | python3 -m json.tool

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .coverage test.db
	rm -rf /tmp/ocr_*  2>/dev/null || true
