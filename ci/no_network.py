"""Запрет сети для тестов, выполняемый машиной, а не договорённостью (Т4, Ц7).

Модуль подкладывается в PYTHONPATH как sitecustomize: Python импортирует его сам при
старте, поэтому обойти запрет из теста нельзя. Тест, ушедший в сеть, падает с понятной
ошибкой, а не краснеет от чужой аварии и не зеленеет от кэша.

Негативный контроль самого запрета — `make test-ci-selfcheck`: попытка открыть внешний
адрес обязана упасть. Без этого проверки не видно: молчаливый запрет неотличим от
отсутствующего.
"""
from __future__ import annotations

import socket


class NetworkInTests(RuntimeError):
    """Тест попытался выйти в сеть."""


def _blocked(*_args, **_kwargs):
    raise NetworkInTests(
        "тест пошёл в сеть — это запрещено: используйте OFFLINE=1 и кэш в data/"
    )


socket.socket.connect = _blocked
socket.create_connection = _blocked
