"""FastAPI WebSocket Stabilizer: Production-ready WebSocket handling for FastAPI applications."""

from .config import (
    BroadcastResult,
    CompressionMode,
    MemoryEvictionPolicy,
    ShutdownReport,
    WebSocketConfig,
)
from .exceptions import (
    BroadcastFailedError,
    ConnectionLimitExceededError,
    ConnectionNotFoundError,
    ConnectionTimeoutError,
    InvalidTokenError,
    MemoryLimitExceededError,
    ShutdownError,
    TokenExpiredError,
    WebSocketStabilizerError,
)
from .logging_utils import StructuredLogger, configure_logging, get_logger
from .manager import WebSocketConnectionManager

__version__ = "1.1.0"

__all__ = [
    # Main class
    "WebSocketConnectionManager",
    # Configuration
    "WebSocketConfig",
    "BroadcastResult",
    "ShutdownReport",
    "CompressionMode",
    "MemoryEvictionPolicy",
    # Exceptions
    "WebSocketStabilizerError",
    "ConnectionNotFoundError",
    "ConnectionLimitExceededError",
    "ConnectionTimeoutError",
    "TokenExpiredError",
    "InvalidTokenError",
    "BroadcastFailedError",
    "ShutdownError",
    "MemoryLimitExceededError",
    # Logging
    "get_logger",
    "configure_logging",
    "StructuredLogger",
]
