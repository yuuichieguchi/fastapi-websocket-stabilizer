# FastAPI WebSocket Stabilizer

A production-ready WebSocket stabilization layer for FastAPI applications that provides robust connection lifecycle management, automatic heartbeat/ping-pong detection, graceful shutdown, and reconnection support.

## Motivation

Building reliable WebSocket applications in production requires handling many edge cases:

- **Connection timeouts**: Proxy servers often have idle connection timeouts (60-90 seconds)
- **Dead connections**: Network partitions can leave zombie connections that silently hang
- **Ungraceful restarts**: Server restarts need clean connection closure to prevent client reconnection storms
- **Load balancer issues**: Behind ALBs/NLBs, stale connections accumulate without proper heartbeat
- **Cloud platform quirks**: Azure App Service, AWS Lambda, and other serverless platforms have specific WebSocket constraints
- **Session resumption**: Clients may disconnect due to network switching and need to restore state

This library abstracts away these concerns with a simple, production-tested API.

## Key Features

- ✅ **Connection Management**: Automatic tracking of active WebSocket connections
- ✅ **Heartbeat/Ping-Pong**: Configurable automatic heartbeat detection of dead connections
- ✅ **Graceful Shutdown**: Clean connection closure with proper async task cleanup
- ✅ **Reconnection Tokens**: Server-side session token support for seamless reconnection
- ✅ **Structured Logging**: Cloud-friendly logging for easy monitoring and debugging
- ✅ **Broadcast Messaging**: Send messages to individual clients, groups, or all clients
- ✅ **Connection Limits**: Configurable max connections with automatic enforcement
- ✅ **Type Safety**: Full type hints for all public APIs (Python 3.10+)
- ✅ **Zero Dependencies**: Only depends on FastAPI (which provides WebSocket support)
- ✅ **Sharded Connection Pools**: Reduce lock contention under high load with configurable sharding
- ✅ **Memory Management**: Track and limit memory usage per connection with eviction policies
- ✅ **Concurrency Control**: Semaphore-based limiting for cleanup and broadcast operations

## Installation

Install via pip:

```bash
pip install fastapi-websocket-stabilizer
```

Or from source:

```bash
git clone https://github.com/fastapi-websocket-stabilizer/fastapi-websocket-stabilizer.git
cd fastapi-websocket-stabilizer
pip install -e ".[dev]"
```

## Quick Start

### Basic Chat Application

```python
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi_websocket_stabilizer import WebSocketConnectionManager, WebSocketConfig

# Configure the manager
ws_config = WebSocketConfig(
    heartbeat_interval=30.0,
    heartbeat_timeout=60.0,
    max_connections=100,
)
ws_manager = WebSocketConnectionManager(ws_config)

# Setup lifespan events
@asynccontextmanager
async def lifespan(app: FastAPI):
    await ws_manager.start()
    yield
    await ws_manager.graceful_shutdown()

app = FastAPI(lifespan=lifespan)

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await websocket.accept()

    try:
        # Register connection
        await ws_manager.connect(client_id, websocket)

        # Handle messages
        while True:
            data = await websocket.receive_text()

            # Handle heartbeat responses
            if data == "pong":
                await ws_manager.handle_pong(client_id)
                continue

            # Broadcast to all clients
            await ws_manager.broadcast(json.dumps({
                "user": client_id,
                "message": data
            }))

    except WebSocketDisconnect:
        await ws_manager.disconnect(client_id)
```

## Usage Patterns

### Pattern 1: Simple Broadcast

```python
# Send message to all clients
await ws_manager.broadcast("System message")

# Send to all except sender
await ws_manager.broadcast(
    json.dumps({"message": "chat"}),
    exclude_client=sender_id
)
```

### Pattern 2: Direct Messaging

```python
# Send to specific client
success = await ws_manager.send_to_client(client_id, "Direct message")

if not success:
    print(f"Client {client_id} is no longer connected")
```

### Pattern 3: Reconnection Support

```python
@app.post("/reconnect-token/{client_id}")
async def get_reconnect_token(client_id: str):
    """Get a token before intentional disconnection."""
    token = ws_manager.generate_reconnect_token(client_id)
    return {"token": token}

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str, token: str = None):
    await websocket.accept()

    # Restore session if token provided
    if token:
        try:
            restored_id = await ws_manager.validate_reconnect_token(token)
            # Continue with restored session
        except TokenExpiredError:
            await websocket.close(code=1008, reason="Token expired")
            return

    await ws_manager.connect(client_id, websocket)
```

### Pattern 4: Statistics and Monitoring

```python
@app.get("/stats")
async def get_stats():
    return {
        "active_connections": ws_manager.get_connection_count(),
        "client_ids": ws_manager.get_active_connections(),
        "memory": ws_manager.get_memory_usage(),  # v1.1.0+
    }
```

### Pattern 5: Per-Connection User Data (v1.1.0+)

```python
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await websocket.accept()
    await ws_manager.connect(client_id, websocket)

    # Store user state per connection
    await ws_manager.set_user_data(client_id, "username", "Alice")
    await ws_manager.set_user_data(client_id, "room", "general")

    # Retrieve later
    username = await ws_manager.get_user_data(client_id, "username")
    print(f"{username} joined")  # "Alice joined"
```

## Configuration Reference

### WebSocketConfig

All configuration options with defaults:

```python
from fastapi_websocket_stabilizer import WebSocketConfig
from datetime import timedelta

config = WebSocketConfig(
    # Heartbeat settings
    heartbeat_interval=30.0,      # Seconds between pings
    heartbeat_timeout=60.0,       # Seconds to wait for pong response
    heartbeat_type="ping",        # Type of heartbeat ("ping" or "custom")

    # Connection limits
    max_connections=None,         # Max concurrent connections (None = unlimited)
    max_message_size=1048576,     # Max message size in bytes (1MB default)

    # Reconnection
    reconnect_token_ttl=timedelta(hours=1),  # Token lifetime
    enable_reconnect=True,        # Enable token support

    # Compression
    compression="deflate",        # "deflate" or "none"

    # Logging
    log_level="INFO",             # DEBUG, INFO, WARNING, ERROR, CRITICAL
    enable_metrics=False,         # Collect metrics (experimental)

    # Sharding (reduces lock contention under high load)
    num_shards=16,                # Number of connection pool shards

    # Concurrency control (prevents cleanup/broadcast storms)
    max_concurrent_cleanup=100,   # Max concurrent connection closures
    max_concurrent_broadcast=1000, # Max concurrent broadcast sends

    # Memory management
    max_memory_per_connection=None,  # Max bytes per connection (None = unlimited)
    max_total_memory=None,           # Max total bytes (None = unlimited)
    memory_eviction_policy="lru",    # "lru", "fifo", or "oldest"
    memory_check_interval=60.0,      # Seconds between memory checks
)
```

### Example Configurations

#### High-frequency trading/gaming (low latency)
```python
WebSocketConfig(
    heartbeat_interval=5.0,       # More frequent heartbeats
    heartbeat_timeout=10.0,       # Detect dead connections quickly
    max_connections=10000,        # Expect many connections
)
```

#### Behind slow proxy (e.g., old reverse proxy)
```python
WebSocketConfig(
    heartbeat_interval=45.0,      # Slower heartbeat (adapt to proxy timeout)
    heartbeat_timeout=90.0,
    compression="none",           # Reduce overhead
)
```

#### Serverless (Azure Functions, AWS Lambda)
```python
WebSocketConfig(
    heartbeat_interval=20.0,      # Serverless platforms have stricter timeouts
    max_connections=500,          # Limited resource per instance
)
```

#### High-load production (10k+ connections)
```python
from fastapi_websocket_stabilizer import WebSocketConfig, MemoryEvictionPolicy

WebSocketConfig(
    heartbeat_interval=30.0,
    max_connections=50000,
    num_shards=32,                      # More shards for reduced lock contention
    max_concurrent_cleanup=50,          # Prevent cleanup storms
    max_concurrent_broadcast=500,       # Control broadcast load
    max_memory_per_connection=1024*1024,  # 1MB per connection
    max_total_memory=1024*1024*1024,      # 1GB total
    memory_eviction_policy=MemoryEvictionPolicy.LRU,  # Evict least recently used
)
```

## Deployment Guide

### Local Development

```bash
# Install dependencies
pip install "fastapi[standard]" uvicorn

# Run example
uvicorn examples.basic_app:app --reload

# Test with curl or WebSocket client
# ws://localhost:8000/ws/user123
```

### Behind Reverse Proxy (nginx, Apache)

**nginx configuration:**
```nginx
upstream app {
    server localhost:8000;
}

server {
    listen 80;

    location /ws/ {
        proxy_pass http://app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        # Set read timeout higher than heartbeat timeout + margin
    }
}
```

**Key considerations:**
- Ensure proxy `read_timeout` and `send_timeout` exceed your `heartbeat_interval`
- Set to at least 2x your `heartbeat_interval` for safety
- Default is often 60s, which may be too low; increase to 300s+ for long-lived connections

### AWS Application Load Balancer (ALB)

**Target Group Configuration:**
- **Deregistration delay**: 30-60 seconds
- **Connection idle timeout**: 60+ seconds (ALB default is 60s)

**Application Configuration:**
```python
config = WebSocketConfig(
    heartbeat_interval=25.0,      # Less than ALB's 60s default
    heartbeat_timeout=50.0,
)
```

### Azure App Service

Azure App Service has specific WebSocket constraints:

**Settings in Azure Portal:**
- Enable WebSocket: ON
- Connection timeout (Web socket idle timeout): 600 seconds

**Application Configuration:**
```python
config = WebSocketConfig(
    heartbeat_interval=30.0,      # Well within Azure's limits
    max_connections=1000,         # App Service tier dependent
)
```

**Deployment via Docker:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Kubernetes

**ConfigMap for configuration:**
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: websocket-config
data:
  heartbeat_interval: "30"
  heartbeat_timeout: "60"
  max_connections: "1000"
```

**Deployment:**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: websocket-app
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: app
        image: websocket-app:1.0
        ports:
        - containerPort: 8000
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 10
```

### Docker Compose

```yaml
version: '3.8'

services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      LOG_LEVEL: INFO
    command: uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
```

## Troubleshooting

### Connections Disconnecting Unexpectedly

**Problem**: Clients disconnect after ~60 seconds of inactivity

**Solution**: Reduce `heartbeat_interval` below your proxy/firewall timeout
```python
# Adjust based on your environment's idle timeout
WebSocketConfig(heartbeat_interval=25.0)  # < 60s proxy timeout
```

### High CPU Usage

**Problem**: Manager consuming excessive CPU

**Solution**: Reduce heartbeat frequency or use larger batches
```python
WebSocketConfig(
    heartbeat_interval=60.0,  # Increase from default 30s
    max_connections=500,      # Reduce if possible
)
```

### "Connection Limit Exceeded" Errors

**Problem**: New connections rejected with limit errors

**Solutions**:
1. Increase `max_connections`:
   ```python
   WebSocketConfig(max_connections=5000)
   ```
2. Implement graceful connection rejection with backoff
3. Scale horizontally (multiple app instances)

### Reconnection Tokens Not Working

**Problem**: Token validation fails repeatedly

**Solutions**:
- Check token hasn't expired: `tokenExpiredError` means TTL passed
- Verify same `WebSocketConnectionManager` instance used for both generation and validation
- Ensure client sends token in query parameter or auth header

### Memory Leaks

**Problem**: Memory usage increases over time

**Solutions**:
1. Ensure `graceful_shutdown()` is called during app shutdown
2. Check that disconnected clients are properly cleaned up
3. Monitor token storage (should be auto-cleaned every 5 minutes)

## API Reference

### WebSocketConnectionManager

```python
class WebSocketConnectionManager:
    def __init__(self, config: Optional[WebSocketConfig] = None) -> None:
        """Initialize manager with optional config."""

    async def start(self) -> None:
        """Start heartbeat and cleanup tasks."""

    async def connect(
        self,
        client_id: str,
        websocket: WebSocket,
        metadata: Optional[dict] = None
    ) -> None:
        """Register a new WebSocket connection."""

    async def disconnect(self, client_id: str) -> None:
        """Gracefully disconnect a client."""

    async def send_to_client(
        self,
        client_id: str,
        message: str | dict
    ) -> bool:
        """Send message to specific client."""

    async def broadcast(
        self,
        message: str | dict,
        exclude_client: Optional[str] = None
    ) -> BroadcastResult:
        """Send message to all clients."""

    def get_active_connections(self) -> list[str]:
        """Get list of connected client IDs."""

    def get_connection_count(self) -> int:
        """Get number of active connections."""

    def generate_reconnect_token(self, client_id: str) -> str:
        """Generate a reconnection token."""

    async def validate_reconnect_token(self, token: str) -> Optional[str]:
        """Validate token and return client_id."""

    async def handle_pong(self, client_id: str) -> None:
        """Handle pong response from client."""

    async def graceful_shutdown(self, timeout: float = 30.0) -> ShutdownReport:
        """Shut down manager and close all connections."""

    # Memory management methods (v1.1.0+)
    def get_memory_usage(self) -> dict[str, int | float]:
        """Get memory usage statistics.
        Returns: {total_memory, connection_count, per_connection_average}"""

    async def set_user_data(self, client_id: str, key: str, value: Any) -> None:
        """Set user data for a connection (with memory limit checking)."""

    async def get_user_data(self, client_id: str, key: str) -> Any | None:
        """Get user data for a connection."""
```

### Exceptions

```python
from fastapi_websocket_stabilizer import (
    WebSocketStabilizerError,          # Base exception
    ConnectionNotFoundError,            # Client not found
    ConnectionLimitExceededError,       # Max connections reached
    ConnectionTimeoutError,             # Heartbeat timeout
    TokenExpiredError,                  # Token TTL exceeded
    InvalidTokenError,                  # Token invalid/tampered
    BroadcastFailedError,               # Broadcast failed
    ShutdownError,                      # Shutdown error
    MemoryLimitExceededError,           # Memory limit exceeded (v1.1.0+)
)
```

## Roadmap

### Completed (v1.1.0)

- [x] Sharded connection pools for high-load scenarios
- [x] Memory management with eviction policies
- [x] Concurrency control for cleanup/broadcast storms

### Planned Features

- [ ] Metrics collection (Prometheus-compatible)
- [ ] Connection lifecycle hooks (on_connect, on_disconnect callbacks)
- [ ] Message compression strategies
- [ ] Distributed mode (Redis-backed connection registry for multi-instance deployments)
- [ ] Built-in rate limiting
- [ ] Automatic reconnection client library (JavaScript/TypeScript)

### Possible Enhancements

- OpenTelemetry instrumentation
- Support for WebSocket subprotocols
- Custom heartbeat message types
- Connection grouping/rooms functionality
- Pub/Sub integration

## Contributing

We welcome contributions! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Write tests for new functionality
4. Ensure all tests pass: `pytest tests/`
5. Format code with black: `black src/ tests/`
6. Lint with ruff: `ruff check src/ tests/`
7. Commit with clear messages
8. Push and create a Pull Request

### Development Setup

```bash
# Clone repository
git clone https://github.com/fastapi-websocket-stabilizer/fastapi-websocket-stabilizer.git
cd fastapi-websocket-stabilizer

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run specific test
pytest tests/test_manager.py::TestConnectionManager::test_should_track_connection_when_connect_called -v

# Check types
mypy src/

# Format code
black src/ tests/

# Lint
ruff check src/ tests/
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

- 📖 [Full Documentation](https://github.com/fastapi-websocket-stabilizer/fastapi-websocket-stabilizer)
- 🐛 [Issue Tracker](https://github.com/fastapi-websocket-stabilizer/fastapi-websocket-stabilizer/issues)
- 💬 [Discussions](https://github.com/fastapi-websocket-stabilizer/fastapi-websocket-stabilizer/discussions)

## Acknowledgments

Inspired by production WebSocket patterns in large-scale FastAPI deployments, with particular consideration for:

- FastAPI's excellent WebSocket support
- Real-world challenges in cloud environments
- Community feedback on WebSocket reliability

## Version History

### 1.1.0
- **Sharded Connection Pools**: Reduce lock contention with configurable `num_shards`
- **Concurrency Control**: Semaphore-based `max_concurrent_cleanup` and `max_concurrent_broadcast`
- **Memory Management**: Per-connection and total memory limits with eviction policies (LRU, FIFO, OLDEST)
- New methods: `get_memory_usage()`, `set_user_data()`, `get_user_data()`
- New exception: `MemoryLimitExceededError`
- Bug fixes: `hmac.hexdigest()` for Python 3.14 compatibility, reconnection within limits

### 1.0.0
- Stable release

### 0.1.1
- Fix GitHub URLs and setuptools compatibility

### 0.1.0 (Initial Release)
- Core connection management
- Heartbeat/ping-pong mechanism
- Reconnection token support
- Graceful shutdown
- Comprehensive tests and documentation
