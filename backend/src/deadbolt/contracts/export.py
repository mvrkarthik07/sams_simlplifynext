"""Export JSON Schema documents for public contract dataclasses."""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

from deadbolt.contracts.clock import FixedClock
from deadbolt.contracts.models import ActionResult, Entitlement

_PUBLIC_DATACLASSES = (ActionResult, Entitlement, FixedClock)
_ContractDataclass = type[ActionResult] | type[Entitlement] | type[FixedClock]


def _schema_for_type(annotation: object) -> dict[str, object]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    schema: dict[str, object] = {}
    if origin in (Union, UnionType):
        non_none = tuple(item for item in args if item is not type(None))
        if len(non_none) == 1 and len(non_none) != len(args):
            schema = {"anyOf": [_schema_for_type(non_none[0]), {"type": "null"}]}
        else:
            schema = {"anyOf": [_schema_for_type(item) for item in args]}
    elif origin in (Mapping, dict):
        schema = {"type": "object", "additionalProperties": {}}
    elif isinstance(annotation, type) and issubclass(annotation, Enum):
        schema = {"type": "string", "enum": [member.value for member in annotation]}
    elif annotation is str:
        schema = {"type": "string"}
    elif annotation is bool:
        schema = {"type": "boolean"}
    elif annotation is int:
        schema = {"type": "integer"}
    elif annotation is datetime:
        schema = {"type": "string", "format": "date-time"}
    return schema


def schema_for_dataclass(cls: _ContractDataclass) -> dict[str, object]:
    hints = get_type_hints(cls)
    properties: dict[str, object] = {}
    required: list[str] = []
    for field in dataclasses.fields(cls):
        properties[field.name] = _schema_for_type(hints[field.name])
        if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
            required.append(field.name)
    schema: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"deadbolt.contracts.{cls.__name__}",
        "title": cls.__name__,
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def export_schemas(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for cls in _PUBLIC_DATACLASSES:
        target = output_dir / f"{cls.__name__}.json"
        target.write_text(
            json.dumps(schema_for_dataclass(cls), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, dest="output_dir")
    args = parser.parse_args()
    export_schemas(args.output_dir)


if __name__ == "__main__":
    main()
