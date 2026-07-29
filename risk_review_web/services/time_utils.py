from __future__ import annotations

import time
from datetime import datetime, timezone


def iso_time(epoch_value=None) -> str:
    """Return a UTC ISO timestamp, using the current time for invalid input."""
    try:
        epoch = float(epoch_value) if epoch_value is not None else time.time()
    except (TypeError, ValueError):
        epoch = time.time()
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()
