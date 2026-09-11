"""Network ban for tests, enforced by the machine rather than by agreement.

Placed on PYTHONPATH as sitecustomize, so Python imports it at startup and a test cannot
work around it. `make test-ci-selfcheck` is the negative control: a silent ban would be
indistinguishable from no ban at all.
"""
from __future__ import annotations

import socket


class NetworkInTests(RuntimeError):
    """A test tried to reach the network."""


def _blocked(*_args, **_kwargs):
    raise NetworkInTests(
        "тест пошёл в сеть — это запрещено: используйте OFFLINE=1 и кэш в data/"
    )


socket.socket.connect = _blocked
socket.create_connection = _blocked
