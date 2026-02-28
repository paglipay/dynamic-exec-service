"""Application configuration for dynamic execution permissions."""

from typing import Dict, List, TypedDict


AllowedModuleConfig = TypedDict(
    "AllowedModuleConfig",
    {"class": str, "methods": List[str]},
)


ALLOWED_MODULES: Dict[str, AllowedModuleConfig] = {
    "plugins.sample_module": {
        "class": "SampleModule",
        "methods": ["add", "process"],
    },
    "plugins.generated_math_plugin": {
        "class": "GeneratedMathPlugin",
        "methods": ["multiply", "greet"],
    },
    "plugins.ssh_module": {
        "class": "SSHModule",
        "methods": ["run_command", "list_directory"],
    },
    "plugins.plugin_generator": {
        "class": "PluginGenerator",
        "methods": ["create_plugin"],
    },
}