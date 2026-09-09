.PHONY: test lint run discover fetch

test:
	OFFLINE=1 python -m pytest

lint:
	python -m ruff check leadcentre tests

run: ## скоринг из кэша, сеть не нужна
	OFFLINE=1 python -m leadcentre.cli discover

discover: ## скоринг с живым GLEIF
	python -m leadcentre.cli discover

fetch: ## обновить кэш из GLEIF
	python -m leadcentre.cli fetch
