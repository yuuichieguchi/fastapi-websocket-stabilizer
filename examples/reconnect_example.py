"""Example demonstrating reconnection token functionality."""

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from fastapi_websocket_stabilizer import (
    InvalidTokenError,
    TokenExpiredError,
    WebSocketConnectionManager,
    WebSocketConfig,
)


ws_config = WebSocketConfig(
    heartbeat_interval=30.0,
    heartbeat_timeout=60.0,
)
ws_manager = WebSocketConnectionManager(ws_config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage WebSocket manager lifecycle."""
    await ws_manager.start()
    print("WebSocket manager started with reconnection support")
    yield
    report = await ws_manager.graceful_shutdown()
    print(f"Shutdown: closed={report.closed_count}")


app = FastAPI(lifespan=lifespan)


@app.post("/reconnect-token/{client_id}")
async def generate_reconnect_token(client_id: str):
    """Generate a reconnection token for a client.

    This endpoint should be called before intentional disconnection
    (e.g., app update, temporary network issue recovery).
    """
    token = ws_manager.generate_reconnect_token(client_id)
    return {"reconnect_token": token}


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str, token: str = None):
    """WebSocket endpoint with reconnection support."""
    await websocket.accept()

    try:
        # Attempt to restore connection from token if provided
        if token:
            try:
                restored_client_id = await ws_manager.validate_reconnect_token(token)
                if restored_client_id != client_id:
                    await websocket.send_json(
                        {"type": "error", "message": "Token client_id mismatch"}
                    )
                    await websocket.close(code=1008, reason="Invalid token")
                    return

                await ws_manager.connect(client_id, websocket)
                await websocket.send_json(
                    {"type": "restored", "message": "Session restored from token"}
                )
            except (InvalidTokenError, TokenExpiredError) as e:
                await websocket.send_json({"type": "error", "message": str(e)})
                await websocket.close(code=1008, reason="Invalid token")
                return
        else:
            # New connection
            await ws_manager.connect(client_id, websocket)

        await websocket.send_json(
            {
                "type": "connection",
                "message": f"Connected. Use reconnect-token endpoint to get "
                f"token before disconnection.",
            }
        )

        # Handle incoming messages
        while True:
            data = await websocket.receive_text()

            if data == "pong":
                await ws_manager.handle_pong(client_id)
                continue

            # Echo message back with reconnection token
            reconnect_token = ws_manager.generate_reconnect_token(client_id)
            message = json.dumps(
                {
                    "type": "message",
                    "user": client_id,
                    "content": data,
                    "reconnect_token": reconnect_token,
                }
            )

            await ws_manager.broadcast(message)

    except WebSocketDisconnect:
        try:
            await ws_manager.disconnect(client_id)
        except Exception:
            pass

    except Exception as e:
        print(f"WebSocket error for {client_id}: {e}")
        try:
            await ws_manager.disconnect(client_id)
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
