COMPOSE := docker compose --profile sources
UI_URL ?= http://localhost:3000

.PHONY: demo up down reset check test

## Reset the lake, start the whole stack with the demo Postgres, check every feature and open the guided tour
demo: reset up check
	@python3 scripts/demo.py --tour --url $(UI_URL)

## Start (or rebuild) the stack and wait until every service is healthy
up:
	$(COMPOSE) up --build -d --wait

## Stop the stack (keeps the lake)
down:
	$(COMPOSE) down

## Stop the stack and wipe the lake, saved sources, connections, schedule and alerts
reset:
	$(COMPOSE) down -v --remove-orphans

## Smoke-test every demo feature through the UI's /api proxy
check:
	@python3 scripts/demo.py --check --url $(UI_URL)

## Unit tests (needs each component's dependencies installed)
test:
	cd pipeline && pytest -q
	cd mcp-server && pytest -q
	cd agent && pytest -q
