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
    "plugins.local_http_module": {
        "class": "LocalHTTPModule",
        "methods": ["post_execute"],
    },
    "plugins.generated_math_plugin": {
        "class": "GeneratedMathPlugin",
        "methods": ["multiply", "greet"],
    },
    "plugins.text_file_crud_plugin": {
        "class": "TextFileCRUDPlugin",
        "methods": [
            "create_text",
            "read_text",
            "update_text",
            "delete_text",
            "list_text_files",
        ],
    },
    "plugins.ssh_module": {
        "class": "SSHModule",
        "methods": ["run_command", "list_directory"],
    },
    "plugins.plugin_generator": {
        "class": "PluginGenerator",
        "methods": ["create_plugin"],
    },
    "plugins.system_tools.terminal_introspection_plugin": {
        "class": "TerminalIntrospectionPlugin",
        "methods": [
            "get_environment_summary",
            "list_directory",
            "discover_folder_structure",
            "pip_freeze",
        ],
    },
    "plugins.system_tools.subprocess_plugin": {
        "class": "SubprocessPlugin",
        "methods": [
            "run_python_script",
        ],
    },
    "plugins.system_tools.excel_plugin": {
        "class": "ExcelPlugin",
        "methods": [
            "excel_to_json",
            "list_columns_in_sheet",
            "list_sheet_names",
        ],
    },
    "plugins.integrations.slack_plugin": {
        "class": "SlackPlugin",
        "methods": [
            "post_message",
            "upload_text_file",
            "upload_local_file",
        ],
    },
    "plugins.integrations.openai_http_plugin": {
        "class": "OpenAIHTTPPlugin",
        "methods": [
            "generate_text",
        ],
    },
    "plugins.integrations.openai_sdk_plugin": {
        "class": "OpenAISDKPlugin",
        "methods": [
            "generate_text",
            "generate_text_with_history",
            "reply_with_plugins",
        ],
    },
    "plugins.integrations.openai_plugin": {
        "class": "OpenAIFunctionCallingPlugin",
        "methods": [
            "generate_with_function_calls",
            "generate_with_function_calls_and_history",
        ],
    },
}