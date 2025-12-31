"""Tests for WebSocket connection manager."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi_websocket_stabilizer import (
    BroadcastResult,
    ConnectionLimitExceededError,
    ConnectionNotFoundError,
    InvalidTokenError,
    MemoryEvictionPolicy,
    MemoryLimitExceededError,
    ShutdownReport,
    TokenExpiredError,
    WebSocketConfig,
    WebSocketConnectionManager,
)


@pytest.fixture
def manager() -> WebSocketConnectionManager:
    """Create a manager instance for testing."""
    return WebSocketConnectionManager()


@pytest.fixture
def config() -> WebSocketConfig:
    """Create a config instance for testing."""
    return WebSocketConfig(heartbeat_interval=0.1, heartbeat_timeout=0.2)


@pytest.fixture
def mock_websocket() -> AsyncMock:
    """Create a mock WebSocket for testing."""
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


class TestConnectionManager:
    """Test WebSocketConnectionManager basic operations."""

    @pytest.mark.asyncio
    async def test_should_track_connection_when_connect_called(
        self, manager: WebSocketConnectionManager, mock_websocket: AsyncMock
    ) -> None:
        """Test that connect() adds connection to tracking."""
        client_id = "test_client_1"
        await manager.connect(client_id, mock_websocket)

        assert client_id in manager.get_active_connections()
        assert manager.get_connection_count() == 1

    @pytest.mark.asyncio
    async def test_should_raise_error_on_empty_client_id(
        self, manager: WebSocketConnectionManager, mock_websocket: AsyncMock
    ) -> None:
        """Test that empty client_id raises ConnectionNotFoundError."""
        with pytest.raises(ConnectionNotFoundError):
            await manager.connect("", mock_websocket)

    @pytest.mark.asyncio
    async def test_should_replace_existing_connection(
        self, manager: WebSocketConnectionManager, mock_websocket: AsyncMock
    ) -> None:
        """Test that reconnecting with same client_id replaces old connection."""
        client_id = "test_client_1"
        old_ws = AsyncMock()
        old_ws.close = AsyncMock()

        await manager.connect(client_id, old_ws)
        new_ws = AsyncMock()
        new_ws.close = AsyncMock()
        await manager.connect(client_id, new_ws)

        assert manager.get_connection_count() == 1
        # Old connection should be closed
        old_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_remove_connection_when_disconnect_called(
        self, manager: WebSocketConnectionManager, mock_websocket: AsyncMock
    ) -> None:
        """Test that disconnect() removes connection from tracking."""
        client_id = "test_client_1"
        await manager.connect(client_id, mock_websocket)
        await manager.disconnect(client_id)

        assert client_id not in manager.get_active_connections()
        assert manager.get_connection_count() == 0
        mock_websocket.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_raise_error_when_disconnecting_nonexistent_client(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that disconnect() raises error for unknown client."""
        with pytest.raises(ConnectionNotFoundError):
            await manager.disconnect("nonexistent_client")

    @pytest.mark.asyncio
    async def test_should_send_text_message_to_client(
        self, manager: WebSocketConnectionManager, mock_websocket: AsyncMock
    ) -> None:
        """Test sending text message to specific client."""
        client_id = "test_client_1"
        await manager.connect(client_id, mock_websocket)

        success = await manager.send_to_client(client_id, "Hello")
        assert success is True
        mock_websocket.send_text.assert_called_with("Hello")

    @pytest.mark.asyncio
    async def test_should_send_json_message_to_client(
        self, manager: WebSocketConnectionManager, mock_websocket: AsyncMock
    ) -> None:
        """Test sending JSON message to specific client."""
        client_id = "test_client_1"
        await manager.connect(client_id, mock_websocket)

        message = {"type": "chat", "content": "Hello"}
        success = await manager.send_to_client(client_id, message)
        assert success is True
        mock_websocket.send_json.assert_called_with(message)

    @pytest.mark.asyncio
    async def test_should_return_false_when_sending_to_nonexistent_client(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that send_to_client returns False for unknown client."""
        success = await manager.send_to_client("nonexistent", "Hello")
        assert success is False

    @pytest.mark.asyncio
    async def test_should_disconnect_client_on_send_failure(
        self, manager: WebSocketConnectionManager, mock_websocket: AsyncMock
    ) -> None:
        """Test that failed send disconnects the client."""
        client_id = "test_client_1"
        await manager.connect(client_id, mock_websocket)

        mock_websocket.send_text.side_effect = RuntimeError("Connection error")
        success = await manager.send_to_client(client_id, "Hello")

        assert success is False
        assert client_id not in manager.get_active_connections()

    @pytest.mark.asyncio
    async def test_should_broadcast_text_message_to_all_clients(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test broadcasting text message to all clients."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws3 = AsyncMock()

        await manager.connect("client_1", ws1)
        await manager.connect("client_2", ws2)
        await manager.connect("client_3", ws3)

        result = await manager.broadcast("broadcast message")

        assert result.total == 3
        assert result.succeeded == 3
        assert result.failed == 0
        ws1.send_text.assert_called_with("broadcast message")
        ws2.send_text.assert_called_with("broadcast message")
        ws3.send_text.assert_called_with("broadcast message")

    @pytest.mark.asyncio
    async def test_should_broadcast_json_message_to_all_clients(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test broadcasting JSON message to all clients."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        await manager.connect("client_1", ws1)
        await manager.connect("client_2", ws2)

        message = {"type": "notification", "content": "test"}
        result = await manager.broadcast(message)

        assert result.succeeded == 2
        ws1.send_json.assert_called_with(message)
        ws2.send_json.assert_called_with(message)

    @pytest.mark.asyncio
    async def test_should_exclude_client_from_broadcast(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that exclude_client prevents sending to specific client."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws3 = AsyncMock()

        await manager.connect("client_1", ws1)
        await manager.connect("client_2", ws2)
        await manager.connect("client_3", ws3)

        result = await manager.broadcast("message", exclude_client="client_2")

        assert result.total == 3
        ws1.send_text.assert_called_with("message")
        ws2.send_text.assert_not_called()
        ws3.send_text.assert_called_with("message")

    @pytest.mark.asyncio
    async def test_should_track_broadcast_failures(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that broadcast tracks failed deliveries."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws3 = AsyncMock()

        ws2.send_text.side_effect = RuntimeError("Connection lost")

        await manager.connect("client_1", ws1)
        await manager.connect("client_2", ws2)
        await manager.connect("client_3", ws3)

        result = await manager.broadcast("message")

        assert result.succeeded == 2
        assert result.failed == 1
        assert len(result.errors) == 1
        assert result.errors[0][0] == "client_2"

    @pytest.mark.asyncio
    async def test_should_return_active_connections_snapshot(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that get_active_connections returns current connections."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        await manager.connect("client_1", ws1)
        await manager.connect("client_2", ws2)

        connections = manager.get_active_connections()

        assert len(connections) == 2
        assert "client_1" in connections
        assert "client_2" in connections


class TestReconnectionTokens:
    """Test reconnection token functionality."""

    @pytest.mark.asyncio
    async def test_should_generate_valid_reconnect_token(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that generated token is valid format."""
        client_id = "test_client_1"
        token = manager.generate_reconnect_token(client_id)

        assert isinstance(token, str)
        parts = token.split(".")
        assert len(parts) == 4  # timestamp, nonce, client_id, signature

    @pytest.mark.asyncio
    async def test_should_validate_valid_reconnect_token(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that valid token passes validation."""
        client_id = "test_client_1"
        token = manager.generate_reconnect_token(client_id)

        result = await manager.validate_reconnect_token(token)

        assert result == client_id

    @pytest.mark.asyncio
    async def test_should_reject_invalid_token_format(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that malformed token is rejected."""
        with pytest.raises(InvalidTokenError):
            await manager.validate_reconnect_token("invalid.token")

    @pytest.mark.asyncio
    async def test_should_reject_nonexistent_token(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that unknown token is rejected."""
        with pytest.raises(InvalidTokenError):
            await manager.validate_reconnect_token("abc.def.ghi.jkl")

    @pytest.mark.asyncio
    async def test_should_reject_tampered_token(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that tampered token signature is rejected."""
        client_id = "test_client_1"
        token = manager.generate_reconnect_token(client_id)

        # Tamper with the token by changing part of the signature
        parts = token.split(".")
        tampered = ".".join(parts[:3]) + ".corrupted"

        with pytest.raises(InvalidTokenError):
            await manager.validate_reconnect_token(tampered)

    @pytest.mark.asyncio
    async def test_should_reject_expired_token(
        self, config: WebSocketConfig, mock_websocket: AsyncMock
    ) -> None:
        """Test that expired token is rejected."""
        config.reconnect_token_ttl = __import__("datetime").timedelta(seconds=0.01)
        manager = WebSocketConnectionManager(config)

        client_id = "test_client_1"
        token = manager.generate_reconnect_token(client_id)

        # Wait for token to expire
        await asyncio.sleep(0.05)

        with pytest.raises(TokenExpiredError):
            await manager.validate_reconnect_token(token)

    @pytest.mark.asyncio
    async def test_should_mark_token_as_used_after_validation(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that token cannot be reused."""
        client_id = "test_client_1"
        token = manager.generate_reconnect_token(client_id)

        await manager.validate_reconnect_token(token)

        # Try to use same token again
        with pytest.raises(InvalidTokenError):
            await manager.validate_reconnect_token(token)


class TestGracefulShutdown:
    """Test graceful shutdown functionality."""

    @pytest.mark.asyncio
    async def test_should_close_all_connections_on_shutdown(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that shutdown closes all connections."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws3 = AsyncMock()

        await manager.connect("client_1", ws1)
        await manager.connect("client_2", ws2)
        await manager.connect("client_3", ws3)

        report = await manager.graceful_shutdown()

        assert report.closed_count == 3
        assert report.failed_count == 0
        ws1.close.assert_called()
        ws2.close.assert_called()
        ws3.close.assert_called()

    @pytest.mark.asyncio
    async def test_should_report_failed_closures(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that shutdown reports failed connection closures."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        ws2.close.side_effect = RuntimeError("Close failed")

        await manager.connect("client_1", ws1)
        await manager.connect("client_2", ws2)

        report = await manager.graceful_shutdown()

        assert report.closed_count == 1
        assert report.failed_count == 1

    @pytest.mark.asyncio
    async def test_should_remove_all_connections_on_shutdown(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that all connections are removed after shutdown."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        await manager.connect("client_1", ws1)
        await manager.connect("client_2", ws2)

        await manager.graceful_shutdown()

        assert manager.get_connection_count() == 0
        assert len(manager.get_active_connections()) == 0

    @pytest.mark.asyncio
    async def test_should_return_shutdown_report(
        self, manager: WebSocketConnectionManager
    ) -> None:
        """Test that shutdown returns valid report."""
        ws1 = AsyncMock()
        await manager.connect("client_1", ws1)

        report = await manager.graceful_shutdown()

        assert isinstance(report, ShutdownReport)
        assert report.closed_count >= 0
        assert report.failed_count >= 0
        assert report.duration_seconds >= 0


class TestConnectionLimits:
    """Test connection limit enforcement."""

    @pytest.mark.asyncio
    async def test_should_enforce_max_connections_limit(
        self, mock_websocket: AsyncMock
    ) -> None:
        """Test that max_connections limit is enforced."""
        config = WebSocketConfig(max_connections=2)
        manager = WebSocketConnectionManager(config)

        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws3 = AsyncMock()

        await manager.connect("client_1", ws1)
        await manager.connect("client_2", ws2)

        with pytest.raises(ConnectionLimitExceededError):
            await manager.connect("client_3", ws3)

    @pytest.mark.asyncio
    async def test_should_allow_reconnection_within_limit(
        self, mock_websocket: AsyncMock
    ) -> None:
        """Test that reconnection doesn't count against limit."""
        config = WebSocketConfig(max_connections=1)
        manager = WebSocketConnectionManager(config)

        ws1 = AsyncMock()
        ws1.close = AsyncMock()
        ws2 = AsyncMock()

        await manager.connect("client_1", ws1)
        await manager.connect("client_1", ws2)

        assert manager.get_connection_count() == 1


class TestConfigValidation:
    """Test configuration validation."""

    def test_should_validate_positive_heartbeat_interval(self) -> None:
        """Test that heartbeat_interval must be positive."""
        with pytest.raises(ValueError):
            WebSocketConfig(heartbeat_interval=-1)

    def test_should_validate_positive_heartbeat_timeout(self) -> None:
        """Test that heartbeat_timeout must be positive."""
        with pytest.raises(ValueError):
            WebSocketConfig(heartbeat_timeout=0)

    def test_should_validate_positive_max_connections(self) -> None:
        """Test that max_connections must be positive if set."""
        with pytest.raises(ValueError):
            WebSocketConfig(max_connections=0)

    def test_should_validate_log_level(self) -> None:
        """Test that log_level must be valid."""
        with pytest.raises(ValueError):
            WebSocketConfig(log_level="INVALID")

    # ==================== Phase 1: Sharding Config ====================

    def test_should_validate_positive_num_shards(self) -> None:
        """
        Given: A config with non-positive num_shards value
        When: WebSocketConfig is instantiated
        Then: ValueError should be raised
        """
        with pytest.raises(ValueError, match="num_shards must be positive"):
            WebSocketConfig(num_shards=0)
        with pytest.raises(ValueError, match="num_shards must be positive"):
            WebSocketConfig(num_shards=-1)

    # ==================== Phase 1: Concurrency Limits ====================

    def test_should_validate_positive_max_concurrent_cleanup(self) -> None:
        """
        Given: A config with non-positive max_concurrent_cleanup value
        When: WebSocketConfig is instantiated
        Then: ValueError should be raised
        """
        with pytest.raises(ValueError, match="max_concurrent_cleanup must be positive"):
            WebSocketConfig(max_concurrent_cleanup=0)
        with pytest.raises(ValueError, match="max_concurrent_cleanup must be positive"):
            WebSocketConfig(max_concurrent_cleanup=-5)

    def test_should_validate_positive_max_concurrent_broadcast(self) -> None:
        """
        Given: A config with non-positive max_concurrent_broadcast value
        When: WebSocketConfig is instantiated
        Then: ValueError should be raised
        """
        with pytest.raises(ValueError, match="max_concurrent_broadcast must be positive"):
            WebSocketConfig(max_concurrent_broadcast=0)
        with pytest.raises(ValueError, match="max_concurrent_broadcast must be positive"):
            WebSocketConfig(max_concurrent_broadcast=-10)

    # ==================== Phase 1: Memory Management Config ====================

    def test_should_validate_positive_memory_check_interval(self) -> None:
        """
        Given: A config with non-positive memory_check_interval value
        When: WebSocketConfig is instantiated
        Then: ValueError should be raised
        """
        with pytest.raises(ValueError, match="memory_check_interval must be positive"):
            WebSocketConfig(memory_check_interval=0)
        with pytest.raises(ValueError, match="memory_check_interval must be positive"):
            WebSocketConfig(memory_check_interval=-1.0)

    def test_should_validate_memory_eviction_policy(self) -> None:
        """
        Given: A config with invalid memory_eviction_policy value
        When: WebSocketConfig is instantiated
        Then: ValueError should be raised
        """
        with pytest.raises(ValueError, match="memory_eviction_policy"):
            WebSocketConfig(memory_eviction_policy="INVALID_POLICY")

    def test_should_allow_none_memory_limits(self) -> None:
        """
        Given: A config with None values for memory limits
        When: WebSocketConfig is instantiated
        Then: No error should be raised (None means unlimited)
        """
        # Should not raise any exception
        config = WebSocketConfig(
            max_memory_per_connection=None,
            max_total_memory=None,
        )
        assert config.max_memory_per_connection is None
        assert config.max_total_memory is None

    def test_should_validate_positive_max_memory_per_connection(self) -> None:
        """
        Given: A config with non-positive max_memory_per_connection value
        When: WebSocketConfig is instantiated
        Then: ValueError should be raised
        """
        with pytest.raises(ValueError, match="max_memory_per_connection must be positive"):
            WebSocketConfig(max_memory_per_connection=0)
        with pytest.raises(ValueError, match="max_memory_per_connection must be positive"):
            WebSocketConfig(max_memory_per_connection=-1024)

    def test_should_validate_positive_max_total_memory(self) -> None:
        """
        Given: A config with non-positive max_total_memory value
        When: WebSocketConfig is instantiated
        Then: ValueError should be raised
        """
        with pytest.raises(ValueError, match="max_total_memory must be positive"):
            WebSocketConfig(max_total_memory=0)
        with pytest.raises(ValueError, match="max_total_memory must be positive"):
            WebSocketConfig(max_total_memory=-1024 * 1024)


class TestConnectionShard:
    """Test ConnectionShard model for sharding support."""

    def test_should_create_shard_with_empty_connections(self) -> None:
        """
        Given: A new ConnectionShard is created
        When: No connections have been added
        Then: The shard should have an empty connections dictionary
        """
        from fastapi_websocket_stabilizer._models import ConnectionShard

        shard = ConnectionShard()
        assert hasattr(shard, "connections")
        assert isinstance(shard.connections, dict)
        assert len(shard.connections) == 0

    def test_should_create_shard_with_lock(self) -> None:
        """
        Given: A new ConnectionShard is created
        When: Accessing the lock attribute
        Then: The shard should have an asyncio.Lock instance
        """
        from fastapi_websocket_stabilizer._models import ConnectionShard

        shard = ConnectionShard()
        assert hasattr(shard, "lock")
        assert isinstance(shard.lock, asyncio.Lock)


class TestConnectionMetadataMemory:
    """Test ConnectionMetadata memory estimation functionality."""

    def test_should_estimate_connection_memory_size(
        self, mock_websocket: AsyncMock
    ) -> None:
        """
        Given: A ConnectionMetadata instance with some data
        When: estimate_memory_size() is called
        Then: A positive integer representing estimated bytes should be returned
        """
        from fastapi_websocket_stabilizer._models import ConnectionMetadata

        metadata = ConnectionMetadata(
            client_id="test_client_123",
            websocket=mock_websocket,
            created_at=time.time(),
            metadata={"key": "value", "another": "data"},
            user_data={"user_id": 12345, "preferences": {"theme": "dark"}},
        )

        estimated_size = metadata.estimate_memory_size()

        # Should return a positive integer
        assert isinstance(estimated_size, int)
        assert estimated_size > 0
        # Should account for at least the string length of client_id
        assert estimated_size >= len("test_client_123")


# ==================== Phase 2: Concurrency Limits ====================


class ConcurrencyTracker:
    """Helper class to track concurrent operations for testing."""

    def __init__(self) -> None:
        self.current: int = 0
        self.max_seen: int = 0
        self.lock: asyncio.Lock = asyncio.Lock()

    async def enter(self) -> None:
        """Record entering a concurrent operation."""
        async with self.lock:
            self.current += 1
            self.max_seen = max(self.max_seen, self.current)

    async def exit(self) -> None:
        """Record exiting a concurrent operation."""
        async with self.lock:
            self.current -= 1


class TestConcurrencyLimits:
    """Test semaphore-based concurrency limits for cleanup and broadcast operations.

    These tests verify that:
    1. The manager uses concurrent processing (not sequential for-loop)
    2. Semaphores properly limit the degree of concurrency

    The tests will FAIL until:
    - graceful_shutdown() uses asyncio.gather() with semaphore limiting
    - broadcast() uses asyncio.gather() with semaphore limiting
    """

    @pytest.mark.asyncio
    async def test_should_limit_concurrent_cleanup_operations(self) -> None:
        """
        Given: A manager with max_concurrent_cleanup=2 and 10 connections
        When: graceful_shutdown() is called with concurrent processing
        Then: At most 2 close operations should run concurrently

        This test verifies that the semaphore limits concurrent connection
        closures during shutdown to prevent cleanup storms.

        Expected behavior:
        - Implementation should use asyncio.gather() or similar for parallel closes
        - Semaphore should limit concurrency to max_concurrent_cleanup
        - With 10 connections and limit=2, max_seen should be exactly 2
        """
        # Arrange
        max_concurrent = 2
        num_connections = 10
        config = WebSocketConfig(max_concurrent_cleanup=max_concurrent)
        manager = WebSocketConnectionManager(config)

        tracker = ConcurrencyTracker()

        async def tracked_close(code: int = 1000, reason: str = "") -> None:
            """Mock close that tracks concurrency."""
            await tracker.enter()
            await asyncio.sleep(0.05)  # Delay to ensure overlap detection
            await tracker.exit()

        # Create mock websockets with tracked close
        for i in range(num_connections):
            ws = AsyncMock()
            ws.close = AsyncMock(side_effect=tracked_close)
            await manager.connect(f"client_{i}", ws)

        # Act
        report = await manager.graceful_shutdown()

        # Assert - The implementation must use concurrent processing
        # If max_seen == 1, it means sequential processing (no concurrency)
        # If max_seen > max_concurrent, semaphore is not working
        assert tracker.max_seen > 1, (
            f"Expected concurrent processing (max_seen > 1), "
            f"but saw max_seen={tracker.max_seen}. "
            "Implementation should use asyncio.gather() for parallel closes."
        )
        assert tracker.max_seen <= max_concurrent, (
            f"Expected max {max_concurrent} concurrent closes, "
            f"but saw {tracker.max_seen}. Semaphore is not limiting concurrency."
        )
        assert report.closed_count == num_connections, (
            f"Expected {num_connections} closed connections, "
            f"got {report.closed_count}"
        )

    @pytest.mark.asyncio
    async def test_should_limit_concurrent_broadcast_sends(self) -> None:
        """
        Given: A manager with max_concurrent_broadcast=3 and 10 connections
        When: broadcast() is called with concurrent processing
        Then: At most 3 send operations should run concurrently

        This test verifies that the semaphore limits concurrent message
        sends during broadcast to prevent overwhelming the system.

        Expected behavior:
        - Implementation should use asyncio.gather() or similar for parallel sends
        - Semaphore should limit concurrency to max_concurrent_broadcast
        - With 10 connections and limit=3, max_seen should be exactly 3
        """
        # Arrange
        max_concurrent = 3
        num_connections = 10
        config = WebSocketConfig(max_concurrent_broadcast=max_concurrent)
        manager = WebSocketConnectionManager(config)

        tracker = ConcurrencyTracker()

        async def tracked_send_text(message: str) -> None:
            """Mock send_text that tracks concurrency."""
            await tracker.enter()
            await asyncio.sleep(0.05)  # Delay to ensure overlap detection
            await tracker.exit()

        # Create mock websockets with tracked send_text
        for i in range(num_connections):
            ws = AsyncMock()
            ws.send_text = AsyncMock(side_effect=tracked_send_text)
            ws.close = AsyncMock()
            await manager.connect(f"client_{i}", ws)

        # Act
        result = await manager.broadcast("test message")

        # Assert - The implementation must use concurrent processing
        # If max_seen == 1, it means sequential processing (no concurrency)
        # If max_seen > max_concurrent, semaphore is not working
        assert tracker.max_seen > 1, (
            f"Expected concurrent processing (max_seen > 1), "
            f"but saw max_seen={tracker.max_seen}. "
            "Implementation should use asyncio.gather() for parallel sends."
        )
        assert tracker.max_seen <= max_concurrent, (
            f"Expected max {max_concurrent} concurrent sends, "
            f"but saw {tracker.max_seen}. Semaphore is not limiting concurrency."
        )
        assert result.succeeded == num_connections, (
            f"Expected {num_connections} successful sends, "
            f"got {result.succeeded}"
        )

    @pytest.mark.asyncio
    async def test_should_complete_shutdown_with_many_connections(self) -> None:
        """
        Given: A manager with default config and 100 connections
        When: graceful_shutdown() is called
        Then: All 100 connections should be closed successfully

        This test verifies that semaphore-limited shutdown can handle
        a large number of connections without deadlocks or hangs.
        """
        # Arrange
        num_connections = 100
        config = WebSocketConfig()  # Use default concurrency limits
        manager = WebSocketConnectionManager(config)

        # Create mock websockets
        for i in range(num_connections):
            ws = AsyncMock()
            ws.close = AsyncMock()
            await manager.connect(f"client_{i}", ws)

        assert manager.get_connection_count() == num_connections

        # Act
        report = await manager.graceful_shutdown()

        # Assert
        assert report.closed_count == num_connections, (
            f"Expected {num_connections} closed connections, "
            f"got {report.closed_count}"
        )
        assert report.failed_count == 0, (
            f"Expected 0 failed closures, got {report.failed_count}"
        )
        assert manager.get_connection_count() == 0, (
            "Expected all connections to be removed after shutdown"
        )

    @pytest.mark.asyncio
    async def test_should_complete_broadcast_with_many_connections(self) -> None:
        """
        Given: A manager with default config and 50 connections
        When: broadcast() is called
        Then: All 50 connections should receive the message

        This test verifies that semaphore-limited broadcast can handle
        many connections without deadlocks or hangs.
        """
        # Arrange
        num_connections = 50
        config = WebSocketConfig()  # Use default concurrency limits
        manager = WebSocketConnectionManager(config)

        # Create mock websockets
        for i in range(num_connections):
            ws = AsyncMock()
            ws.send_text = AsyncMock()
            ws.close = AsyncMock()
            await manager.connect(f"client_{i}", ws)

        assert manager.get_connection_count() == num_connections

        # Act
        result = await manager.broadcast("broadcast message to all")

        # Assert
        assert result.total == num_connections, (
            f"Expected total={num_connections}, got {result.total}"
        )
        assert result.succeeded == num_connections, (
            f"Expected {num_connections} successful sends, "
            f"got {result.succeeded}"
        )
        assert result.failed == 0, (
            f"Expected 0 failed sends, got {result.failed}"
        )


# ==================== Phase 3: Connection Pool Sharding ====================


def get_shard_index(client_id: str, num_shards: int) -> int:
    """Helper to calculate expected shard index for a client ID.

    Uses the same hashing algorithm as the manager implementation.

    Args:
        client_id: The client identifier
        num_shards: Number of shards configured

    Returns:
        The shard index (0 to num_shards-1)
    """
    return hash(client_id) % num_shards


class TestSharding:
    """Test Connection Pool Sharding functionality.

    Phase 3 implements sharding of the connection pool to reduce lock contention
    and improve concurrent performance. Instead of a single `_connections` dict
    with a single lock, connections are distributed across multiple shards,
    each with its own dict and lock.

    These tests verify:
    - Connections are distributed across shards by hash(client_id) % num_shards
    - All existing operations work transparently with sharding
    - Concurrent operations on different shards don't block each other
    - Broadcast reaches all clients across all shards

    Tests will FAIL until:
    - WebSocketConnectionManager has `_shards: list[ConnectionShard]` attribute
    - Connections are stored in shards based on hash(client_id) % num_shards
    - All methods (connect, disconnect, send, broadcast) use sharded storage
    """

    @pytest.mark.asyncio
    async def test_should_distribute_connections_across_shards(self) -> None:
        """
        Given: A manager with num_shards=4
        When: Multiple clients connect with IDs that hash to different shards
        Then: Connections should be stored in the expected shards

        This test verifies that the sharding algorithm correctly distributes
        connections based on hash(client_id) % num_shards.
        """
        # Arrange
        num_shards = 4
        config = WebSocketConfig(num_shards=num_shards)
        manager = WebSocketConnectionManager(config)

        # Create clients with predictable shard distribution
        # We'll use client IDs and verify they end up in expected shards
        client_ids = ["client_a", "client_b", "client_c", "client_d", "client_e"]

        # Act - Connect all clients
        for client_id in client_ids:
            ws = AsyncMock()
            ws.close = AsyncMock()
            await manager.connect(client_id, ws)

        # Assert - Verify connections are distributed across shards
        # The manager must have _shards attribute with list of ConnectionShard
        assert hasattr(manager, "_shards"), (
            "Manager must have _shards attribute for sharded connection storage"
        )
        assert len(manager._shards) == num_shards, (
            f"Expected {num_shards} shards, got {len(manager._shards)}"
        )

        # Verify each client is in the expected shard
        for client_id in client_ids:
            expected_shard_idx = get_shard_index(client_id, num_shards)
            shard = manager._shards[expected_shard_idx]
            assert client_id in shard.connections, (
                f"Client {client_id} should be in shard {expected_shard_idx}, "
                f"but was not found. Shard contains: {list(shard.connections.keys())}"
            )

        # Verify total connection count is correct
        total_in_shards = sum(len(s.connections) for s in manager._shards)
        assert total_in_shards == len(client_ids), (
            f"Expected {len(client_ids)} connections across all shards, "
            f"got {total_in_shards}"
        )

    @pytest.mark.asyncio
    async def test_should_maintain_correct_count_with_sharding(self) -> None:
        """
        Given: A manager with num_shards=8
        When: 20 clients are connected
        Then: get_connection_count() returns 20 and get_active_connections()
              returns all 20 client IDs

        This test verifies that connection counting and listing works correctly
        when connections are distributed across multiple shards.
        """
        # Arrange
        num_shards = 8
        num_clients = 20
        config = WebSocketConfig(num_shards=num_shards)
        manager = WebSocketConnectionManager(config)

        # First verify sharding is implemented
        assert hasattr(manager, "_shards"), (
            "Manager must have _shards attribute for sharded connection storage"
        )
        assert len(manager._shards) == num_shards, (
            f"Expected {num_shards} shards, got {len(manager._shards)}"
        )

        client_ids = [f"client_{i}" for i in range(num_clients)]

        # Act - Connect all clients
        for client_id in client_ids:
            ws = AsyncMock()
            ws.close = AsyncMock()
            await manager.connect(client_id, ws)

        # Assert - Verify count
        assert manager.get_connection_count() == num_clients, (
            f"Expected {num_clients} connections, "
            f"got {manager.get_connection_count()}"
        )

        # Assert - Verify all client IDs are returned
        active = manager.get_active_connections()
        assert len(active) == num_clients, (
            f"Expected {num_clients} active connections, got {len(active)}"
        )
        for client_id in client_ids:
            assert client_id in active, (
                f"Client {client_id} should be in active connections"
            )

        # Verify connections are actually distributed across shards
        shards_with_connections = sum(
            1 for s in manager._shards if len(s.connections) > 0
        )
        assert shards_with_connections > 1, (
            f"Expected connections in multiple shards, but only "
            f"{shards_with_connections} shard(s) have connections"
        )

    @pytest.mark.asyncio
    async def test_should_allow_concurrent_operations_on_different_shards(
        self,
    ) -> None:
        """
        Given: A manager with num_shards=4 and 4 clients in different shards
        When: Concurrent send_to_client operations are performed to all 4 clients
        Then: All operations should succeed without blocking each other

        This test verifies that operations on different shards can proceed
        concurrently without lock contention. Each shard has its own lock,
        so operations on different shards should not interfere.
        """
        # Arrange
        num_shards = 4
        config = WebSocketConfig(num_shards=num_shards)
        manager = WebSocketConnectionManager(config)

        # First verify sharding is implemented
        assert hasattr(manager, "_shards"), (
            "Manager must have _shards attribute for sharded connection storage"
        )
        assert len(manager._shards) == num_shards, (
            f"Expected {num_shards} shards, got {len(manager._shards)}"
        )

        # Find 4 client IDs that hash to different shards
        shard_clients: dict[int, str] = {}
        candidate = 0
        while len(shard_clients) < num_shards:
            client_id = f"test_client_{candidate}"
            shard_idx = get_shard_index(client_id, num_shards)
            if shard_idx not in shard_clients:
                shard_clients[shard_idx] = client_id
            candidate += 1

        client_ids = list(shard_clients.values())

        # Connect all clients with tracked websockets
        send_times: dict[str, tuple[float, float]] = {}  # client_id -> (start, end)
        send_lock = asyncio.Lock()

        async def tracked_send_text(client_id: str, message: str) -> None:
            """Track when send starts and ends."""
            async with send_lock:
                start = time.time()
            await asyncio.sleep(0.05)  # Simulate network delay
            async with send_lock:
                end = time.time()
                send_times[client_id] = (start, end)

        for client_id in client_ids:
            ws = AsyncMock()
            ws.send_text = AsyncMock(
                side_effect=lambda msg, cid=client_id: tracked_send_text(cid, msg)
            )
            ws.close = AsyncMock()
            await manager.connect(client_id, ws)

        # Verify clients are in different shards
        for shard_idx, client_id in shard_clients.items():
            assert client_id in manager._shards[shard_idx].connections, (
                f"Client {client_id} should be in shard {shard_idx}"
            )

        # Act - Send to all clients concurrently
        tasks = [
            asyncio.create_task(manager.send_to_client(client_id, f"msg_{client_id}"))
            for client_id in client_ids
        ]
        results = await asyncio.gather(*tasks)

        # Assert - All sends should succeed
        assert all(results), (
            f"All send operations should succeed, got {results}"
        )

        # Verify that operations overlapped (ran concurrently)
        # If locks per shard work correctly, some sends should overlap in time
        times = list(send_times.values())
        if len(times) >= 2:
            # Sort by start time
            sorted_times = sorted(times, key=lambda x: x[0])
            # Check if any operations overlapped
            overlapped = False
            for i in range(len(sorted_times) - 1):
                if sorted_times[i][1] > sorted_times[i + 1][0]:
                    overlapped = True
                    break
            assert overlapped, (
                "Expected concurrent operations on different shards to overlap in time. "
                "If using a single lock, operations would be sequential."
            )

    @pytest.mark.asyncio
    async def test_should_broadcast_to_all_shards(self) -> None:
        """
        Given: A manager with num_shards=4 and clients distributed across all shards
        When: broadcast() is called
        Then: All clients across all shards should receive the message

        This test verifies that broadcast correctly iterates over all shards
        and sends to all clients regardless of which shard they are in.
        """
        # Arrange
        num_shards = 4
        config = WebSocketConfig(num_shards=num_shards)
        manager = WebSocketConnectionManager(config)

        # Create 12 clients (3 per shard on average)
        num_clients = 12
        client_ids = [f"broadcast_client_{i}" for i in range(num_clients)]

        websockets: dict[str, AsyncMock] = {}
        for client_id in client_ids:
            ws = AsyncMock()
            ws.send_text = AsyncMock()
            ws.send_json = AsyncMock()
            ws.close = AsyncMock()
            websockets[client_id] = ws
            await manager.connect(client_id, ws)

        # Verify clients are distributed across multiple shards
        assert hasattr(manager, "_shards"), (
            "Manager must have _shards attribute"
        )
        shards_with_clients = sum(
            1 for s in manager._shards if len(s.connections) > 0
        )
        assert shards_with_clients > 1, (
            f"Expected clients in multiple shards, but only {shards_with_clients} "
            "shard(s) have clients"
        )

        # Act - Broadcast message
        result = await manager.broadcast("broadcast_message")

        # Assert - All clients received the message
        assert result.total == num_clients, (
            f"Expected total={num_clients}, got {result.total}"
        )
        assert result.succeeded == num_clients, (
            f"Expected succeeded={num_clients}, got {result.succeeded}"
        )
        assert result.failed == 0, (
            f"Expected failed=0, got {result.failed}"
        )

        # Verify each websocket received the message
        for client_id, ws in websockets.items():
            ws.send_text.assert_called_with("broadcast_message"), (
                f"Client {client_id} did not receive broadcast message"
            )

    @pytest.mark.asyncio
    async def test_should_work_with_single_shard(self) -> None:
        """
        Given: A manager with num_shards=1
        When: All basic operations are performed (connect, disconnect, send, broadcast)
        Then: All operations should work exactly as before sharding was implemented

        This test verifies backward compatibility - with a single shard,
        behavior should be identical to the pre-sharding implementation.
        """
        # Arrange
        config = WebSocketConfig(num_shards=1)
        manager = WebSocketConnectionManager(config)

        # Test connect
        ws1 = AsyncMock()
        ws1.send_text = AsyncMock()
        ws1.close = AsyncMock()
        ws2 = AsyncMock()
        ws2.send_text = AsyncMock()
        ws2.close = AsyncMock()

        await manager.connect("client_1", ws1)
        await manager.connect("client_2", ws2)

        assert manager.get_connection_count() == 2
        assert set(manager.get_active_connections()) == {"client_1", "client_2"}

        # Test send_to_client
        success = await manager.send_to_client("client_1", "hello")
        assert success is True
        ws1.send_text.assert_called_with("hello")

        # Test broadcast
        result = await manager.broadcast("broadcast")
        assert result.succeeded == 2
        ws2.send_text.assert_called_with("broadcast")

        # Test disconnect
        await manager.disconnect("client_1")
        assert manager.get_connection_count() == 1
        assert "client_1" not in manager.get_active_connections()

        # Verify single shard exists
        assert hasattr(manager, "_shards"), (
            "Manager must have _shards attribute"
        )
        assert len(manager._shards) == 1, (
            "Expected exactly 1 shard for num_shards=1"
        )

    @pytest.mark.asyncio
    async def test_should_disconnect_from_correct_shard(self) -> None:
        """
        Given: A manager with num_shards=4 and a connected client
        When: The client is disconnected
        Then: The client should be removed from the correct shard and count should update

        This test verifies that disconnect() correctly identifies which shard
        contains the client and removes it from that shard only.
        """
        # Arrange
        num_shards = 4
        config = WebSocketConfig(num_shards=num_shards)
        manager = WebSocketConnectionManager(config)

        # Connect multiple clients
        client_ids = ["disconnect_test_a", "disconnect_test_b", "disconnect_test_c"]
        target_client = "disconnect_test_b"
        target_shard_idx = get_shard_index(target_client, num_shards)

        for client_id in client_ids:
            ws = AsyncMock()
            ws.close = AsyncMock()
            await manager.connect(client_id, ws)

        # Verify initial state
        assert manager.get_connection_count() == 3
        assert hasattr(manager, "_shards"), (
            "Manager must have _shards attribute"
        )
        assert target_client in manager._shards[target_shard_idx].connections, (
            f"Target client should be in shard {target_shard_idx}"
        )

        # Act - Disconnect the target client
        await manager.disconnect(target_client)

        # Assert - Client removed from correct shard
        assert manager.get_connection_count() == 2, (
            f"Expected 2 connections after disconnect, "
            f"got {manager.get_connection_count()}"
        )
        assert target_client not in manager.get_active_connections(), (
            f"Client {target_client} should not be in active connections"
        )
        assert target_client not in manager._shards[target_shard_idx].connections, (
            f"Client {target_client} should be removed from shard {target_shard_idx}"
        )

        # Other clients should still be present
        for client_id in client_ids:
            if client_id != target_client:
                assert client_id in manager.get_active_connections(), (
                    f"Client {client_id} should still be connected"
                )


# ==================== Phase 4: Memory Management ====================


class TestMemoryManagement:
    """Test Memory Management functionality.

    Phase 4 implements memory tracking and management features:
    - Track memory usage per connection (metadata + user_data)
    - Enforce per-connection and total memory limits
    - Eviction policies: LRU, FIFO, OLDEST
    - Background memory monitoring (optional)

    These tests verify:
    - Memory is tracked per connection
    - Per-connection memory limits are enforced
    - Total memory limits trigger eviction
    - Eviction policies work correctly (OLDEST, LRU)
    - Memory statistics are reported correctly
    - Memory tracking updates when user_data changes

    Tests will FAIL until:
    - WebSocketConnectionManager has get_memory_usage() method
    - Connect validates memory limits and raises MemoryLimitExceededError
    - Eviction logic is implemented based on memory_eviction_policy
    - set_user_data() method is implemented
    """

    @pytest.mark.asyncio
    async def test_should_track_memory_per_connection(self) -> None:
        """
        Given: A manager with a connected client that has large metadata
        When: get_memory_usage() is called
        Then: The returned stats should show memory tracked for that connection

        This test verifies that the manager tracks memory usage per connection.
        The metadata dictionary should contribute to the memory count.
        """
        # Arrange
        config = WebSocketConfig()
        manager = WebSocketConnectionManager(config)

        # Create client with large metadata
        large_metadata = {
            "user_profile": {
                "name": "Test User",
                "email": "test@example.com",
                "preferences": {"theme": "dark", "language": "en"},
            },
            "session_data": "x" * 500,  # 500 byte string
        }

        ws = AsyncMock()
        ws.close = AsyncMock()
        client_id = "memory_test_client"

        # Act
        await manager.connect(client_id, ws, metadata=large_metadata)
        memory_usage = manager.get_memory_usage()

        # Assert - Manager must have get_memory_usage() method
        assert memory_usage is not None, (
            "get_memory_usage() should return memory statistics"
        )
        assert isinstance(memory_usage, dict), (
            "get_memory_usage() should return a dictionary"
        )
        assert "total_memory" in memory_usage, (
            "Memory usage should include 'total_memory' key"
        )
        assert memory_usage["total_memory"] > 0, (
            "Total memory should be positive when a client with metadata is connected"
        )

        # Cleanup
        await manager.disconnect(client_id)

    @pytest.mark.asyncio
    async def test_should_enforce_per_connection_memory_limit(self) -> None:
        """
        Given: A manager with max_memory_per_connection=1000 bytes
        When: A client tries to connect with metadata larger than the limit
        Then: MemoryLimitExceededError should be raised

        This test verifies that per-connection memory limits are enforced
        during the connect() call.
        """
        # Arrange
        max_memory = 1000  # bytes
        config = WebSocketConfig(max_memory_per_connection=max_memory)
        manager = WebSocketConnectionManager(config)

        # Create metadata that exceeds the limit
        # Each character in Python string is roughly 1 byte for ASCII
        # Plus dictionary overhead, this should exceed 1000 bytes
        oversized_metadata = {
            "large_data": "x" * 2000,  # 2000+ bytes
        }

        ws = AsyncMock()
        ws.close = AsyncMock()
        client_id = "oversized_client"

        # Act & Assert
        with pytest.raises(MemoryLimitExceededError) as exc_info:
            await manager.connect(client_id, ws, metadata=oversized_metadata)

        assert "memory" in str(exc_info.value).lower() or "limit" in str(exc_info.value).lower(), (
            "Error message should mention memory or limit"
        )

    @pytest.mark.asyncio
    async def test_should_enforce_total_memory_limit(self) -> None:
        """
        Given: A manager with max_total_memory=5000 bytes
        When: Multiple clients connect with metadata that eventually exceeds total limit
        Then: Eviction should be triggered to stay under the limit

        This test verifies that total memory limits trigger connection eviction
        when the limit is exceeded.
        """
        # Arrange
        max_total = 5000  # bytes
        config = WebSocketConfig(
            max_total_memory=max_total,
            memory_eviction_policy=MemoryEvictionPolicy.OLDEST,
        )
        manager = WebSocketConnectionManager(config)

        # Create multiple clients with metadata
        # Each connection with ~1500 bytes metadata means 4th client should trigger eviction
        clients_data = []
        for i in range(5):
            ws = AsyncMock()
            ws.close = AsyncMock()
            metadata = {"data": "x" * 1000}  # ~1000+ bytes each
            clients_data.append((f"client_{i}", ws, metadata))

        # Act - Connect all clients
        for client_id, ws, metadata in clients_data:
            await manager.connect(client_id, ws, metadata=metadata)

        # Assert - Some connections should have been evicted
        # Total connections should be less than 5 if eviction happened
        # OR total memory should be under limit
        memory_usage = manager.get_memory_usage()
        assert memory_usage["total_memory"] <= max_total, (
            f"Total memory {memory_usage['total_memory']} should not exceed "
            f"max_total_memory {max_total}"
        )

        # Verify eviction happened (not all 5 clients should remain)
        active_count = manager.get_connection_count()
        assert active_count < 5, (
            f"Expected some clients to be evicted due to memory limit, "
            f"but {active_count} clients remain"
        )

        # Cleanup
        await manager.graceful_shutdown()

    @pytest.mark.asyncio
    async def test_should_evict_oldest_connection(self) -> None:
        """
        Given: A manager with memory_eviction_policy=OLDEST and small max_total_memory
        When: Multiple clients connect with delays and memory limit is exceeded
        Then: The oldest connection (by created_at) should be evicted first

        This test verifies that OLDEST eviction policy removes the connection
        with the earliest created_at timestamp.
        """
        # Arrange
        max_total = 3000  # bytes - small limit to force eviction
        config = WebSocketConfig(
            max_total_memory=max_total,
            memory_eviction_policy=MemoryEvictionPolicy.OLDEST,
        )
        manager = WebSocketConnectionManager(config)

        # Connect client1 first (oldest)
        ws1 = AsyncMock()
        ws1.close = AsyncMock()
        await manager.connect("client_oldest", ws1, metadata={"data": "a" * 800})

        # Small delay to ensure different created_at timestamps
        await asyncio.sleep(0.01)

        # Connect client2 (middle)
        ws2 = AsyncMock()
        ws2.close = AsyncMock()
        await manager.connect("client_middle", ws2, metadata={"data": "b" * 800})

        await asyncio.sleep(0.01)

        # Connect client3 (newest) - this should trigger eviction of oldest
        ws3 = AsyncMock()
        ws3.close = AsyncMock()
        await manager.connect("client_newest", ws3, metadata={"data": "c" * 800})

        await asyncio.sleep(0.01)

        # Connect client4 to trigger eviction
        ws4 = AsyncMock()
        ws4.close = AsyncMock()
        await manager.connect("client_trigger", ws4, metadata={"data": "d" * 800})

        # Assert - client_oldest should have been evicted
        active_connections = manager.get_active_connections()
        assert "client_oldest" not in active_connections, (
            "client_oldest (first connected) should have been evicted "
            "due to OLDEST eviction policy"
        )

        # Newer clients should still be connected
        assert "client_newest" in active_connections or "client_trigger" in active_connections, (
            "Newer clients should remain after eviction"
        )

        # Cleanup
        await manager.graceful_shutdown()

    @pytest.mark.asyncio
    async def test_should_evict_lru_connection(self) -> None:
        """
        Given: A manager with memory_eviction_policy=LRU and small max_total_memory
        When: 3 clients connect, client2 has no activity, and a 4th client triggers eviction
        Then: client2 (least recently used) should be evicted

        This test verifies that LRU eviction policy removes the connection
        with the oldest last_activity timestamp.
        """
        # Arrange
        max_total = 3000  # bytes - small limit to force eviction
        config = WebSocketConfig(
            max_total_memory=max_total,
            memory_eviction_policy=MemoryEvictionPolicy.LRU,
        )
        manager = WebSocketConnectionManager(config)

        # Connect 3 clients
        ws1 = AsyncMock()
        ws1.close = AsyncMock()
        ws1.send_text = AsyncMock()
        await manager.connect("client_active1", ws1, metadata={"data": "a" * 700})

        ws2 = AsyncMock()
        ws2.close = AsyncMock()
        ws2.send_text = AsyncMock()
        await manager.connect("client_inactive", ws2, metadata={"data": "b" * 700})

        ws3 = AsyncMock()
        ws3.close = AsyncMock()
        ws3.send_text = AsyncMock()
        await manager.connect("client_active2", ws3, metadata={"data": "c" * 700})

        # Small delay
        await asyncio.sleep(0.02)

        # Update activity for client1 and client3, but NOT client2
        # This makes client2 the "least recently used"
        await manager.send_to_client("client_active1", "ping")
        await asyncio.sleep(0.01)
        await manager.send_to_client("client_active2", "ping")

        # Small delay to ensure last_activity timestamps differ
        await asyncio.sleep(0.01)

        # Connect client4 to trigger eviction
        ws4 = AsyncMock()
        ws4.close = AsyncMock()
        await manager.connect("client_trigger", ws4, metadata={"data": "d" * 700})

        # Assert - client_inactive should have been evicted (LRU)
        active_connections = manager.get_active_connections()
        assert "client_inactive" not in active_connections, (
            "client_inactive (least recently used) should have been evicted "
            "due to LRU eviction policy"
        )

        # Active clients should still be connected
        # At least one of the recently active clients should remain
        recently_active = ["client_active1", "client_active2", "client_trigger"]
        remaining = [c for c in recently_active if c in active_connections]
        assert len(remaining) > 0, (
            "At least some recently active clients should remain after LRU eviction"
        )

        # Cleanup
        await manager.graceful_shutdown()

    @pytest.mark.asyncio
    async def test_should_return_memory_usage_stats(self) -> None:
        """
        Given: A manager with multiple connected clients having various metadata sizes
        When: get_memory_usage() is called
        Then: Returns dict with total_memory, connection_count, per_connection_average

        This test verifies that memory usage statistics are correctly calculated
        and returned in the expected format.
        """
        # Arrange
        config = WebSocketConfig()
        manager = WebSocketConnectionManager(config)

        # Connect multiple clients with different metadata sizes
        clients = [
            ("client_small", {"data": "x" * 100}),
            ("client_medium", {"data": "y" * 500}),
            ("client_large", {"data": "z" * 1000}),
        ]

        for client_id, metadata in clients:
            ws = AsyncMock()
            ws.close = AsyncMock()
            await manager.connect(client_id, ws, metadata=metadata)

        # Act
        memory_usage = manager.get_memory_usage()

        # Assert - Check all required keys exist
        assert isinstance(memory_usage, dict), (
            "get_memory_usage() should return a dictionary"
        )

        required_keys = ["total_memory", "connection_count", "per_connection_average"]
        for key in required_keys:
            assert key in memory_usage, (
                f"Memory usage stats should include '{key}' key"
            )

        # Verify values are sensible
        assert memory_usage["connection_count"] == 3, (
            f"Expected 3 connections, got {memory_usage['connection_count']}"
        )
        assert memory_usage["total_memory"] > 0, (
            "Total memory should be positive with connected clients"
        )
        assert memory_usage["per_connection_average"] > 0, (
            "Per-connection average should be positive"
        )

        # Average should equal total / count
        expected_avg = memory_usage["total_memory"] / memory_usage["connection_count"]
        assert abs(memory_usage["per_connection_average"] - expected_avg) < 1, (
            f"Per-connection average {memory_usage['per_connection_average']} "
            f"should equal total/count {expected_avg}"
        )

        # Cleanup
        await manager.graceful_shutdown()

    @pytest.mark.asyncio
    async def test_should_update_memory_on_set_user_data(self) -> None:
        """
        Given: A manager with a connected client
        When: set_user_data(client_id, key, large_value) is called
        Then: Memory tracking should be updated to reflect the new data

        This test verifies that memory tracking is updated when user_data
        is modified through the set_user_data() method.
        """
        # Arrange
        config = WebSocketConfig()
        manager = WebSocketConnectionManager(config)

        ws = AsyncMock()
        ws.close = AsyncMock()
        client_id = "user_data_client"

        await manager.connect(client_id, ws, metadata={"initial": "small"})

        # Get initial memory usage
        initial_memory = manager.get_memory_usage()
        initial_total = initial_memory["total_memory"]

        # Act - Add large user data
        large_value = "x" * 5000  # 5KB of data
        await manager.set_user_data(client_id, "large_key", large_value)

        # Get updated memory usage
        updated_memory = manager.get_memory_usage()
        updated_total = updated_memory["total_memory"]

        # Assert - Memory should have increased
        assert updated_total > initial_total, (
            f"Memory should increase after adding user data. "
            f"Initial: {initial_total}, Updated: {updated_total}"
        )

        # The increase should be at least close to the size of the added data
        memory_increase = updated_total - initial_total
        assert memory_increase >= 4000, (
            f"Memory increase ({memory_increase}) should reflect the ~5KB added data"
        )

        # Cleanup
        await manager.graceful_shutdown()

    @pytest.mark.asyncio
    async def test_should_raise_error_on_set_user_data_exceeds_limit(self) -> None:
        """
        Given: A manager with max_memory_per_connection=2000 and a connected client
        When: set_user_data is called with value that would exceed the limit
        Then: MemoryLimitExceededError should be raised

        This test verifies that per-connection memory limits are also enforced
        when updating user_data, not just during initial connection.
        """
        # Arrange
        max_memory = 2000  # bytes
        config = WebSocketConfig(max_memory_per_connection=max_memory)
        manager = WebSocketConnectionManager(config)

        ws = AsyncMock()
        ws.close = AsyncMock()
        client_id = "limit_test_client"

        # Connect with small metadata (should succeed)
        await manager.connect(client_id, ws, metadata={"small": "data"})

        # Act & Assert - Try to add large user data
        with pytest.raises(MemoryLimitExceededError):
            await manager.set_user_data(client_id, "large_key", "x" * 5000)

        # Cleanup
        await manager.graceful_shutdown()

    @pytest.mark.asyncio
    async def test_should_get_user_data(self) -> None:
        """
        Given: A manager with a connected client that has user_data set
        When: get_user_data(client_id, key) is called
        Then: The correct value should be returned

        This test verifies that user_data can be retrieved after being set.
        """
        # Arrange
        config = WebSocketConfig()
        manager = WebSocketConnectionManager(config)

        ws = AsyncMock()
        ws.close = AsyncMock()
        client_id = "get_data_client"

        await manager.connect(client_id, ws)

        # Set some user data
        await manager.set_user_data(client_id, "user_id", 12345)
        await manager.set_user_data(client_id, "role", "admin")

        # Act
        user_id = await manager.get_user_data(client_id, "user_id")
        role = await manager.get_user_data(client_id, "role")
        missing = await manager.get_user_data(client_id, "nonexistent")

        # Assert
        assert user_id == 12345, f"Expected user_id=12345, got {user_id}"
        assert role == "admin", f"Expected role='admin', got {role}"
        assert missing is None, f"Expected None for missing key, got {missing}"

        # Cleanup
        await manager.graceful_shutdown()

    @pytest.mark.asyncio
    async def test_should_return_zero_memory_when_no_connections(self) -> None:
        """
        Given: A manager with no connected clients
        When: get_memory_usage() is called
        Then: Should return stats with zero total_memory and zero connection_count

        This test verifies edge case behavior when no connections exist.
        """
        # Arrange
        config = WebSocketConfig()
        manager = WebSocketConnectionManager(config)

        # Act - No connections
        memory_usage = manager.get_memory_usage()

        # Assert
        assert memory_usage["total_memory"] == 0, (
            "Total memory should be 0 with no connections"
        )
        assert memory_usage["connection_count"] == 0, (
            "Connection count should be 0 with no connections"
        )
        assert memory_usage["per_connection_average"] == 0, (
            "Per-connection average should be 0 with no connections"
        )
