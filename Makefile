.PHONY: help test lint health preflight

help:
	@echo "muffinpanrecipes common commands"
	@echo "  make test       run the Python test suite"
	@echo "  make lint       run ruff checks"
	@echo "  make health     run production health check with Doppler"
	@echo "  make preflight  verify required Doppler-injected secrets are present"

test:
	uv run pytest -q

lint:
	uv run ruff check .

health:
	doppler run -- uv run python scripts/health_check.py

preflight:
	doppler run -- sh -lc 'if [ -n "$$STABILITY_API_KEY" ]; then echo "STABILITY_API_KEY: present"; else echo "STABILITY_API_KEY: missing"; exit 1; fi'
