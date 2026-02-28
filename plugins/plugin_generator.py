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
        template = plugin_definition.get("template")

        self._validate_identifier(plugin_name, "plugin_name")
        self._validate_identifier(class_name, "class_name")

        if not isinstance(overwrite, bool):
            raise ValueError("overwrite must be a boolean")
        if template is not None and not isinstance(template, str):
            raise ValueError("template must be a string")

        class_source: str
        if template:
            if template != "text_file_crud":
                raise ValueError("Unsupported template")
            class_source = self._build_text_file_crud_template(class_name)
        else:
            if not isinstance(methods, list) or not methods:
                raise ValueError("methods must be a non-empty list")

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

        plugin_filename = f"{plugin_name}.py"
        plugin_path = (self.plugins_dir / plugin_filename).resolve()

        if plugin_path.parent != self.plugins_dir:
            raise ValueError("Invalid plugin path")
        if plugin_path.exists() and not overwrite:
            raise ValueError("Plugin file already exists")

        plugin_path.write_text(class_source, encoding="utf-8")

        return {
            "status": "success",
            "message": "Plugin created successfully" if not overwrite else "Plugin created or overwritten successfully",
            "plugin_file": plugin_filename,
            "class_name": class_name,
        }

    def _build_text_file_crud_template(self, class_name: str) -> str:
        """Build source for a safe text-file CRUD plugin template."""
        return (
            '"""Generated text file CRUD plugin module."""\n\n'
            "from __future__ import annotations\n\n"
            "from pathlib import Path\n\n\n"
            f"class {class_name}:\n"
            "    \"\"\"CRUD operations for .txt files within a base directory.\"\"\"\n\n"
            "    def __init__(self, base_dir: str = \"generated_data\") -> None:\n"
            "        if not isinstance(base_dir, str) or not base_dir:\n"
            "            raise ValueError(\"base_dir must be a non-empty string\")\n"
            "        self.base_dir = Path(base_dir).resolve()\n"
            "        self.base_dir.mkdir(parents=True, exist_ok=True)\n\n"
            "    def _resolve_filename(self, filename: str) -> Path:\n"
            "        if not isinstance(filename, str) or not filename:\n"
            "            raise ValueError(\"filename must be a non-empty string\")\n"
            "        if not filename.endswith(\".txt\"):\n"
            "            raise ValueError(\"Only .txt files are allowed\")\n"
            "        if \"/\" in filename or \"\\\\\" in filename:\n"
            "            raise ValueError(\"filename must not contain path separators\")\n"
            "\n"
            "        file_path = (self.base_dir / filename).resolve()\n"
            "        if file_path.parent != self.base_dir:\n"
            "            raise ValueError(\"Invalid file path\")\n"
            "        return file_path\n\n"
            "    def create_text(self, filename: str, content: str):\n"
            "        if not isinstance(content, str):\n"
            "            raise ValueError(\"content must be a string\")\n"
            "        file_path = self._resolve_filename(filename)\n"
            "        if file_path.exists():\n"
            "            raise ValueError(\"File already exists\")\n"
            "        file_path.write_text(content, encoding=\"utf-8\")\n"
            "        return {\"status\": \"success\", \"action\": \"create\", \"filename\": filename}\n\n"
            "    def read_text(self, filename: str):\n"
            "        file_path = self._resolve_filename(filename)\n"
            "        if not file_path.exists():\n"
            "            raise ValueError(\"File does not exist\")\n"
            "        return {\"status\": \"success\", \"action\": \"read\", \"filename\": filename, \"content\": file_path.read_text(encoding=\"utf-8\")}\n\n"
            "    def update_text(self, filename: str, content: str):\n"
            "        if not isinstance(content, str):\n"
            "            raise ValueError(\"content must be a string\")\n"
            "        file_path = self._resolve_filename(filename)\n"
            "        if not file_path.exists():\n"
            "            raise ValueError(\"File does not exist\")\n"
            "        file_path.write_text(content, encoding=\"utf-8\")\n"
            "        return {\"status\": \"success\", \"action\": \"update\", \"filename\": filename}\n\n"
            "    def delete_text(self, filename: str):\n"
            "        file_path = self._resolve_filename(filename)\n"
            "        if not file_path.exists():\n"
            "            raise ValueError(\"File does not exist\")\n"
            "        file_path.unlink()\n"
            "        return {\"status\": \"success\", \"action\": \"delete\", \"filename\": filename}\n\n"
            "    def list_text_files(self):\n"
            "        files = sorted(path.name for path in self.base_dir.glob(\"*.txt\") if path.is_file())\n"
            "        return {\"status\": \"success\", \"action\": \"list\", \"files\": files}\n"
        )

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
