PY ?= .venv/bin/python
PIP ?= .venv/bin/pip
PORT ?= 8741

.PHONY: install run test cli

install:
	python3 -m venv .venv
	$(PIP) install -r requirements.txt

run:
	$(PY) -m uvicorn storyteller.app:app --host 0.0.0.0 --port $(PORT)

test:
	$(PY) -m pytest -q

cli:
	$(PY) -m storyteller.cli $(NAME)
