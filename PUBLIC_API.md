# Public API Reference

## Main Class

### WebSocketConnectionManager

```python
class WebSocketConnectionManager:
    """Manages WebSocket connections with lifecycle and heartbeat support."""

    def __init__(self, config: Optional[WebSocketConfig] = None) -> None:
        """Initialize manager with optional custom configuration."""

    async def start(self) -> None:
        """Start background tasks (heartbeat and token cleanup).
        Call during application startup."""

    async def connect(
        self,
        client_id: str,
        websocket: WebSocket,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Register a new WebSocket connection.

        Raises:
            ConnectionLimitExceededError: If max connections limit reached
            ConnectionNotFoundError: If client_id is empty
        """

    async def disconnect(self, client_id: str) -> None:
        """Gracefully disconnect a client.

        Raises:
            ConnectionNotFoundError: If client not found
        """

    async def send_to_client(
        self,
        client_id: str,
        message: str | dict,
    ) -> bool:
        """Send message to specific client.

        Returns:
            True if successful, False if client not connected
        """

    async def broadcast(
        self,
        message: str | dict,
        exclude_client: Optional[str] = None,
    ) -> BroadcastResult:
        """Broadcast message to all clients.

        Returns:
            BroadcastResult with delivery statistics

        Raises:
            BroadcastFailedError: If all deliveries failed
        """

    def get_active_connections(self) -> list[str]:
        """Get list of connected client IDs."""

    def get_connection_count(self) -> int:
        """Get number of active connections."""

    def generate_reconnect_token(self, client_id: str) -> str:
        """Generate a reconnection token for a client."""

    async def validate_reconnect_token(self, token: str) -> Optional[str]:
        """Validate token and return associated client_id.

        Returns:
            Client ID if valid, None otherwise

        Raises:
            TokenExpiredError: If token TTL exceeded
            InvalidTokenError: If token invalid or tampered
        """

    async def handle_pong(self, client_id: str) -> None:
        """Handle pong response from client during heartbeat."""

    async def graceful_shutdown(self, timeout: float = 30.0) -> ShutdownReport:
        """Shutdown manager and close all connections.
        Call during application shutdown."""
```

---

## Configuration

### WebSocketConfig

```python
@dataclass
class WebSocketConfig:
    """Configuration for WebSocket connection manager.

    Attributes:
        heartbeat_interval: Seconds between heartbeat pings (default: 30.0)
        heartbeat_timeout: Seconds to wait for pong response (default: 60.0)
        heartbeat_type: Type of heartbeat - 'ping' or 'custom' (default: 'ping')
        max_connections: Maximum concurrent connections (default: None = unlimited)
        max_message_size: Maximum message size in bytes (default: 1MB)
        reconnect_token_ttl: Time-to-live for tokens (default: 1 hour)
        enable_reconnect: Enable token support (default: True)
        compression: WebSocket compression mode (default: DEFLATE)
        log_level: Logging level DEBUG/INFO/WARNING/ERROR/CRITICAL (default: INFO)
        enable_metrics: Collect metrics (default: False)
    """

    heartbeat_interval: float = 30.0
    heartbeat_timeout: float = 60.0
    heartbeat_type: str = "ping"
    max_connections: Optional[int] = None
    max_message_size: int = 1048576
    reconnect_token_ttl: timedelta = timedelta(hours=1)
    enable_reconnect: bool = True
    compression: CompressionMode = CompressionMode.DEFLATE
    log_level: str = "INFO"
    enable_metrics: bool = False
```

### BroadcastResult

```python
@dataclass
class BroadcastResult:
    """Result of a broadcast operation.

    Attributes:
        total: Total number of recipients targeted
        succeeded: Number of successful message deliveries
        failed: Number of failed message deliveries
        errors: List of (client_id, error_message) tuples for failures
    """

    total: int
    succeeded: int
    failed: int
    errors: list[tuple[str, str]] = field(default_factory=list)
```

### ShutdownReport

```python
@dataclass
class ShutdownReport:
    """Report from graceful shutdown.

    Attributes:
        closed_count: Number of connections successfully closed
        failed_count: Number of connections that failed to close
        duration_seconds: Total time taken for shutdown
    """

    closed_count: int
    failed_count: int
    duration_seconds: float
```

---

## Exceptions

```python
from fastapi_websocket_stabilizer import (
    WebSocketStabilizerError,           # Base exception
    ConnectionNotFoundError,             # Client not found or empty ID
    ConnectionLimitExceededError,        # Max connections reached
    ConnectionTimeoutError,              # Heartbeat timeout
    TokenExpiredError,                   # Token TTL exceeded
    InvalidTokenError,                   # Token invalid or tampered
    BroadcastFailedError,                # Broadcast failed for all clients
    ShutdownError,                       # Shutdown error
)
```

---

## Logging Utilities

### get_logger

```python
def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for the WebSocket stabilizer.

    Args:
        name: Logger name (typically __name__ from module)

    Returns:
        Configured logger instance
    """
```

### configure_logging

```python
def configure_logging(level: str = "INFO") -> None:
    """Configure logging for the WebSocket stabilizer.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
```

### StructuredLogger

```python
class StructuredLogger:
    """Structured logging helper for cloud-friendly log output."""

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize structured logger."""

    def log_connection(
        self,
        event: str,
        client_id: str,
        **kwargs: Any,
    ) -> None:
        """Log connection lifecycle event."""

    def log_broadcast(
        self,
        total: int,
        succeeded: int,
        failed: int,
        **kwargs: Any,
    ) -> None:
        """Log broadcast operation result."""

    def log_heartbeat(
        self,
        event: str,
        client_id: str,
        **kwargs: Any,
    ) -> None:
        """Log heartbeat-related event."""

    def log_token(
        self,
        event: str,
        client_id: str,
        **kwargs: Any,
    ) -> None:
        """Log token-related event."""

    def log_error(
        self,
        error_type: str,
        client_id: str,
        error_message: str,
        **kwargs: Any,
    ) -> None:
        """Log error event."""
```

---

## Enums

### CompressionMode

```python
class CompressionMode(str, Enum):
    """WebSocket compression modes."""
    DEFLATE = "deflate"
    NONE = "none"
```

---

## Typical Usage Pattern

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi_websocket_stabilizer import (
    WebSocketConnectionManager,
    WebSocketConfig,
)

# Configure
config = WebSocketConfig(
    heartbeat_interval=30.0,
    heartbeat_timeout=60.0,
    max_connections=1000,
)

manager = WebSocketConnectionManager(config)

# Setup lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    await manager.start()
    yield
    await manager.graceful_shutdown()

app = FastAPI(lifespan=lifespan)

# Use in endpoint
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await websocket.accept()

    try:
        # Register connection
        await manager.connect(client_id, websocket)

        # Handle messages
        while True:
            data = await websocket.receive_text()

            # Handle heartbeat
            if data == "pong":
                await manager.handle_pong(client_id)
                continue

            # Broadcast
            await manager.broadcast(data, exclude_client=client_id)

    except WebSocketDisconnect:
        await manager.disconnect(client_id)
```

---

## Version

```python
from fastapi_websocket_stabilizer import __version__

print(__version__)  # "0.1.0"
```

---

## Exported Names

All public API symbols are available from the main package:

```python
from fastapi_websocket_stabilizer import (
    # Main class
    WebSocketConnectionManager,

    # Configuration
    WebSocketConfig,
    BroadcastResult,
    ShutdownReport,
    CompressionMode,

    # Exceptions
    WebSocketStabilizerError,
    ConnectionNotFoundError,
    ConnectionLimitExceededError,
    ConnectionTimeoutError,
    TokenExpiredError,
    InvalidTokenError,
    BroadcastFailedError,
    ShutdownError,

    # Logging
    get_logger,
    configure_logging,
    StructuredLogger,

    # Version
    __version__,
)
```
