BREW_PREFIX := $(shell brew --prefix 2>/dev/null || echo /opt/homebrew)
PYTHON ?= $(BREW_PREFIX)/opt/python@3.12/bin/python3.12
VENV := .venv

.PHONY: setup dev run test lint docker-up docker-down

setup:
	@test -x "$(PYTHON)" || (echo "Install Python 3.12: brew install python@3.12" && exit 1)
	@test -x "$(VENV)/bin/python" || "$(PYTHON)" -m venv "$(VENV)"
	"$(VENV)/bin/python" -m pip install --upgrade pip
	"$(VENV)/bin/python" -m pip install -r requirements-dev.txt

dev: setup
	"$(VENV)/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 --reload

run:
	"$(VENV)/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 --workers 1

test:
	"$(VENV)/bin/pytest"

lint:
	"$(VENV)/bin/ruff" check app tests

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

