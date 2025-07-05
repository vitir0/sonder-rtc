import asyncio
import websockets
import json
import uuid
from Crypto.Cipher import AES
import base64

# Ключ должен совпадать с клиентским
SIGNALING_KEY = b"your-encryption-key-123456"

# Дополнение для AES
def pad(data):
    length = 16 - (len(data) % 16)
    return data + bytes([length]) * length

def unpad(data):
    return data[:-data[-1]]

def encrypt_data(data, key):
    cipher = AES.new(key, AES.MODE_CBC)
    ct_bytes = cipher.encrypt(pad(data.encode()))
    return base64.b64encode(cipher.iv + ct_bytes).decode()

def decrypt_data(encrypted, key):
    encrypted = base64.b64decode(encrypted)
    iv = encrypted[:16]
    ct = encrypted[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    pt = unpad(cipher.decrypt(ct))
    return pt.decode()

connections = {}

async def handler(websocket):
    username = None
    
    try:
        async for message in websocket:
            try:
                # Дешифруем сообщение
                decrypted = decrypt_data(message, SIGNALING_KEY)
                data = json.loads(decrypted)
                
                # Регистрация пользователя
                if data['type'] == 'register':
                    username = data['username']
                    connections[username] = websocket
                    response = {
                        'type': 'registered',
                        'status': 'success'
                    }
                    await websocket.send(encrypt_data(json.dumps(response), SIGNALING_KEY))
                
                # Пересылка сообщений
                elif 'receiver' in data and data['receiver'] in connections:
                    await connections[data['receiver']].send(encrypt_data(json.dumps(data), SIGNALING_KEY))
                    
            except Exception as e:
                print(f"Error processing message: {e}")
                
    except websockets.ConnectionClosed:
        pass
    finally:
        # Удаляем соединение при отключении
        if username and username in connections:
            del connections[username]

async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        print("Secure signaling server running on ws://0.0.0.0:8765")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
