class BackendError(Exception):
    """Base backend exception."""


class DangerousSQLError(BackendError):
    """Raised when SQL contains dangerous operations."""
