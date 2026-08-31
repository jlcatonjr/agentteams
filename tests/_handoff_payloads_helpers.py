"""Module-level helpers for handoff_payloads tests.

Defined at module scope so `multiprocessing` (spawn context) can pickle them.
"""

from __future__ import annotations

import time
from typing import Any


def slow_validate_worker(payload: Any, schema: dict, queue) -> None:
    time.sleep(5.0)
    queue.put(("ok", None))
