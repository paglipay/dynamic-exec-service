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
    "plugins.mongodb_plugin": {
        "class": "MongoDBPlugin",
        "methods": [
            "ping",
            "list_collections",
            "create_document",
            "create_documents",
            "get_document_by_id",
            "find_documents",
            "count_documents",
            "update_documents",
            "replace_document",
            "delete_documents",
            "distinct_values",
            "aggregate_documents",
            "create_index",
            "list_indexes",
            "find_index",
            "create_or_replace_index",
            "drop_index",
            "create_text_index",
            "text_search",
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
    "plugins.system_tools.word_plugin": {
        "class": "WordPlugin",
        "methods": [
            "create_document",
            "inspect_document",
            "replace_text",
            "add_table",
            "export_pdf",
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
            # Messaging — post_message handles both plain text and Block Kit blocks
            "post_message",
            # Modals
            "open_modal",
            "request_modal_with_button",
            # File upload — upload_content for generated strings; upload_local_file for disk files
            "upload_content",
            "upload_local_file",
            # File retrieval from Slack / MongoDB
            "get_file",
            "get_file_exif",
            "backfill_exif",
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
            "clear_conversation_history",
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
    "plugins.integrations.web_search_plugin": {
        "class": "WebSearchPlugin",
        "methods": [
            "web_search",
            "search_near_address",
            "search_image_context",
        ],
    },
    "plugins.system_tools.media_storage_plugin": {
        "class": "MediaStoragePlugin",
        "methods": [
            # Browsing — returns download_url alongside metadata
            "list_files",
            # Zipping — rename_zip builds the archive from a staging session;
            # zip_files bundles already-stored files without renaming
            "rename_zip",
            "zip_files",
            # Staging inspection (read-only) — for checking what's in a session
            "list_staged",
        ],
    },
    "plugins.system_tools.file_reader_plugin": {
        "class": "FileReaderPlugin",
        "methods": [
            "list_directory",
            "read_text_file",
            "read_pdf_text",
            "read_docx_text",
            "parse_csv_tsv",
            "summarize_excel",
            "read_image_for_vision",
            "read_image_gps",
        ],
    },
    "plugins.system_tools.image_processing_plugin": {
        "class": "ImageProcessingPlugin",
        "methods": [
            "geocode_address",
            "reverse_geocode",
            "get_lat_lon",
            "find_nearest_site",
            "detect_objects",
            "classify_project",
            "process_and_store",
            "tag_image",
            "scan_folder",
        ],
    },
}