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
