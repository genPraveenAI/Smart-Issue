from __future__ import annotations

import json
import re
from functools import lru_cache
from io import BytesIO
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from PIL import Image, UnidentifiedImageError

from .agent import redact_sensitive_text

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_LOG_BYTES = 1024 * 1024
MAX_LOG_CHARS = 180_000
MAX_IMAGE_SIZE = (1920, 2880)
MAX_CAPTURE_FRAMES = 10


def stitch_capture_frames(frames: list[bytes]) -> bytes:
    if not frames or len(frames) > MAX_CAPTURE_FRAMES:
        raise ValueError(f"Scroll capture must contain between 1 and {MAX_CAPTURE_FRAMES} frames.")

    images = []
    for frame in frames:
        if not frame or len(frame) > MAX_IMAGE_BYTES:
            raise ValueError("Each captured frame must be smaller than 4 MB.")
        try:
            with Image.open(BytesIO(frame)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Captured frames must be JPEG, PNG, or WebP images.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Captured frame dimensions exceed the 16-megapixel limit.")
                source.load()
                image = source.convert("RGB")
                image.thumbnail((1280, 900), Image.Resampling.LANCZOS)
                images.append(image)
        except (OSError, UnidentifiedImageError) as error:
            raise ValueError("A scroll-capture frame is not a valid image.") from error

    columns = 1 if len(images) == 1 else 2
    gutter = 8
    column_widths = [max(image.width for image in images[index::columns]) for index in range(columns)]
    row_heights = [
        max(image.height for image in images[index : index + columns])
        for index in range(0, len(images), columns)
    ]
    canvas_width = sum(column_widths) + gutter * (columns - 1)
    canvas_height = sum(row_heights) + gutter * (len(row_heights) - 1)
    if canvas_width * canvas_height > MAX_IMAGE_PIXELS:
        raise ValueError("Combined scroll capture exceeds the 16-megapixel limit.")

    canvas = Image.new("RGB", (canvas_width, canvas_height), "white")
    row_y = 0
    for row_index, row_height in enumerate(row_heights):
        column_x = 0
        for column_index in range(columns):
            frame_index = row_index * columns + column_index
            if frame_index < len(images):
                canvas.paste(images[frame_index], (column_x, row_y))
            column_x += column_widths[column_index] + gutter
        row_y += row_height + gutter

    output = BytesIO()
    canvas.save(output, format="JPEG", quality=80, optimize=True)
    combined = output.getvalue()
    if len(combined) > MAX_IMAGE_BYTES:
        canvas.thumbnail((1600, 2400), Image.Resampling.LANCZOS)
        output = BytesIO()
        canvas.save(output, format="JPEG", quality=68, optimize=True)
        combined = output.getvalue()
    if not combined or len(combined) > MAX_IMAGE_BYTES:
        raise ValueError("Combined scroll capture is larger than the 4 MB limit.")
    return combined


class EvidenceState(TypedDict, total=False):
    screenshot: bytes | None
    screenshot_mime: str | None
    diagnostic_context: dict[str, str]
    sanitized_screenshot: bytes | None
    sanitized_screenshot_mime: str | None
    console_log: bytes | None
    sanitized_log: str | None
    log_redactions: int


def sanitize_screenshot(data: bytes | None, declared_mime: str | None) -> tuple[bytes | None, str | None]:
    if data is None:
        return None, None
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Screenshot must be smaller than 4 MB.")

    try:
        with Image.open(BytesIO(data)) as source:
            image_format = source.format
            if image_format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Screenshot must be a JPEG, PNG, or WebP image.")
            expected_mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[image_format]
            if declared_mime and declared_mime not in {expected_mime, "image/jpg" if image_format == "JPEG" else expected_mime}:
                raise ValueError("Screenshot file contents do not match the selected image type.")
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("Screenshot dimensions exceed the 16-megapixel limit.")
            source.load()
            image = source.copy()
            image.thumbnail(MAX_IMAGE_SIZE, Image.Resampling.LANCZOS)
            output = BytesIO()
            if image_format == "JPEG":
                image.convert("RGB").save(output, format="JPEG", quality=82, optimize=True)
            elif image_format == "WEBP":
                image.save(output, format="WEBP", quality=82, method=4)
            else:
                image.save(output, format="PNG", optimize=True)
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError("The selected screenshot is not a valid image.") from error

    sanitized = output.getvalue()
    if not sanitized or len(sanitized) > MAX_IMAGE_BYTES:
        raise ValueError("Sanitized screenshot is larger than the 4 MB limit.")
    return sanitized, expected_mime


def sanitize_console_log(data: bytes | None) -> tuple[str | None, int]:
    if data is None:
        return None, 0
    if len(data) > MAX_LOG_BYTES:
        raise ValueError("Diagnostic log must be smaller than 1 MB.")
    decoded = data.decode("utf-8", errors="replace")
    if "\x00" in decoded:
        raise ValueError("Diagnostic log must be plain text.")
    decoded = decoded[:MAX_LOG_CHARS]
    redactions = 0

    def replace_secret(match: re.Match[str]) -> str:
        nonlocal redactions
        redactions += 1
        return f"{match.group(1)}[redacted]"

    secret_pattern = re.compile(
        r"(?i)([\"']?\b(?:authorization|proxy-authorization|cookie|set-cookie|password|passwd|access[_-]?token|refresh[_-]?token|api[_-]?key|client[_-]?secret)\b[\"']?\s*[:=]\s*)(?:(?:Bearer|Basic)\s+)?(\"[^\"]*\"|'[^']*'|[^\s,;}\"]+)"
    )
    decoded = secret_pattern.sub(lambda match: replace_secret(match), decoded)
    decoded = redact_sensitive_text(decoded)
    redactions += sum(decoded.count(marker) for marker in ("[redacted email]", "[redacted number]", "[redacted phone]"))
    return decoded.strip(), redactions


def build_diagnostic_bundle(context: dict[str, str]) -> bytes | None:
    allowed_fields = {
        "browser",
        "runtime",
        "captured_at",
        "event_occurred_at",
        "application_version",
        "workflow",
        "error_code",
        "application_event_id",
    }
    details = {
        key: value[:500]
        for key, value in context.items()
        if key in allowed_fields and isinstance(value, str) and value.strip()
    }
    if not details:
        return None
    return json.dumps(
        {"source": "smartissue_host_event_and_runtime", "details": details},
        indent=2,
    ).encode("utf-8")


@lru_cache(maxsize=1)
def build_evidence_agent():
    def sanitize_image_node(state: EvidenceState) -> dict[str, Any]:
        sanitized, mime = sanitize_screenshot(state.get("screenshot"), state.get("screenshot_mime"))
        return {"sanitized_screenshot": sanitized, "sanitized_screenshot_mime": mime}

    def redact_log_node(state: EvidenceState) -> dict[str, Any]:
        console_log = state.get("console_log")
        if console_log is None:
            console_log = build_diagnostic_bundle(state.get("diagnostic_context", {}))
        log, redactions = sanitize_console_log(console_log)
        return {"sanitized_log": log, "log_redactions": redactions}

    graph = StateGraph(EvidenceState)
    graph.add_node("screenshot_sanitizer", sanitize_image_node)
    graph.add_node("diagnostic_log_redactor", redact_log_node)
    graph.add_edge(START, "screenshot_sanitizer")
    graph.add_edge("screenshot_sanitizer", "diagnostic_log_redactor")
    graph.add_edge("diagnostic_log_redactor", END)
    return graph.compile()


def process_evidence(
    *,
    screenshot: bytes | None = None,
    screenshot_mime: str | None = None,
    console_log: bytes | None = None,
    diagnostic_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    return build_evidence_agent().invoke(
        {
            "screenshot": screenshot,
            "screenshot_mime": screenshot_mime,
            "console_log": console_log,
            "diagnostic_context": diagnostic_context or {},
        }
    )