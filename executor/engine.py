"""JSON execution engine for dynamic plugin instantiation and method calls."""

from importlib import import_module
from typing import Any

from executor.permissions import validate_class, validate_method


class JSONExecutor:
    """Executes whitelisted class methods from JSON-driven requests."""

    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}

    def instantiate(
        self,
        module_name: str,
        class_name: str,
        constructor_args: dict[str, Any],
    ) -> Any:
        """Import a module, instantiate a class, and store the instance by module."""
        validate_class(module_name, class_name)

        module = import_module(module_name)
        class_object = getattr(module, class_name, None)
        if class_object is None:
            raise ValueError("Requested class was not found")

        instance = class_object(**constructor_args)
        self._instances[module_name] = instance
        return instance

    def call_method(
        self,
        module_name: str,
        method_name: str,
        args: list[Any],
    ) -> Any:
        """Call an allowed method on a previously-instantiated module instance."""
        validate_method(module_name, method_name)

        if module_name not in self._instances:
            raise ValueError("Module instance is not initialized")

        instance = self._instances[module_name]
        method = getattr(instance, method_name, None)
        if method is None or not callable(method):
            raise ValueError("Requested method was not found")

        return method(*args)
