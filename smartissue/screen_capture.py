from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

COMPONENT_PATH = Path(__file__).resolve().parent.parent / "components" / "screen_capture"
_screen_capture = components.declare_component("smartissue_screen_capture", path=str(COMPONENT_PATH))


def capture_screen(*, key: str) -> dict[str, Any] | None:
    """Request screen permission and collect page frames while the associate scrolls."""
    return _screen_capture(key=key, default=None)