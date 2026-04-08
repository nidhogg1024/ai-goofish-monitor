"""
WebSocket 路由
提供实时通信功能
"""
import asyncio
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Set

logger = logging.getLogger(__name__)

router = APIRouter()

_connections_lock = asyncio.Lock()
active_connections: Set[WebSocket] = set()


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
):
    await websocket.accept()
    async with _connections_lock:
        active_connections.add(websocket)

    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("WebSocket error: %s", e)
    finally:
        async with _connections_lock:
            active_connections.discard(websocket)


async def broadcast_message(message_type: str, data: dict):
    """向所有连接的客户端广播消息"""
    message = {
        "type": message_type,
        "data": data,
    }

    async with _connections_lock:
        snapshot = list(active_connections)

    disconnected: list[WebSocket] = []
    for connection in snapshot:
        try:
            await connection.send_json(message)
        except Exception:
            disconnected.append(connection)

    if disconnected:
        async with _connections_lock:
            for conn in disconnected:
                active_connections.discard(conn)
