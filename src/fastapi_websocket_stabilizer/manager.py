"""WebSocket connection manager and lifecycle management."""

import asyncio
import hmac
import secrets
import time
from json import JSONDecodeError
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect

from .config import BroadcastResult, ShutdownReport, WebSocketConfig
from .exceptions import (
    BroadcastFailedError,
    ConnectionLimitExceededError,
    ConnectionNotFoundError,
    InvalidTokenError,
    TokenExpiredError,
)
from .logging_utils import StructuredLogger, get_logger
from ._models import ConnectionMetadata, ReconnectToken


class WebSocketConnectionManager:
    """Manages WebSocket connections with lifecycle and heartbeat support.

    This manager provides:
    - Connection tracking by client ID
    - Broadcast messaging (to one, many, or all clients)
    - Heartbeat/ping-pong mechanism
    - Reconnection token generation and validation
    - Graceful shutdown
    """

    def __init__(self, config: Optional[WebSocketConfig] = None) -> None:
        """Initialize connection manager.

        Args:
            config: WebSocketConfig instance. Defaults to config with default values.
        """
        self.config = config or WebSocketConfig()
        self._logger = get_logger(__name__)
        self._structured_logger = StructuredLogger(self._logger)

        # Connection storage
        self._connections: dict[str, ConnectionMetadata] = {}
        self._connections_lock = asyncio.Lock()

        # Token storage
        self._tokens: dict[str, ReconnectToken] = {}
        self._tokens_lock = asyncio.Lock()

        # Token generation secret (should be unique per manager instance)
        self._token_secret = secrets.token_hex(32)

        # Background tasks
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._token_cleanup_task: Optional[asyncio.Task[None]] = None
        self._accepting_connections = True

    async def start(self) -> None:
        """Start background tasks (heartbeat and token cleanup).

        Should be called during application startup.
        """
        self._accepting_connections = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_worker())
        self._token_cleanup_task = asyncio.create_task(self._token_cleanup_worker())
        self._logger.info("WebSocket manager started with heartbeat and token cleanup")

    async def connect(
        self,
        client_id: str,
        websocket: WebSocket,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Register and track a new WebSocket connection.

        Args:
            client_id: Unique identifier for the client
            websocket: FastAPI WebSocket instance (must be already accepted)
            metadata: Optional metadata to attach to connection

        Raises:
            ConnectionLimitExceededError: If max connections limit is reached
            ConnectionNotFoundError: If client_id is empty
        """
        if not client_id:
            raise ConnectionNotFoundError("client_id cannot be empty")

        async with self._connections_lock:
            if (
                self.config.max_connections
                and len(self._connections) >= self.config.max_connections
            ):
                raise ConnectionLimitExceededError(
                    f"Maximum connections ({self.config.max_connections}) reached"
                )

            if client_id in self._connections:
                # Replace old connection if it exists
                old_conn = self._connections[client_id]
                try:
                    await old_conn.websocket.close(code=1000, reason="reconnected")
                except Exception:
                    pass

            conn = ConnectionMetadata(
                client_id=client_id,
                websocket=websocket,
                created_at=time.time(),
                metadata=metadata or {},
            )
            self._connections[client_id] = conn

        self._structured_logger.log_connection("connected", client_id)

    async def disconnect(self, client_id: str) -> None:
        """Gracefully disconnect a client and clean up resources.

        Args:
            client_id: Client identifier

        Raises:
            ConnectionNotFoundError: If client_id not found
        """
        async with self._connections_lock:
            if client_id not in self._connections:
                raise ConnectionNotFoundError(f"Connection not found: {client_id}")

            conn = self._connections[client_id]
            duration = conn.get_duration_seconds()

            # Cancel pong timeout task if pending
            if conn.pong_timeout_task:
                conn.pong_timeout_task.cancel()
                try:
                    await conn.pong_timeout_task
                except asyncio.CancelledError:
                    pass

            # Close WebSocket
            try:
                await conn.websocket.close(code=1000, reason="normal closure")
            except Exception as e:
                self._logger.debug(f"Error closing WebSocket for {client_id}: {e}")

            del self._connections[client_id]

        self._structured_logger.log_connection(
            "disconnected", client_id, duration_seconds=duration
        )

    async def send_to_client(self, client_id: str, message: str | dict) -> bool:
        """Send a message to a specific client.

        Args:
            client_id: Target client identifier
            message: Message to send (string or dict)

        Returns:
            True if message sent successfully, False otherwise
        """
        async with self._connections_lock:
            if client_id not in self._connections:
                return False
            conn = self._connections[client_id]

        try:
            if isinstance(message, dict):
                await conn.websocket.send_json(message)
            else:
                await conn.websocket.send_text(message)
            conn.update_last_activity()
            return True
        except Exception as e:
            self._logger.debug(f"Failed to send message to {client_id}: {e}")
            await self.disconnect(client_id)
            return False

    async def broadcast(
        self,
        message: str | dict,
        exclude_client: Optional[str] = None,
    ) -> BroadcastResult:
        """Broadcast a message to all connected clients.

        Args:
            message: Message to broadcast
            exclude_client: Optional client ID to exclude from broadcast

        Returns:
            BroadcastResult with delivery statistics
        """
        # Create snapshot of current connections
        async with self._connections_lock:
            connections = list(self._connections.items())

        result = BroadcastResult(total=len(connections), succeeded=0, failed=0)

        for client_id, conn in connections:
            if exclude_client and client_id == exclude_client:
                continue

            try:
                if isinstance(message, dict):
                    await conn.websocket.send_json(message)
                else:
                    await conn.websocket.send_text(message)
                conn.update_last_activity()
                result.succeeded += 1
            except Exception as e:
                result.failed += 1
                error_msg = str(e)
                result.errors.append((client_id, error_msg))
                self._logger.debug(f"Broadcast failed for {client_id}: {e}")

                # Disconnect failed client
                try:
                    await self.disconnect(client_id)
                except Exception:
                    pass

        self._structured_logger.log_broadcast(
            result.total, result.succeeded, result.failed
        )

        if result.failed > 0 and result.failed == result.total:
            raise BroadcastFailedError(
                f"Broadcast failed for all {result.total} clients"
            )

        return result

    def get_active_connections(self) -> list[str]:
        """Get list of currently connected client IDs.

        This is thread-safe and returns a snapshot.

        Returns:
            List of client IDs
        """
        # Note: Can't use async context manager in non-async function,
        # so we use run_coroutine_threadsafe pattern if needed.
        # For now, return synchronously (relies on Python's GIL for dict access)
        return list(self._connections.keys())

    def get_connection_count(self) -> int:
        """Get number of currently active connections.

        Returns:
            Number of connected clients
        """
        return len(self._connections)

    def generate_reconnect_token(self, client_id: str) -> str:
        """Generate a reconnection token for a client.

        Token format: <timestamp>.<nonce>.<client_id>.<hmac>

        Args:
            client_id: Client identifier to associate with token

        Returns:
            Token string
        """
        nonce = secrets.token_hex(16)
        timestamp = str(int(time.time()))
        message = f"{timestamp}.{nonce}.{client_id}"
        signature = hmac.new(
            self._token_secret.encode(), message.encode(), "sha256"
        ).hex()
        token = f"{message}.{signature}"

        # Store token for validation
        expires_at = time.time() + self.config.reconnect_token_ttl.total_seconds()
        reconnect_token = ReconnectToken(
            token=token,
            client_id=client_id,
            created_at=time.time(),
            expires_at=expires_at,
            nonce=nonce,
        )

        self._tokens[token] = reconnect_token
        self._structured_logger.log_token("generated", client_id, ttl_seconds=int(self.config.reconnect_token_ttl.total_seconds()))

        return token

    async def validate_reconnect_token(self, token: str) -> Optional[str]:
        """Validate a reconnection token and return associated client_id.

        Args:
            token: Token string to validate

        Returns:
            Client ID if token is valid, None otherwise

        Raises:
            TokenExpiredError: If token has expired
            InvalidTokenError: If token is invalid or tampered
        """
        async with self._tokens_lock:
            if token not in self._tokens:
                raise InvalidTokenError("Token not found or already used")

            reconnect_token = self._tokens[token]

            if reconnect_token.is_expired():
                del self._tokens[token]
                raise TokenExpiredError("Token has expired")

            # Validate HMAC signature
            parts = token.split(".")
            if len(parts) != 4:
                raise InvalidTokenError("Token format invalid")

            timestamp, nonce, client_id, provided_signature = parts
            message = f"{timestamp}.{nonce}.{client_id}"
            expected_signature = hmac.new(
                self._token_secret.encode(), message.encode(), "sha256"
            ).hex()

            if not hmac.compare_digest(provided_signature, expected_signature):
                raise InvalidTokenError("Token signature invalid")

            # Mark token as used
            del self._tokens[token]
            self._structured_logger.log_token("validated", client_id)

            return client_id

    async def graceful_shutdown(self, timeout: float = 30.0) -> ShutdownReport:
        """Gracefully shutdown the connection manager.

        Closes all connections and stops background tasks.

        Args:
            timeout: Maximum time to wait for shutdown in seconds

        Returns:
            ShutdownReport with closure statistics
        """
        start_time = time.time()
        self._accepting_connections = False

        # Stop background tasks
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await asyncio.wait_for(self._heartbeat_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if self._token_cleanup_task:
            self._token_cleanup_task.cancel()
            try:
                await asyncio.wait_for(self._token_cleanup_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # Close all connections
        closed_count = 0
        failed_count = 0

        async with self._connections_lock:
            connection_list = list(self._connections.values())

        for conn in connection_list:
            try:
                await conn.websocket.close(code=1001, reason="server shutdown")
                closed_count += 1
            except Exception as e:
                self._logger.debug(f"Error closing {conn.client_id}: {e}")
                failed_count += 1

        async with self._connections_lock:
            self._connections.clear()

        duration = time.time() - start_time
        report = ShutdownReport(
            closed_count=closed_count,
            failed_count=failed_count,
            duration_seconds=duration,
        )

        self._logger.info(
            f"Graceful shutdown complete: "
            f"closed={closed_count}, failed={failed_count}, "
            f"duration={duration:.2f}s"
        )

        return report

    async def _heartbeat_worker(self) -> None:
        """Background task for heartbeat/ping management."""
        while True:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)

                # Get snapshot of connections
                async with self._connections_lock:
                    connections = list(self._connections.items())

                for client_id, conn in connections:
                    if conn.pending_pong:
                        # Still waiting for previous pong, disconnect
                        self._structured_logger.log_heartbeat(
                            "timeout", client_id
                        )
                        try:
                            await self.disconnect(client_id)
                        except Exception:
                            pass
                    else:
                        # Send new ping
                        try:
                            await conn.websocket.send_json(
                                {"type": "ping", "timestamp": time.time()}
                            )
                            conn.pending_pong = True
                            conn.update_last_activity()

                            # Schedule pong timeout
                            if conn.pong_timeout_task:
                                conn.pong_timeout_task.cancel()

                            conn.pong_timeout_task = asyncio.create_task(
                                self._wait_for_pong(client_id)
                            )

                            self._structured_logger.log_heartbeat(
                                "ping_sent", client_id
                            )
                        except Exception as e:
                            self._logger.debug(
                                f"Failed to send heartbeat to {client_id}: {e}"
                            )
                            try:
                                await self.disconnect(client_id)
                            except Exception:
                                pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Error in heartbeat worker: {e}")

    async def _wait_for_pong(self, client_id: str) -> None:
        """Wait for pong response with timeout."""
        try:
            await asyncio.sleep(self.config.heartbeat_timeout)

            # Timeout expired without receiving pong
            async with self._connections_lock:
                if client_id in self._connections:
                    conn = self._connections[client_id]
                    if conn.pending_pong:
                        self._structured_logger.log_heartbeat(
                            "pong_timeout", client_id
                        )
                        await self.disconnect(client_id)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._logger.debug(f"Error in pong timeout handler: {e}")

    async def handle_pong(self, client_id: str) -> None:
        """Handle pong response from client.

        Args:
            client_id: Client that sent pong
        """
        async with self._connections_lock:
            if client_id not in self._connections:
                return

            conn = self._connections[client_id]
            conn.pending_pong = False
            conn.update_last_activity()

            if conn.pong_timeout_task:
                conn.pong_timeout_task.cancel()
                conn.pong_timeout_task = None

        self._structured_logger.log_heartbeat("pong_received", client_id)

    async def _token_cleanup_worker(self) -> None:
        """Background task for expired token cleanup."""
        while True:
            try:
                await asyncio.sleep(300)  # Cleanup every 5 minutes

                async with self._tokens_lock:
                    expired_tokens = [
                        token
                        for token, data in self._tokens.items()
                        if data.is_expired()
                    ]

                    for token in expired_tokens:
                        del self._tokens[token]

                if expired_tokens:
                    self._logger.debug(
                        f"Cleaned up {len(expired_tokens)} expired tokens"
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Error in token cleanup worker: {e}")
