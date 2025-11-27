"""Logging utilities for WebSocket stabilizer."""

import logging
from typing import Any


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for the WebSocket stabilizer.

    Args:
        name: Logger name (typically __name__ from calling module)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(f"fastapi_websocket_stabilizer.{name}")


def configure_logging(level: str = "INFO") -> None:
    """Configure logging for the WebSocket stabilizer.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    logger = logging.getLogger("fastapi_websocket_stabilizer")
    logger.setLevel(level.upper())
    logger.addHandler(handler)


class StructuredLogger:
    """Structured logging helper for cloud-friendly log output."""

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize structured logger.

        Args:
            logger: Python logger instance
        """
        self._logger = logger

    def log_connection(
        self,
        event: str,
        client_id: str,
        **kwargs: Any,
    ) -> None:
        """Log connection lifecycle event.

        Args:
            event: Event type (connected, disconnected, timeout, error)
            client_id: Client identifier
            **kwargs: Additional context data
        """
        message = f"[WS_CONNECTION] event={event} client_id={client_id}"
        for key, value in kwargs.items():
            message += f" {key}={value}"
        self._logger.info(message)

    def log_broadcast(
        self,
        total: int,
        succeeded: int,
        failed: int,
        **kwargs: Any,
    ) -> None:
        """Log broadcast operation result.

        Args:
            total: Total recipients
            succeeded: Successful deliveries
            failed: Failed deliveries
            **kwargs: Additional context data
        """
        message = (
            f"[WS_BROADCAST] total={total} succeeded={succeeded} failed={failed}"
        )
        for key, value in kwargs.items():
            message += f" {key}={value}"
        self._logger.info(message)

    def log_heartbeat(
        self,
        event: str,
        client_id: str,
        **kwargs: Any,
    ) -> None:
        """Log heartbeat-related event.

        Args:
            event: Event type (ping_sent, pong_received, timeout)
            client_id: Client identifier
            **kwargs: Additional context data
        """
        message = f"[WS_HEARTBEAT] event={event} client_id={client_id}"
        for key, value in kwargs.items():
            message += f" {key}={value}"
        self._logger.debug(message)

    def log_token(
        self,
        event: str,
        client_id: str,
        **kwargs: Any,
    ) -> None:
        """Log token-related event.

        Args:
            event: Event type (generated, validated, expired, invalid)
            client_id: Client identifier
            **kwargs: Additional context data
        """
        message = f"[WS_TOKEN] event={event} client_id={client_id}"
        for key, value in kwargs.items():
            message += f" {key}={value}"
        self._logger.info(message)

    def log_error(
        self,
        error_type: str,
        client_id: str,
        error_message: str,
        **kwargs: Any,
    ) -> None:
        """Log error event.

        Args:
            error_type: Type of error
            client_id: Client identifier (if applicable)
            error_message: Error message
            **kwargs: Additional context data
        """
        message = (
            f"[WS_ERROR] error_type={error_type} client_id={client_id} "
            f"error={error_message}"
        )
        for key, value in kwargs.items():
            message += f" {key}={value}"
        self._logger.error(message)
