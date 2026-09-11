"""Transition buffer constant shared by clip extraction."""

from __future__ import annotations

# Transition buffer: extra footage before/after each segment for smooth fades
# This allows crossfade transitions without cutting into the main content
TRANSITION_BUFFER = 0.5  # seconds
