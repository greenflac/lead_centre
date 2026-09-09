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

test-ci: ## тесты с машинным запретом сети (Т4)
	PYTHONPATH=ci:. OFFLINE=1 python -m pytest -p no:cacheprovider

test-ci-selfcheck: ## негативный контроль запрета: обращение к сети обязано упасть
	@PYTHONPATH=ci python -c "import urllib.request as u; u.urlopen('https://api.gleif.org/', timeout=5)" \
		2>/dev/null && (echo "ПРОВАЛ: сеть прошла, запрет не работает"; exit 1) \
		|| echo "запрет сети работает"

mutate: ## прогон мутации без ловушки кэша байт-кода
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	PYTHONPATH=ci:. OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider
