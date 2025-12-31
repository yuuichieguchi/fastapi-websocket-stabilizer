"""Custom exception types for WebSocket stabilization."""


class WebSocketStabilizerError(Exception):
    """Base exception for all WebSocket stabilizer errors."""

    pass


class ConnectionNotFoundError(WebSocketStabilizerError):
    """Raised when attempting to access a non-existent connection."""

    pass


class ConnectionLimitExceededError(WebSocketStabilizerError):
    """Raised when max connection limit is reached."""

    pass


class ConnectionTimeoutError(WebSocketStabilizerError):
    """Raised when a connection times out during heartbeat."""

    pass


class TokenExpiredError(WebSocketStabilizerError):
    """Raised when attempting to use an expired reconnection token."""

    pass


class InvalidTokenError(WebSocketStabilizerError):
    """Raised when a reconnection token is invalid or tampered with."""

    pass


class BroadcastFailedError(WebSocketStabilizerError):
    """Raised when broadcasting encounters critical failures."""

    pass


class ShutdownError(WebSocketStabilizerError):
    """Raised when graceful shutdown encounters errors."""

    pass


class MemoryLimitExceededError(WebSocketStabilizerError):
    """Raised when memory limit is exceeded for a connection or total."""

    pass
