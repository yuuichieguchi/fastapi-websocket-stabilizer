# Quick Start Guide

## Installation

```bash
pip install fastapi-websocket-stabilizer
```

## 5-Minute Example

Create a file `app.py`:

```python
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi_websocket_stabilizer import WebSocketConnectionManager

# Create manager
manager = WebSocketConnectionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: start background tasks
    await manager.start()
    yield
    # Shutdown: close all connections gracefully
    await manager.graceful_shutdown()

app = FastAPI(lifespan=lifespan)

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await websocket.accept()

    try:
        # Register connection
        await manager.connect(client_id, websocket)

        while True:
            data = await websocket.receive_text()

            # Handle heartbeat pongs
            if data == "pong":
                await manager.handle_pong(client_id)
                continue

            # Broadcast message
            await manager.broadcast(json.dumps({
                "from": client_id,
                "message": data
            }))

    except WebSocketDisconnect:
        await manager.disconnect(client_id)
```

Run it:

```bash
pip install uvicorn
uvicorn app:app --reload
```

Connect with a WebSocket client:

```bash
# Install websocat: brew install websocat (macOS) or apt-get install websocat (Linux)
websocat ws://localhost:8000/ws/alice
# Type: hello world
# See: {"from": "alice", "message": "hello world"}
```

## Common Patterns

### Pattern 1: Direct Message to Specific Client

```python
success = await manager.send_to_client("alice", "Hello Alice!")
if success:
    print("Message sent")
else:
    print("Client not connected")
```

### Pattern 2: Broadcast to Everyone Except Sender

```python
await manager.broadcast(
    json.dumps({"message": "New user joined"}),
    exclude_client=sender_id
)
```

### Pattern 3: Get Connection Stats

```python
@app.get("/stats")
async def stats():
    return {
        "connected": manager.get_connection_count(),
        "clients": manager.get_active_connections()
    }
```

### Pattern 4: Session Recovery with Tokens

```python
# Generate token before disconnect
@app.post("/token/{client_id}")
async def get_token(client_id: str):
    token = manager.generate_reconnect_token(client_id)
    return {"token": token}

# Reconnect with token
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str, token: str = None):
    await websocket.accept()

    if token:
        try:
            restored_id = await manager.validate_reconnect_token(token)
            # Session restored!
        except Exception as e:
            print(f"Token invalid: {e}")

    await manager.connect(client_id, websocket)
    # ... rest of endpoint
```

## Configuration

Customize heartbeat behavior:

```python
from fastapi_websocket_stabilizer import WebSocketConfig

config = WebSocketConfig(
    heartbeat_interval=30.0,      # Send ping every 30 seconds
    heartbeat_timeout=60.0,       # Expect pong within 60 seconds
    max_connections=1000,         # Limit to 1000 concurrent connections
)

manager = WebSocketConnectionManager(config)
```

## Testing Your Implementation

### Test 1: Connection Tracking

```python
@pytest.mark.asyncio
async def test_connection():
    manager = WebSocketConnectionManager()
    ws = AsyncMock()

    await manager.connect("test_client", ws)
    assert "test_client" in manager.get_active_connections()

    await manager.disconnect("test_client")
    assert "test_client" not in manager.get_active_connections()
```

### Test 2: Broadcast

```python
@pytest.mark.asyncio
async def test_broadcast():
    manager = WebSocketConnectionManager()
    ws1 = AsyncMock()
    ws2 = AsyncMock()

    await manager.connect("client1", ws1)
    await manager.connect("client2", ws2)

    result = await manager.broadcast("hello")

    assert result.succeeded == 2
    ws1.send_text.assert_called_with("hello")
    ws2.send_text.assert_called_with("hello")
```

## Deployment Checklist

- [ ] Set `heartbeat_interval` lower than your proxy/firewall timeout (recommend 25s for 60s proxy)
- [ ] Enable graceful shutdown (use lifespan context manager)
- [ ] Configure logging level (default INFO is fine)
- [ ] Set `max_connections` based on server capacity
- [ ] Test reconnection tokens if using them
- [ ] Monitor connection stats in production

## Troubleshooting

**Clients disconnect after ~60 seconds?**
```python
# Your proxy has 60s timeout. Lower heartbeat interval:
WebSocketConfig(heartbeat_interval=25.0)
```

**Memory growing over time?**
```python
# Ensure graceful_shutdown() is called on app exit:
@asynccontextmanager
async def lifespan(app: FastAPI):
    await manager.start()
    yield
    await manager.graceful_shutdown()  # This is important!
```

**Can't receive pong?**
```python
# Make sure client sends "pong" on ping:
data = await websocket.receive_json()
if data.get("type") == "ping":
    await websocket.send_text("pong")
```

## Next Steps

- Read [README.md](README.md) for full documentation
- Review [ARCHITECTURE.md](ARCHITECTURE.md) for design details
- Check [examples/](examples/) for more complex applications
- Run tests: `pytest tests/`
