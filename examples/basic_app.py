"""Basic chat application demonstrating WebSocket stabilizer usage."""

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from fastapi_websocket_stabilizer import WebSocketConnectionManager, WebSocketConfig


# Initialize manager with custom config
ws_config = WebSocketConfig(
    heartbeat_interval=30.0,
    heartbeat_timeout=60.0,
    max_connections=100,
)
ws_manager = WebSocketConnectionManager(ws_config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage WebSocket manager lifecycle."""
    # Startup
    await ws_manager.start()
    print("WebSocket manager started")
    yield
    # Shutdown
    report = await ws_manager.graceful_shutdown()
    print(f"Shutdown: closed={report.closed_count}, failed={report.failed_count}")


app = FastAPI(lifespan=lifespan)


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """WebSocket endpoint with stabilization layer."""
    await websocket.accept()

    try:
        # Register connection with manager
        await ws_manager.connect(client_id, websocket)

        # Send welcome message
        await ws_manager.send_to_client(
            client_id,
            json.dumps(
                {
                    "type": "connection",
                    "message": f"Welcome {client_id}! Connected clients: "
                    f"{ws_manager.get_connection_count()}",
                }
            ),
        )

        # Broadcast join notification
        await ws_manager.broadcast(
            json.dumps(
                {
                    "type": "user_joined",
                    "user": client_id,
                    "total_users": ws_manager.get_connection_count(),
                }
            ),
            exclude_client=client_id,
        )

        # Handle incoming messages
        while True:
            data = await websocket.receive_text()

            # Handle heartbeat pongs
            if data == "pong":
                await ws_manager.handle_pong(client_id)
                continue

            # Broadcast message to all clients
            message = json.dumps(
                {"type": "message", "user": client_id, "content": data}
            )

            await ws_manager.broadcast(message)

    except WebSocketDisconnect:
        # Cleanup handled by manager
        await ws_manager.disconnect(client_id)

        # Notify other clients
        await ws_manager.broadcast(
            json.dumps(
                {
                    "type": "user_left",
                    "user": client_id,
                    "total_users": ws_manager.get_connection_count(),
                }
            )
        )

    except Exception as e:
        print(f"WebSocket error for {client_id}: {e}")
        try:
            await ws_manager.disconnect(client_id)
        except Exception:
            pass


@app.get("/stats")
async def get_stats():
    """Get WebSocket connection statistics."""
    return {
        "active_connections": ws_manager.get_connection_count(),
        "connected_clients": ws_manager.get_active_connections(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
