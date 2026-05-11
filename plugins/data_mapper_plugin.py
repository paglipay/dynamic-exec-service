"""DataMapperPlugin — restructures a source dict into a new shape using a mapping spec.

Mapping spec format (constructor_args["mapping"]):
  Each key is the desired output field name. The value describes where/how to
  derive it from the source dict:

  - "field_name"            — rename/copy a top-level field directly
  - "parent.child"          — extract a nested field via dotted path
  - "field / 3600"          — divide a numeric field by a constant
  - "field * 1000"          — multiply a numeric field by a constant
  - "field + 5"             — add a constant to a numeric field
  - "field - 1"             — subtract a constant from a numeric field
  - '"static"'              — embed a hardcoded string constant (quoted)
  - 42                      — embed a hardcoded number constant (int/float)
  - "field || default"      — use field value, fall back to literal default
                              when the field is missing or None

  Any unresolvable path returns None for that output key.
"""

from __future__ import annotations

import re
from typing import Any


_ARITHMETIC_RE = re.compile(
    r"^(.+?)\s*([+\-*/])\s*(-?\d+(?:\.\d+)?)$"
)
_FALLBACK_RE = re.compile(r"^(.+?)\s*\|\|\s*(.+)$")


def _resolve_path(source: dict, path: str) -> Any:
    """Walk a dotted path through nested dicts; return None if any step is missing."""
    current: Any = source
    for part in path.strip().split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _apply_spec(source: dict, spec: Any) -> Any:
    """Derive a single output value from source using one mapping spec entry."""
    # Hardcoded constant (int / float)
    if isinstance(spec, (int, float)):
        return spec

    if not isinstance(spec, str):
        return None

    spec = spec.strip()

    # Quoted string constant: "value" or 'value'
    if (spec.startswith('"') and spec.endswith('"')) or (
        spec.startswith("'") and spec.endswith("'")
    ):
        return spec[1:-1]

    # Fallback: "field || default"
    fallback_match = _FALLBACK_RE.fullmatch(spec)
    if fallback_match:
        field_part = fallback_match.group(1).strip()
        default_part = fallback_match.group(2).strip()
        value = _resolve_path(source, field_part)
        if value is not None:
            return value
        # Default is either a quoted string or a bare literal
        if (default_part.startswith('"') and default_part.endswith('"')) or (
            default_part.startswith("'") and default_part.endswith("'")
        ):
            return default_part[1:-1]
        try:
            return int(default_part)
        except ValueError:
            try:
                return float(default_part)
            except ValueError:
                return default_part

    # Arithmetic: "field OP constant"
    arith_match = _ARITHMETIC_RE.fullmatch(spec)
    if arith_match:
        field_part = arith_match.group(1).strip()
        operator = arith_match.group(2)
        try:
            constant = float(arith_match.group(3))
        except ValueError:
            return None
        raw = _resolve_path(source, field_part)
        if raw is None:
            return None
        try:
            numeric = float(raw)
        except (TypeError, ValueError):
            return None
        if operator == "+":
            result = numeric + constant
        elif operator == "-":
            result = numeric - constant
        elif operator == "*":
            result = numeric * constant
        elif operator == "/":
            if constant == 0:
                return None
            result = numeric / constant
        else:
            return None
        # Return int when result is a whole number for cleaner JSON
        return int(result) if result == int(result) else result

    # Plain field name or dotted path
    return _resolve_path(source, spec)


class DataMapperPlugin:
    """Reshape a source JSON dict into a new structure defined by a mapping spec.

    Constructor args:
        mapping (dict): output field → spec string/constant (see module docstring).

    Method:
        map(source)  — applies the mapping to ``source`` and returns the output dict.
    """

    def __init__(self, mapping: dict[str, Any]) -> None:
        if not isinstance(mapping, dict):
            raise ValueError("mapping must be an object")
        self._mapping = mapping

    def map(self, source: Any) -> dict[str, Any]:
        """Apply the mapping spec to source and return the remapped dict.

        Args:
            source: The input dict (result from a previous workflow step).

        Returns:
            A new dict shaped according to the mapping spec.
        """
        if not isinstance(source, dict):
            raise ValueError("source must be a JSON object (dict)")

        output: dict[str, Any] = {}
        for output_key, spec in self._mapping.items():
            if not isinstance(output_key, str) or not output_key:
                continue
            output[output_key] = _apply_spec(source, spec)

        return output
