# Makefile para automação de tarefas TubeWranglerr

lint:
build:

.PHONY: test lint clean build

test:
	docker compose exec tubewranglerr python3 -m pytest tests/ -v

lint:
	docker compose exec tubewranglerr pip install --quiet flake8
	docker compose exec tubewranglerr flake8 core/ scripts/

clean:
	docker compose exec tubewranglerr rm -rf .pytest_cache __pycache__ core/__pycache__ scripts/__pycache__ tests/__pycache__

build:
	docker compose build

