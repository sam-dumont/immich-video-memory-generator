"""The duration and motion constants the production structure editor plans against."""

from __future__ import annotations

CONTENT_RESERVE_SECONDS = 7.5
MIN_CARRIER_SECONDS = 3.5
NOMINAL_STILL_SECONDS = 4.0
# A Live Photo plays only when its measured motion residual reaches this; below it the
# photograph is held.
RESIDUAL_MIN = 1.5
