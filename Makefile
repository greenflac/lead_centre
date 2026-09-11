.PHONY: test lint run discover fetch demo test-ci test-ci-selfcheck mutate check-web shots-live

test:
	OFFLINE=1 python -m pytest

lint:
	python -m ruff check leadcentre tests ci eval scripts web/scripts

run: ## скоринг из кэша, сеть не нужна
	OFFLINE=1 python -m leadcentre.cli discover

discover: ## скоринг с живым GLEIF
	python -m leadcentre.cli discover

fetch: ## обновить кэш из GLEIF
	python -m leadcentre.cli fetch

demo: ## сквозная проверка обещания из README: четыре обращения, около 32 проверок
	PYTHONPATH=. OFFLINE=1 python scripts/e2e_demo.py

shots-live: ## кадры живого режима: API без OFFLINE, своя сборка дашборда, живые вызовы модели
	bash scripts/shots_live.sh

check-web: ## приборы дашборда: контраст, свойства системы, сверка порта с движком
	python3 web/scripts/contrast.py
	python3 web/scripts/css_audit.py
	python3 web/scripts/crosscheck.py

test-ci: ## тесты с машинным запретом сети (Т4)
	PYTHONPATH=ci:. OFFLINE=1 python -m pytest -p no:cacheprovider

test-ci-selfcheck: ## негативный контроль запрета: сеть обязана упасть ИМЕННО от запрета
	@PYTHONPATH=ci python -c "import urllib.request as u; u.urlopen('https://api.gleif.org/', timeout=5)" \
		2>&1 | grep -q "NetworkInTests" \
		&& echo "запрет сети работает" \
		|| (echo "ПРОВАЛ: сеть либо прошла, либо упала не от запрета"; exit 1)
	@PYTHONPATH=ci python -c "import ci.sitecustomize" 2>/dev/null \
		|| (echo "ПРОВАЛ: модуль запрета не импортируется"; exit 1)

mutate: ## прогон мутации без ловушки кэша байт-кода
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	PYTHONPATH=ci:. OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider
