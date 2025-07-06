import asyncio
import ssl
import json
import logging
from aiohttp import web, WSMsgType
import uuid

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Хранилище для активных пользователей и звонков
users = {}  # {user_id: websocket}
calls = {}  # {call_id: {caller: user_id, callee: user_id}}

# Генерация уникального ID для звонка
def generate_call_id():
    return str(uuid.uuid4())

# Обработчик WebSocket-соединений
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    user_id = str(uuid.uuid4())  # Уникальный ID пользователя
    users[user_id] = ws
    logger.info(f"User {user_id} connected")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    action = data.get("action")
                    logger.info(f"Received action: {action} from {user_id}")

                    if action == "start_call":
                        # Инициация звонка
                        callee_id = data.get("callee_id")
                        call_id = generate_call_id()
                        if callee_id in users and callee_id != user_id:
                            calls[call_id] = {"caller": user_id, "callee": callee_id}
                            # Отправляем запрос на звонок получателю
                            await users[callee_id].send_json({
                                "action": "incoming_call",
                                "call_id": call_id,
                                "caller_id": user_id
                            })
                            # Подтверждение инициатору
                            await ws.send_json({
                                "action": "call_initiated",
                                "call_id": call_id
                            })
                        else:
                            await ws.send_json({"action": "error", "message": "Callee not found or invalid"})

                    elif action == "accept_call":
                        # Принятие звонка
                        call_id = data.get("call_id")
                        if call_id in calls and calls[call_id]["callee"] == user_id:
                            caller_id = calls[call_id]["caller"]
                            await users[caller_id].send_json({
                                "action": "call_accepted",
                                "call_id": call_id,
                                "callee_id": user_id
                            })
                        else:
                            await ws.send_json({"action": "error", "message": "Invalid call"})

                    elif action == "sdp":
                        # Обмен SDP
                        call_id = data.get("call_id")
                        sdp = data.get("sdp")
                        if call_id in calls:
                            target_id = calls[call_id]["callee"] if user_id == calls[call_id]["caller"] else calls[call_id]["caller"]
                            if target_id in users:
                                await users[target_id].send_json({
                                    "action": "sdp",
                                    "call_id": call_id,
                                    "sdp": sdp
                                })
                            else:
                                await ws.send_json({"action": "error", "message": "Target user offline"})

                    elif action == "ice_candidate":
                        # Обмен ICE-кандидатами
                        call_id = data.get("call_id")
                        candidate = data.get("candidate")
                        if call_id in calls:
                            target_id = calls[call_id]["callee"] if user_id == calls[call_id]["caller"] else calls[call_id]["caller"]
                            if target_id in users:
                                await users[target_id].send_json({
                                    "action": "ice_candidate",
                                    "call_id": call_id,
                                    "candidate": candidate
                                })

                    elif action == "end_call":
                        # Завершение звонка
                        call_id = data.get("call_id")
                        if call_id in calls:
                            target_id = calls[call_id]["callee"] if user_id == calls[call_id]["caller"] else calls[call_id]["caller"]
                            if target_id in users:
                                await users[target_id].send_json({
                                    "action": "call_ended",
                                    "call_id": call_id
                                })
                            del calls[call_id]
                            await ws.send_json({"action": "call_ended", "call_id": call_id})

                except json.JSONDecodeError:
                    await ws.send_json({"action": "error", "message": "Invalid JSON"})
            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WebSocket error: {msg}")
    except Exception as e:
        logger.error(f"Error in WebSocket: {e}")
    finally:
        # Очистка при отключении
        del users[user_id]
        for call_id in list(calls.keys()):
            if calls[call_id]["caller"] == user_id or calls[call_id]["callee"] == user_id:
                target_id = calls[call_id]["callee"] if user_id == calls[call_id]["caller"] else calls[call_id]["caller"]
                if target_id in users:
                    await users[target_id].send_json({
                        "action": "call_ended",
                        "call_id": call_id
                    })
                del calls[call_id]
        logger.info(f"User {user_id} disconnected")

    return ws

# Настройка SSL (для продакшена замените на реальные сертификаты)
ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ssl_context.load_cert_chain(certfile="server.crt", keyfile="server.key")  # Создайте самоподписанные сертификаты для теста

# Создание приложения
app = web.Application()
app.router.add_get("/ws", websocket_handler)

# Запуск сервера
if __name__ == "__main__":
    logger.info("Starting WebSocket server on https://0.0.0.0:8443")
    web.run_app(app, host="0.0.0.0", port=8443, ssl_context=ssl_context)
