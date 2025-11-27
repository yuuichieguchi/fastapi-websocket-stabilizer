"""FastAPI WebSocket Stabilizer: Production-ready WebSocket handling for FastAPI applications."""

from .config import BroadcastResult, CompressionMode, ShutdownReport, WebSocketConfig
from .exceptions import (
    BroadcastFailedError,
    ConnectionLimitExceededError,
    ConnectionNotFoundError,
    ConnectionTimeoutError,
    InvalidTokenError,
    ShutdownError,
    TokenExpiredError,
    WebSocketStabilizerError,
)
from .logging_utils import StructuredLogger, configure_logging, get_logger
from .manager import WebSocketConnectionManager

__version__ = "0.1.0"

__all__ = [
    # Main class
    "WebSocketConnectionManager",
    # Configuration
    "WebSocketConfig",
    "BroadcastResult",
    "ShutdownReport",
    "CompressionMode",
    # Exceptions
    "WebSocketStabilizerError",
    "ConnectionNotFoundError",
    "ConnectionLimitExceededError",
    "ConnectionTimeoutError",
    "TokenExpiredError",
    "InvalidTokenError",
    "BroadcastFailedError",
    "ShutdownError",
    # Logging
    "get_logger",
    "configure_logging",
    "StructuredLogger",
]
