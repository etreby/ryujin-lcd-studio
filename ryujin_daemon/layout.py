"""Minimal JSON layout contract for the Ryujin III LCD.

The contract is also shipped as a JSON Schema (``schemas/layout.schema.json``)
so the GUI designer can target it. ``load_layout`` parses and validates a
layout document against that schema when ``jsonschema`` is installed, falling
back to the built-in checks otherwise.
"""

import json
from pathlib import Path

try:  # optional dependency
    import jsonschema
except Exception:  # noqa: BLE001
    jsonschema = None


class LayoutError(ValueError):
    """Raised for invalid layout contracts."""


LAYOUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://ryujin-lcd-studio/layout.schema.json",
    "title": "Ryujin III LCD layout",
    "type": "object",
    "required": ["width", "height", "format"],
    "properties": {
        "width": {"type": "integer", "minimum": 1},
        "height": {"type": "integer", "minimum": 1},
        "format": {"type": "string", "enum": ["gray8"]},
    },
    "additionalProperties": True,
}


def _schema_path():
    return Path(__file__).resolve().parent.parent / "schemas" / "layout.schema.json"


def load_schema():
    path = _schema_path()
    if path.exists():
        return json.loads(path.read_text())
    return LAYOUT_SCHEMA


def load_layout(path):
    try:
        layout = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise LayoutError(f"invalid layout JSON: {error}")
    schema = load_schema()
    if jsonschema is not None:
        try:
            jsonschema.validate(layout, schema)
        except jsonschema.ValidationError as error:
            raise LayoutError(str(error.message))
    else:
        if not isinstance(layout, dict) or not isinstance(layout.get("width"), int) or not isinstance(layout.get("height"), int):
            raise LayoutError("layout requires integer width and height")
        if layout.get("format") != "gray8":
            raise LayoutError("layout format must be gray8")
    if layout.get("width", 0) < 1 or layout.get("height", 0) < 1:
        raise LayoutError("layout width/height must be positive")
    return layout


def export_schema(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(load_schema(), indent=2) + "\n")
