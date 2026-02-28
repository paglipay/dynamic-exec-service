"""Plugin that securely generates new plugin files."""

from __future__ import annotations

import ast
import keyword
import re
from pathlib import Path
from typing import Any


IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
DANGEROUS_PATTERN = re.compile(
    r"\b(import|exec|eval|os|subprocess|open|compile|__import__|input|globals|locals|sys)\b"
)


class PluginGenerator:
    """Creates simple, safe plugin classes from validated definitions."""

    def __init__(self) -> None:
        self.plugins_dir = Path(__file__).resolve().parent

    def create_plugin(self, plugin_definition: dict[str, Any]) -> dict[str, Any]:
        """Validate a plugin definition and generate a plugin source file."""
        if not isinstance(plugin_definition, dict):
            raise ValueError("plugin_definition must be an object")

        plugin_name = plugin_definition.get("plugin_name")
        class_name = plugin_definition.get("class_name")
        methods = plugin_definition.get("methods")
        overwrite = plugin_definition.get("overwrite", False)

        self._validate_identifier(plugin_name, "plugin_name")
        self._validate_identifier(class_name, "class_name")

        if not isinstance(methods, list) or not methods:
            raise ValueError("methods must be a non-empty list")
        if not isinstance(overwrite, bool):
            raise ValueError("overwrite must be a boolean")

        plugin_filename = f"{plugin_name}.py"
        plugin_path = (self.plugins_dir / plugin_filename).resolve()

        if plugin_path.parent != self.plugins_dir:
            raise ValueError("Invalid plugin path")
        if plugin_path.exists() and not overwrite:
            raise ValueError("Plugin file already exists")

        method_sources: list[str] = []
        for method in methods:
            method_sources.append(self._build_method_source(method))

        class_source = (
            '"""Generated plugin module."""\n\n\n'
            f"class {class_name}:\n"
            "    \"\"\"Auto-generated plugin class.\"\"\"\n\n"
            "    def __init__(self) -> None:\n"
            "        pass\n\n"
            f"{'\n\n'.join(method_sources)}\n"
        )

        plugin_path.write_text(class_source, encoding="utf-8")

        return {
            "status": "success",
            "message": "Plugin created successfully" if not overwrite else "Plugin created or overwritten successfully",
            "plugin_file": plugin_filename,
            "class_name": class_name,
        }

    def _validate_identifier(self, value: Any, field_name: str) -> None:
        """Validate Python identifier values and reject keywords."""
        if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError(f"{field_name} is not a valid identifier")
        if keyword.iskeyword(value):
            raise ValueError(f"{field_name} cannot be a Python keyword")

    def _build_method_source(self, method_definition: dict[str, Any]) -> str:
        """Validate and render a method definition to source code."""
        if not isinstance(method_definition, dict):
            raise ValueError("Each method must be an object")

        method_name = method_definition.get("method_name")
        parameters = method_definition.get("parameters", [])
        body = method_definition.get("body")

        self._validate_identifier(method_name, "method_name")
        if not isinstance(parameters, list):
            raise ValueError("parameters must be a list")
        for parameter in parameters:
            self._validate_identifier(parameter, "parameter")

        if not isinstance(body, str) or not body.strip():
            raise ValueError("body must be a non-empty string")
        self._validate_method_body(body)

        parameters_signature = ", ".join(["self", *parameters])
        normalized_body = body.strip()
        return (
            f"    def {method_name}({parameters_signature}):\n"
            f"        {normalized_body}"
        )

    def _validate_method_body(self, body: str) -> None:
        """Allow only a single return statement and block dangerous tokens."""
        normalized = body.strip()
        if "\n" in normalized:
            raise ValueError("Method body must be a single line return statement")
        if DANGEROUS_PATTERN.search(normalized):
            raise ValueError("Method body contains forbidden content")

        try:
            module_node = ast.parse(normalized)
        except SyntaxError as exc:
            raise ValueError("Method body is not valid Python") from exc

        if len(module_node.body) != 1 or not isinstance(module_node.body[0], ast.Return):
            raise ValueError("Method body must contain exactly one return statement")
