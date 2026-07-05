.PHONY: help bootstrap up down logs ps build test lint shell-api shell-worker

help:
	@echo "Targets:"
	@echo "  bootstrap  Copy .env.example, build, start core services"
	@echo "  up         docker compose up -d"
	@echo "  down       docker compose down"
	@echo "  logs       docker compose logs -f"
	@echo "  ps         docker compose ps"
	@echo "  build      docker compose build"
	@echo "  test       run pytest"
	@echo "  lint       run ruff check ."

bootstrap:
	bash scripts/bootstrap.sh

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

build:
	docker compose build

test:
	python -m pytest -q

lint:
	ruff check .

shell-api:
	docker compose exec platform-api bash

shell-worker:
	docker compose exec worker-demo bash

monitoring-up:
	docker compose --profile monitoring up -d

collective-setup:
	bash scripts/setup-collective.sh

collective-up:
	docker compose --profile collective up -d bot-collective worker-collective

collective-logs:
	docker compose --profile collective logs -f bot-collective worker-collective

exoplanet-setup:
	bash scripts/setup-exoplanet.sh

exoplanet-up:
	docker compose --profile exoplanet up -d bot-exoplanet worker-exoplanet

exoplanet-logs:
	docker compose --profile exoplanet logs -f bot-exoplanet worker-exoplanet
