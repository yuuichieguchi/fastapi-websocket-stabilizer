"""Configuration models for WebSocket stabilizer."""

from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Optional


class CompressionMode(str, Enum):
    """WebSocket compression modes."""

    DEFLATE = "deflate"
    NONE = "none"


class MemoryEvictionPolicy(str, Enum):
    """Memory eviction policies for connection management."""

    LRU = "lru"
    FIFO = "fifo"
    OLDEST = "oldest"


@dataclass
class WebSocketConfig:
    """Configuration for WebSocket connection manager.

    Attributes:
        heartbeat_interval: Seconds between heartbeat pings. Default: 30.0
        heartbeat_timeout: Seconds to wait for pong response. Default: 60.0
        heartbeat_type: Type of heartbeat mechanism ('ping' or 'custom'). Default: 'ping'
        max_connections: Maximum number of concurrent connections. None = unlimited. Default: None
        max_message_size: Maximum message size in bytes. Default: 1MB
        reconnect_token_ttl: Time-to-live for reconnection tokens. Default: 1 hour
        enable_reconnect: Whether to enable reconnection token support. Default: True
        compression: WebSocket compression mode. Default: DEFLATE
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR). Default: 'INFO'
        enable_metrics: Whether to collect connection metrics. Default: False
    """

    # Heartbeat configuration
    heartbeat_interval: float = 30.0
    heartbeat_timeout: float = 60.0
    heartbeat_type: str = "ping"

    # Connection limits
    max_connections: Optional[int] = None
    max_message_size: int = 1024 * 1024  # 1MB

    # Reconnection
    reconnect_token_ttl: timedelta = field(default_factory=lambda: timedelta(hours=1))
    enable_reconnect: bool = True

    # Compression
    compression: CompressionMode = CompressionMode.DEFLATE

    # Logging and metrics
    log_level: str = "INFO"
    enable_metrics: bool = False

    # Sharding configuration
    num_shards: int = 16

    # Concurrency limits
    max_concurrent_cleanup: int = 100
    max_concurrent_broadcast: int = 1000

    # Memory management
    memory_check_interval: float = 60.0
    memory_eviction_policy: MemoryEvictionPolicy = MemoryEvictionPolicy.LRU
    max_memory_per_connection: Optional[int] = None
    max_total_memory: Optional[int] = None

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if self.heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive")
        if self.heartbeat_timeout <= 0:
            raise ValueError("heartbeat_timeout must be positive")
        if self.max_connections is not None and self.max_connections <= 0:
            raise ValueError("max_connections must be positive or None")
        if self.max_message_size <= 0:
            raise ValueError("max_message_size must be positive")
        if self.reconnect_token_ttl.total_seconds() <= 0:
            raise ValueError("reconnect_token_ttl must be positive")
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError("log_level must be a valid logging level")

        # Sharding validation
        if self.num_shards <= 0:
            raise ValueError("num_shards must be positive")

        # Concurrency limits validation
        if self.max_concurrent_cleanup <= 0:
            raise ValueError("max_concurrent_cleanup must be positive")
        if self.max_concurrent_broadcast <= 0:
            raise ValueError("max_concurrent_broadcast must be positive")

        # Memory management validation
        if self.memory_check_interval <= 0:
            raise ValueError("memory_check_interval must be positive")

        # Handle string-to-enum conversion for memory_eviction_policy
        if isinstance(self.memory_eviction_policy, str):
            try:
                object.__setattr__(
                    self,
                    "memory_eviction_policy",
                    MemoryEvictionPolicy(self.memory_eviction_policy.lower()),
                )
            except ValueError:
                raise ValueError(
                    f"memory_eviction_policy must be one of: {[e.value for e in MemoryEvictionPolicy]}"
                )
        elif not isinstance(self.memory_eviction_policy, MemoryEvictionPolicy):
            raise ValueError(
                f"memory_eviction_policy must be one of: {[e.value for e in MemoryEvictionPolicy]}"
            )

        if self.max_memory_per_connection is not None and self.max_memory_per_connection <= 0:
            raise ValueError("max_memory_per_connection must be positive")
        if self.max_total_memory is not None and self.max_total_memory <= 0:
            raise ValueError("max_total_memory must be positive")


@dataclass
class BroadcastResult:
    """Result of a broadcast operation.

    Attributes:
        total: Total number of recipients targeted.
        succeeded: Number of successful message deliveries.
        failed: Number of failed message deliveries.
        errors: List of (client_id, error_message) tuples for failures.
    """

    total: int
    succeeded: int
    failed: int
    errors: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ShutdownReport:
    """Report from graceful shutdown.

    Attributes:
        closed_count: Number of connections successfully closed.
        failed_count: Number of connections that failed to close.
        duration_seconds: Total time taken for shutdown.
    """

    closed_count: int
    failed_count: int
    duration_seconds: float
