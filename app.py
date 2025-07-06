import asyncio
import websockets
import json

# Хранилище активных подключений
connections = {}

async def handler(websocket):
    """Обработка нового подключения"""
    username = None
    
    try:
        async for message in websocket:
            data = json.loads(message)
            print(f"Received: {data}")
            
            # Регистрация пользователя
            if data['type'] == 'register':
                username = data['username']
                connections[username] = websocket
                await websocket.send(json.dumps({
                    'type': 'registered',
                    'status': 'success'
                }))
            
            # Пересылка сообщения конкретному пользователю
            elif 'receiver' in data and data['receiver'] in connections:
                receiver_ws = connections[data['receiver']]
                await receiver_ws.send(json.dumps(data))
    
    except websockets.ConnectionClosed:
        print("Connection closed")
    finally:
        # Удаляем пользователя из подключений при отключении
        if username and username in connections:
            del connections[username]
            print(f"User {username} disconnected")

async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        print("Signaling server running on ws://0.0.0.0:8765")
        await asyncio.Future()  # Бесконечное выполнение

if __name__ == "__main__":
    asyncio.run(main())
