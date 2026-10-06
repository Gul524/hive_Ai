"""Errors that callers can handle without inspecting implementation details."""


class HiveError(Exception):
    """Base class for expected Hive failures."""


class ConfigurationError(HiveError):
    """Configuration is missing, unreadable, or invalid."""


class StateError(HiveError):
    """Persistent state could not be opened or initialized."""
