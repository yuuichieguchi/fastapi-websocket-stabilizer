"""WebSocket connection manager and lifecycle management."""

import asyncio
import hmac
import secrets
import time
from json import JSONDecodeError
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect

from .config import BroadcastResult, MemoryEvictionPolicy, ShutdownReport, WebSocketConfig
from .exceptions import (
    BroadcastFailedError,
    ConnectionLimitExceededError,
    ConnectionNotFoundError,
    InvalidTokenError,
    MemoryLimitExceededError,
    TokenExpiredError,
)
from .logging_utils import StructuredLogger, get_logger
from ._models import ConnectionMetadata, ReconnectToken, ConnectionShard


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

        # Connection storage - sharded for reduced lock contention
        self._shards: list[ConnectionShard] = [
            ConnectionShard() for _ in range(self.config.num_shards)
        ]

        # Token storage
        self._tokens: dict[str, ReconnectToken] = {}
        self._tokens_lock = asyncio.Lock()

        # Token generation secret (should be unique per manager instance)
        self._token_secret = secrets.token_hex(32)

        # Background tasks
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._token_cleanup_task: Optional[asyncio.Task[None]] = None
        self._accepting_connections = True

        # Semaphores for concurrency control
        self._cleanup_semaphore = asyncio.Semaphore(self.config.max_concurrent_cleanup)
        self._broadcast_semaphore = asyncio.Semaphore(self.config.max_concurrent_broadcast)

    def _get_shard_index(self, client_id: str) -> int:
        """Get shard index for a client_id using consistent hashing.

        Args:
            client_id: Client identifier

        Returns:
            Shard index (0 to num_shards-1)
        """
        return hash(client_id) % len(self._shards)

    def _get_shard(self, client_id: str) -> ConnectionShard:
        """Get the shard for a given client_id.

        Args:
            client_id: Client identifier

        Returns:
            The ConnectionShard for this client
        """
        return self._shards[self._get_shard_index(client_id)]

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
            MemoryLimitExceededError: If memory limits are exceeded
        """
        if not client_id:
            raise ConnectionNotFoundError("client_id cannot be empty")

        shard = self._get_shard(client_id)

        # Create connection metadata first to estimate memory
        conn = ConnectionMetadata(
            client_id=client_id,
            websocket=websocket,
            created_at=time.time(),
            metadata=metadata or {},
        )

        # Check per-connection memory limit before acquiring lock
        new_conn_memory = conn.estimate_memory_size()
        if (
            self.config.max_memory_per_connection is not None
            and new_conn_memory > self.config.max_memory_per_connection
        ):
            raise MemoryLimitExceededError(
                f"Connection memory ({new_conn_memory} bytes) exceeds "
                f"per-connection limit ({self.config.max_memory_per_connection} bytes)"
            )

        # Handle total memory limit and eviction outside the lock
        if self.config.max_total_memory is not None:
            current_total = self._calculate_total_memory()
            needed_memory = current_total + new_conn_memory

            if needed_memory > self.config.max_total_memory:
                bytes_to_free = needed_memory - self.config.max_total_memory
                await self._evict_for_memory(bytes_to_free)

                # Recheck after eviction
                current_total = self._calculate_total_memory()
                if current_total + new_conn_memory > self.config.max_total_memory:
                    raise MemoryLimitExceededError(
                        f"Cannot free enough memory for new connection. "
                        f"Current: {current_total}, New: {new_conn_memory}, "
                        f"Limit: {self.config.max_total_memory}"
                    )

        async with shard.lock:
            is_reconnection = client_id in shard.connections

            if (
                not is_reconnection
                and self.config.max_connections
                and self.get_connection_count() >= self.config.max_connections
            ):
                raise ConnectionLimitExceededError(
                    f"Maximum connections ({self.config.max_connections}) reached"
                )

            if is_reconnection:
                # Replace old connection if it exists
                old_conn = shard.connections[client_id]
                try:
                    await old_conn.websocket.close(code=1000, reason="reconnected")
                except Exception:
                    pass

            shard.connections[client_id] = conn

        self._structured_logger.log_connection("connected", client_id)

    async def disconnect(self, client_id: str) -> None:
        """Gracefully disconnect a client and clean up resources.

        Args:
            client_id: Client identifier

        Raises:
            ConnectionNotFoundError: If client_id not found
        """
        shard = self._get_shard(client_id)

        async with shard.lock:
            if client_id not in shard.connections:
                raise ConnectionNotFoundError(f"Connection not found: {client_id}")

            conn = shard.connections[client_id]
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

            del shard.connections[client_id]

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
        shard = self._get_shard(client_id)

        async with shard.lock:
            if client_id not in shard.connections:
                return False
            conn = shard.connections[client_id]

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
        # Create snapshot of current connections from all shards
        connections: list[tuple[str, ConnectionMetadata]] = []
        for shard in self._shards:
            async with shard.lock:
                connections.extend(list(shard.connections.items()))

        result = BroadcastResult(total=len(connections), succeeded=0, failed=0)

        async def send_with_semaphore(
            client_id: str, conn: ConnectionMetadata
        ) -> tuple[str, bool, str | None]:
            async with self._broadcast_semaphore:
                try:
                    if isinstance(message, dict):
                        await conn.websocket.send_json(message)
                    else:
                        await conn.websocket.send_text(message)
                    conn.update_last_activity()
                    return (client_id, True, None)
                except Exception as e:
                    return (client_id, False, str(e))

        # Filter out excluded client first
        targets = [
            (cid, conn) for cid, conn in connections
            if not (exclude_client and cid == exclude_client)
        ]

        results = await asyncio.gather(*[
            send_with_semaphore(cid, conn) for cid, conn in targets
        ])

        # Process results
        for client_id, success, error in results:
            if success:
                result.succeeded += 1
            else:
                result.failed += 1
                result.errors.append((client_id, error or "Unknown error"))
                self._logger.debug(f"Broadcast failed for {client_id}: {error}")
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
        result: list[str] = []
        for shard in self._shards:
            result.extend(shard.connections.keys())
        return result

    def get_connection_count(self) -> int:
        """Get number of currently active connections.

        Returns:
            Number of connected clients
        """
        return sum(len(shard.connections) for shard in self._shards)

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
        ).hexdigest()
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
            ).hexdigest()

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

        # Close all connections from all shards
        closed_count = 0
        failed_count = 0

        # Collect connections from all shards
        connection_list: list[ConnectionMetadata] = []
        for shard in self._shards:
            async with shard.lock:
                connection_list.extend(list(shard.connections.values()))

        async def close_with_semaphore(conn: ConnectionMetadata) -> bool:
            async with self._cleanup_semaphore:
                try:
                    await conn.websocket.close(code=1001, reason="server shutdown")
                    return True
                except Exception as e:
                    self._logger.debug(f"Error closing {conn.client_id}: {e}")
                    return False

        results = await asyncio.gather(*[
            close_with_semaphore(conn) for conn in connection_list
        ])
        closed_count = sum(1 for r in results if r is True)
        failed_count = len(results) - closed_count

        # Clear all shards
        for shard in self._shards:
            async with shard.lock:
                shard.connections.clear()

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

                # Get snapshot of connections from all shards
                connections: list[tuple[str, ConnectionMetadata]] = []
                for shard in self._shards:
                    async with shard.lock:
                        connections.extend(list(shard.connections.items()))

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
            shard = self._get_shard(client_id)
            async with shard.lock:
                if client_id in shard.connections:
                    conn = shard.connections[client_id]
                    if conn.pending_pong:
                        self._structured_logger.log_heartbeat(
                            "pong_timeout", client_id
                        )
                        # Release lock before disconnect to avoid deadlock
                        should_disconnect = True
                    else:
                        should_disconnect = False
                else:
                    should_disconnect = False

            if should_disconnect:
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
        shard = self._get_shard(client_id)

        async with shard.lock:
            if client_id not in shard.connections:
                return

            conn = shard.connections[client_id]
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

    def _calculate_total_memory(self) -> int:
        """Calculate total memory usage across all connections.

        Returns:
            Total memory in bytes
        """
        total = 0
        for shard in self._shards:
            for conn in shard.connections.values():
                total += conn.estimate_memory_size()
        return total

    def get_memory_usage(self) -> dict[str, int | float]:
        """Get current memory usage statistics.

        Returns:
            Dictionary with:
            - total_memory: Total bytes used by all connections
            - connection_count: Number of active connections
            - per_connection_average: Average bytes per connection
        """
        total_memory = 0
        connection_count = 0

        for shard in self._shards:
            for conn in shard.connections.values():
                total_memory += conn.estimate_memory_size()
                connection_count += 1

        per_connection_average: float = 0.0
        if connection_count > 0:
            per_connection_average = total_memory / connection_count

        return {
            "total_memory": total_memory,
            "connection_count": connection_count,
            "per_connection_average": per_connection_average,
        }

    async def set_user_data(self, client_id: str, key: str, value: Any) -> None:
        """Set user data for a connection.

        Args:
            client_id: Client identifier
            key: Data key to set
            value: Value to store

        Raises:
            ConnectionNotFoundError: If client not found
            MemoryLimitExceededError: If setting data would exceed memory limit
        """
        shard = self._get_shard(client_id)

        async with shard.lock:
            if client_id not in shard.connections:
                raise ConnectionNotFoundError(f"Connection not found: {client_id}")

            conn = shard.connections[client_id]

            # Check if adding this data would exceed per-connection limit
            if self.config.max_memory_per_connection is not None:
                # Temporarily set the value to estimate new size
                old_value = conn.user_data.get(key)
                conn.user_data[key] = value
                new_memory = conn.estimate_memory_size()

                if new_memory > self.config.max_memory_per_connection:
                    # Restore old value and raise error
                    if old_value is not None:
                        conn.user_data[key] = old_value
                    else:
                        del conn.user_data[key]
                    raise MemoryLimitExceededError(
                        f"Setting user_data would exceed per-connection memory limit. "
                        f"New size: {new_memory}, Limit: {self.config.max_memory_per_connection}"
                    )
                # Value is already set, no need to set again
            else:
                conn.user_data[key] = value

    async def get_user_data(self, client_id: str, key: str) -> Any | None:
        """Get user data for a connection.

        Args:
            client_id: Client identifier
            key: Data key to retrieve

        Returns:
            Value if found, None otherwise
        """
        shard = self._get_shard(client_id)

        async with shard.lock:
            if client_id not in shard.connections:
                return None

            return shard.connections[client_id].user_data.get(key)

    async def _evict_for_memory(self, needed_bytes: int) -> None:
        """Evict connections to free up memory.

        Eviction is based on config.memory_eviction_policy:
        - OLDEST: Evict connection with smallest created_at
        - LRU: Evict connection with smallest last_activity
        - FIFO: Same as OLDEST

        Args:
            needed_bytes: Minimum bytes to free
        """
        freed_bytes = 0

        while freed_bytes < needed_bytes:
            # Find candidate for eviction across all shards
            candidate: ConnectionMetadata | None = None
            candidate_client_id: str | None = None

            for shard in self._shards:
                for client_id, conn in shard.connections.items():
                    if candidate is None:
                        candidate = conn
                        candidate_client_id = client_id
                    else:
                        # Compare based on eviction policy
                        if self.config.memory_eviction_policy in (
                            MemoryEvictionPolicy.OLDEST,
                            MemoryEvictionPolicy.FIFO,
                        ):
                            # Evict oldest by created_at
                            if conn.created_at < candidate.created_at:
                                candidate = conn
                                candidate_client_id = client_id
                        elif self.config.memory_eviction_policy == MemoryEvictionPolicy.LRU:
                            # Evict least recently used by last_activity
                            if conn.last_activity < candidate.last_activity:
                                candidate = conn
                                candidate_client_id = client_id

            if candidate is None or candidate_client_id is None:
                # No more connections to evict
                break

            # Evict the candidate
            freed_bytes += candidate.estimate_memory_size()
            self._logger.info(
                f"Evicting connection {candidate_client_id} due to memory pressure "
                f"(policy: {self.config.memory_eviction_policy.value})"
            )

            try:
                await self.disconnect(candidate_client_id)
            except ConnectionNotFoundError:
                # Connection already removed
                pass
