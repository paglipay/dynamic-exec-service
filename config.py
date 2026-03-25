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
    "plugins.system_tools.file_system_plugin": {
        "class": "FileSystemPlugin",
        "methods": [
            "list_directory",
            "create_directory",
            "move_path",
            "delete_path",
            "path_info",
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
            "preview_sheet",
            "excel_to_json",
            "list_columns_in_sheet",
            "list_sheet_names",
            "append_mapped_output_change",
            "update_sheet_row_values",
        ],
    },
    "plugins.system_tools.markdown_pdf_plugin": {
        "class": "MarkdownPDFPlugin",
        "methods": [
            "markdown_to_pdf",
        ],
    },
    "plugins.system_tools.pdf_plugin": {
        "class": "PDFPlugin",
        "methods": [
            "pdf_to_text",
            "pdf_to_images",
        ],
    },
    "plugins.system_tools.word_template_plugin": {
        "class": "WordTemplatePlugin",
        "methods": [
            "generate_documents",
        ],
    },
    "plugins.system_tools.apscheduler_plugin": {
        "class": "APSchedulerPlugin",
        "methods": [
            "start_scheduler",
            "stop_scheduler",
            "health",
            "list_jobs",
            "remove_job",
            "get_last_run",
            "run_workflow_now",
            "add_interval_workflow_job",
            "add_date_workflow_job",
            "add_cron_workflow_job",
        ],
    },
    "plugins.system_tools.streamlit_plugin": {
        "class": "StreamlitPlugin",
        "methods": [
            "create_app_file",
            "start_app",
            "status",
            "stop_app",
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
            "generate_image",
        ],
    },
    "plugins.integrations.openai_plugin": {
        "class": "OpenAIFunctionCallingPlugin",
        "methods": [
            "generate_with_function_calls",
            "generate_with_function_calls_and_history",
            "redis_health_check",
        ],
    },
    "plugins.integrations.pika_plugin": {
        "class": "PikaPlugin",
        "methods": [
            "connect",
            "connection_status",
            "disconnect",
            "publish_message",
            "publish_workflow",
            "subscribe",
            "consume",
            "consume_and_execute_workflow",
            "start_consuming_workflows",
        ],
    },
    "plugins.integrations.gmail_plugin": {
        "class": "GmailPlugin",
        "methods": [
            "get_profile",
            "list_messages",
            "get_message",
            "send_email",
        ],
    },
    "plugins.integrations.github_repo_sync_plugin": {
        "class": "GitHubRepoSyncPlugin",
        "methods": [
            "upsert_text_file",
            "commit_streamlit_app",
        ],
    },
}