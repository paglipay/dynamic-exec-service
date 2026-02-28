"""Generated plugin module."""


class GeneratedMathPlugin:
    """Auto-generated plugin class."""

    def __init__(self) -> None:
        pass

    def multiply(self, x, y):
        return x * y

    def add(self, x, y):
        return x + y

    def subtract(self, x, y):
        return x - y

    def divide(self, x, y):
        return x / y if y != 0 else 'Cannot divide by zero'

    def greet(self, name):
        return f'Hello, {name}'
