# Architecture & Design

## Overview

`fastapi-websocket-stabilizer` is a production-ready WebSocket connection manager for FastAPI that abstracts common connection lifecycle patterns, heartbeat management, and graceful shutdown procedures.

## Project Structure

```
fastapi-websocket-stabilizer/
├── src/fastapi_websocket_stabilizer/
│   ├── __init__.py              # Public API exports
│   ├── manager.py               # Main WebSocketConnectionManager class
│   ├── config.py                # Configuration models and result types
│   ├── exceptions.py            # Custom exception hierarchy
│   ├── logging_utils.py         # Structured logging utilities
│   └── _models.py               # Internal data models (private)
├── examples/
│   ├── basic_app.py             # Simple chat application demo
│   └── reconnect_example.py     # Reconnection token demo
├── tests/
│   ├── __init__.py
│   └── test_manager.py          # Comprehensive test suite
├── pyproject.toml               # Project metadata and dependencies
├── README.md                     # User documentation
├── LICENSE                       # MIT License
└── .gitignore                   # Git ignore rules
```

## Core Components

### 1. WebSocketConnectionManager (`manager.py`)

**Responsibility**: Central abstraction for managing WebSocket connection lifecycle.

**Key Methods**:
- `connect()` - Register a new WebSocket connection
- `disconnect()` - Gracefully close and untrack a connection
- `send_to_client()` - Send message to specific client
- `broadcast()` - Send message to all clients
- `generate_reconnect_token()` - Create resumable session token
- `validate_reconnect_token()` - Validate and consume token
- `handle_pong()` - Handle heartbeat pong responses
- `graceful_shutdown()` - Clean closure of all connections
- `start()` - Initialize background tasks

**Thread Safety**:
- Uses `asyncio.Lock` to protect `_connections` dict during modifications
- Uses `asyncio.Lock` to protect `_tokens` dict
- Snapshot-based operations prevent deadlocks during broadcasts
- No blocking calls in async methods

**Background Tasks**:
1. **Heartbeat Worker** (`_heartbeat_worker`):
   - Sends ping frames at configured interval
   - Detects missing pongs (connection timeout)
   - Auto-disconnects stale connections

2. **Token Cleanup Worker** (`_token_cleanup_worker`):
   - Periodically removes expired reconnection tokens
   - Runs every 5 minutes
   - Prevents unbounded memory growth

### 2. Configuration (`config.py`)

**WebSocketConfig**: Dataclass for manager configuration
- **Heartbeat settings**: `heartbeat_interval`, `heartbeat_timeout`, `heartbeat_type`
- **Connection limits**: `max_connections`, `max_message_size`
- **Reconnection**: `reconnect_token_ttl`, `enable_reconnect`
- **Compression**: WebSocket compression mode
- **Logging**: `log_level`, `enable_metrics`

**Result Models**:
- `BroadcastResult`: Statistics from broadcast operations
- `ShutdownReport`: Statistics from graceful shutdown

**Design Pattern**: Post-init validation ensures config values are sensible before use.

### 3. Exceptions (`exceptions.py`)

Custom exception hierarchy for specific error cases:

```
WebSocketStabilizerError (base)
├── ConnectionNotFoundError
├── ConnectionLimitExceededError
├── ConnectionTimeoutError
├── TokenExpiredError
├── InvalidTokenError
├── BroadcastFailedError
└── ShutdownError
```

Allows caller to handle specific scenarios differently.

### 4. Logging (`logging_utils.py`)

**StructuredLogger**: Cloud-friendly logging with structured metadata.

**Log Types**:
- `log_connection()` - Connection events (connected, disconnected, timeout)
- `log_broadcast()` - Broadcast delivery statistics
- `log_heartbeat()` - Heartbeat events (ping sent, pong received, timeout)
- `log_token()` - Token lifecycle events
- `log_error()` - Error conditions with context

**Format**: Structured logs with event type and key-value pairs, suitable for:
- ELK Stack (Elasticsearch/Kibana)
- Datadog
- Azure Application Insights
- CloudWatch

### 5. Internal Models (`_models.py`)

**ConnectionMetadata**: Per-connection state tracking
- `client_id`: Unique identifier
- `websocket`: WebSocket instance
- `created_at`: Connection establishment timestamp
- `last_activity`: Last message/heartbeat timestamp
- `pending_pong`: Boolean flag for heartbeat timeout detection
- `pong_timeout_task`: Asyncio task for timeout cancellation
- `metadata`: User-provided metadata
- `user_data`: Custom application data

**ReconnectToken**: Reconnection token representation
- `token`: The actual token string
- `client_id`: Associated client ID
- `created_at`: Generation timestamp
- `expires_at`: Expiration timestamp
- `nonce`: Replay attack prevention

## Data Flow

### Connection Lifecycle

```
┌─ Client connects to /ws/client_id ─┐
│                                     │
│  await websocket.accept()           │
│  await ws_manager.connect(...)      ├─→ Client added to _connections dict
│                                     │
│  Event: connection/connected (log)  │
└─────────────────────────────────────┘

       ↓ (while True loop)

┌────────────────────────────────────┐
│ Client sends/receives messages     │
│ Heartbeat pings flow through       │ ← Managed by _heartbeat_worker
│ ┌──────────────────────────────────┤
│ └─→ await ws_manager.send_to_client()
│ └─→ await ws_manager.broadcast()
│ └─→ await ws_manager.handle_pong()
└────────────────────────────────────┘

       ↓ (on disconnect/error)

┌────────────────────────────────────┐
│ await ws_manager.disconnect(...)   ├─→ Client removed from dict
│ WebSocket closed gracefully        │
│ Event: connection/disconnected     │
└────────────────────────────────────┘
```

### Heartbeat Mechanism

```
Manager initialized
  └─→ _heartbeat_worker task created

Every heartbeat_interval seconds:
  1. Take snapshot of all connections
  2. For each connection:
     ├─ If pending_pong was True → timeout detected → disconnect
     ├─ Else → send ping frame
     ├─ Set pending_pong = True
     └─ Schedule _wait_for_pong timeout task

Client receives ping → sends pong
  └─→ handle_pong() called
      ├─ Set pending_pong = False
      └─ Cancel timeout task

Timeout expires (heartbeat_timeout seconds)
  └─→ _wait_for_pong timeout fires
      ├─ If pending_pong still True
      └─ Disconnect client (dead connection)
```

### Reconnection Token Flow

```
Token Generation:
  1. Manager.generate_reconnect_token(client_id)
  2. Create message = timestamp.nonce.client_id
  3. Generate HMAC: hmac_new(secret, message).hex()
  4. Token = message + "." + hmac
  5. Store in _tokens dict with TTL
  6. Return token to client

Token Validation:
  1. Client provides token on reconnect
  2. Manager.validate_reconnect_token(token)
  3. Parse and verify HMAC signature
  4. Check expiration timestamp
  5. Verify token not already used (removed from dict)
  6. Return client_id if valid
  7. Token cleanup worker periodically removes expired tokens
```

## Thread Safety Model

**Asyncio Single-Threaded**: The manager assumes single-threaded asyncio execution.

**Synchronization Points**:
- `_connections_lock`: Protects `_connections` dict
  - Acquired during: connect(), disconnect(), broadcast (read), heartbeat (read)
  - Never held across I/O operations
- `_tokens_lock`: Protects `_tokens` dict
  - Acquired during: generate_reconnect_token(), validate_reconnect_token()

**Snapshot Pattern**:
```python
async with self._connections_lock:
    connections = list(self._connections.items())  # Create snapshot
# Release lock before I/O

for client_id, conn in connections:  # Work with snapshot
    await conn.websocket.send_json(...)  # No lock held
```

This pattern prevents deadlocks while maintaining consistency.

## Error Handling Strategy

**Connection Errors**: When send fails
- Error logged with client_id
- Client auto-disconnected to prevent stale state
- Caller receives `False` or exception depending on context

**Broadcast Partial Failure**:
- Continues sending to all clients despite failures
- Returns `BroadcastResult` with stats and error list
- Allows caller to decide next action

**Graceful Shutdown**:
- All errors caught to ensure cleanup completes
- Returns `ShutdownReport` with success/failure counts
- Logs errors but doesn't raise exceptions

## Configuration Best Practices

### Conservative (Default)
```python
WebSocketConfig(
    heartbeat_interval=30.0,
    heartbeat_timeout=60.0,
)
```
✓ Safe for most deployments
✓ Works behind proxies with 60-90s timeouts
✓ Detects dead connections in ~2 minutes

### Aggressive (Low-latency)
```python
WebSocketConfig(
    heartbeat_interval=5.0,
    heartbeat_timeout=10.0,
)
```
✓ For real-time applications (gaming, trading)
✓ Detects dead connections in ~15 seconds
⚠ May false-positive on slow networks

### Relaxed (High-latency)
```python
WebSocketConfig(
    heartbeat_interval=60.0,
    heartbeat_timeout=120.0,
)
```
✓ For slow/unreliable networks
✓ Reduces network overhead
⚠ Detects dead connections in ~3 minutes

## Testing Strategy

**Unit Tests** (`test_manager.py`):
- Connection tracking (add, remove, list)
- Message sending (individual, broadcast, failures)
- Token generation and validation
- Configuration validation
- Error cases and edge conditions

**Test Approach**:
- Mock WebSocket objects (no real I/O)
- Test async behavior with pytest-asyncio
- Verify state mutations with assertions
- Test error paths explicitly

**Coverage**:
- All public methods tested
- All exception types triggered
- Boundary conditions (max connections, token expiry)
- Error recovery paths

## Future Enhancements

1. **Metrics Collection**
   - Prometheus-compatible metrics
   - Connection count, broadcast stats, error rates

2. **Distributed Mode**
   - Redis-backed connection registry
   - Multi-instance deployments

3. **Lifecycle Hooks**
   - Callbacks on connect/disconnect
   - Custom heartbeat handlers

4. **Message Compression**
   - Per-message compression strategies
   - Compression statistics

5. **Built-in Rate Limiting**
   - Per-client message rate limits
   - Broadcast rate limits

6. **Client Library**
   - JavaScript/TypeScript client
   - Auto-reconnection with exponential backoff
   - Session token management
