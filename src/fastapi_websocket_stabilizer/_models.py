"""Internal data models for WebSocket connections and tokens."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import WebSocket


@dataclass
class ConnectionMetadata:
    """Metadata for a tracked WebSocket connection.

    Attributes:
        client_id: Unique identifier for the connection
        websocket: FastAPI WebSocket instance
        created_at: Unix timestamp when connection was established
        last_activity: Unix timestamp of last heartbeat or message
        pending_pong: Whether a ping has been sent without a pong response
        pong_timeout_task: Asyncio task for pong timeout detection
        metadata: Additional user-provided metadata
        user_data: Custom user data attached to connection
    """

    client_id: str
    websocket: WebSocket
    created_at: float
    last_activity: float = field(default_factory=time.time)
    pending_pong: bool = False
    pong_timeout_task: Optional[asyncio.Task[None]] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    user_data: dict[str, Any] = field(default_factory=dict)

    def get_duration_seconds(self) -> float:
        """Get connection duration in seconds.

        Returns:
            Time elapsed since connection establishment
        """
        return time.time() - self.created_at

    def update_last_activity(self) -> None:
        """Update last activity timestamp to current time."""
        self.last_activity = time.time()


@dataclass
class ReconnectToken:
    """Reconnection token for session resumption.

    Attributes:
        token: The actual token string
        client_id: Client identifier associated with token
        created_at: Unix timestamp when token was generated
        expires_at: Unix timestamp when token expires
        nonce: Random nonce to prevent replay attacks
    """

    token: str
    client_id: str
    created_at: float
    expires_at: float
    nonce: str

    def is_expired(self) -> bool:
        """Check if token has expired.

        Returns:
            True if token expiration time has passed
        """
        return time.time() > self.expires_at

    def get_ttl_seconds(self) -> float:
        """Get remaining time-to-live in seconds.

        Returns:
            Seconds until expiration (negative if already expired)
        """
        return self.expires_at - time.time()
