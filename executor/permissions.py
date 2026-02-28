"""Whitelist-based permission checks for dynamic execution."""

from config import ALLOWED_MODULES


def validate_module(module_name: str) -> None:
    """Ensure module is allowed."""
    if module_name not in ALLOWED_MODULES:
        raise ValueError("Module is not allowed")


def validate_class(module_name: str, class_name: str) -> None:
    """Ensure class matches allowed class for module."""
    validate_module(module_name)
    allowed_class = ALLOWED_MODULES[module_name]["class"]
    if class_name != allowed_class:
        raise ValueError("Class is not allowed for this module")


def validate_method(module_name: str, method_name: str) -> None:
    """Ensure method is allowed for module."""
    validate_module(module_name)
    allowed_methods = ALLOWED_MODULES[module_name]["methods"]
    if method_name not in allowed_methods:
        raise ValueError("Method is not allowed for this module")


def validate_request(module_name: str, class_name: str, method_name: str) -> None:
    """Validate module, class, and method against whitelist."""
    validate_class(module_name, class_name)
    validate_method(module_name, method_name)
