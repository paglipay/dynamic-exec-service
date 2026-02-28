"""Sample plugin module."""


class SampleModule:
    """Example plugin used for arithmetic and simple processing."""

    def __init__(self, name: str, data: str) -> None:
        self.name = name
        self.data = data

    def add(self, x: int, y: int) -> int:
        """Return the sum of two integers."""
        return x + y

    def process(self) -> str:
        """Return a processed representation of the stored values."""
        return f"{self.name}: {self.data}".strip()
