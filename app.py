import os
import json
import asyncio
import logging
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from websockets.server import serve
from websockets.exceptions import ConnectionClosed

# Настройка логгирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("SignalingServer")

class SignalingServer:
    def __init__(self):
        # Ключ шифрования из переменных окружения
        self.encryption_key = os.getenv("ENCRYPTION_KEY", "default-encryption-key-123456").encode()
        if len(self.encryption_key) not in [16, 24, 32]:
            logger.warning("Encryption key length should be 16, 24 or 32 bytes. Using padded key.")
            self.encryption_key = self.encryption_key.ljust(32, b'\0')[:32]
        
        # Хранилища данных
        self.connected_users = {}  # {websocket: username}
        self.user_connections = {}  # {username: websocket}
        self.active_calls = {}      # {caller: callee, callee: caller}
        
        logger.info(f"Signaling server initialized with encryption key: {self.encryption_key[:3]}...")

    def encrypt_message(self, message):
        """Шифрование сообщения с использованием AES-CBC"""
        try:
            # Добавляем padding
            padder = padding.PKCS7(128).padder()
            padded_data = padder.update(message) + padder.finalize()
            
            # Генерируем IV
            iv = os.urandom(16)
            
            # Создаем шифр
            cipher = Cipher(
                algorithms.AES(self.encryption_key),
                modes.CBC(iv),
                backend=default_backend()
            )
            encryptor = cipher.encryptor()
            ciphertext = encryptor.update(padded_data) + encryptor.finalize()
            
            return iv + ciphertext
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            return None

    def decrypt_message(self, encrypted_data):
        """Дешифрование сообщения с использованием AES-CBC"""
        try:
            # Извлекаем IV и зашифрованные данные
            iv = encrypted_data[:16]
            ciphertext = encrypted_data[16:]
            
            # Создаем дешифратор
            cipher = Cipher(
                algorithms.AES(self.encryption_key),
                modes.CBC(iv),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()
            
            # Удаляем padding
            unpadder = padding.PKCS7(128).unpadder()
            plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()
            
            return plaintext
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            return None

    async def register_user(self, websocket, username):
        """Регистрация пользователя на сервере"""
        if username in self.user_connections:
            # Уведомляем о дубликате подключения
            await self.send_message(
                websocket, 
                {"type": "error", "message": "User already connected"}
            )
            await websocket.close()
            return False
        
        self.connected_users[websocket] = username
        self.user_connections[username] = websocket
        logger.info(f"User registered: {username}")
        
        await self.send_message(
            websocket, 
            {"type": "registered", "status": "success"}
        )
        return True

    async def handle_call(self, sender, receiver, offer, call_type):
        """Обработка входящего вызова"""
        # Проверяем доступность пользователя
        if receiver not in self.user_connections:
            await self.send_message(
                self.user_connections[sender],
                {"type": "user-unavailable", "username": receiver}
            )
            return
        
        # Проверяем не занят ли пользователь
        if receiver in self.active_calls:
            await self.send_message(
                self.user_connections[sender],
                {"type": "user-busy", "username": receiver}
            )
            return
        
        # Сохраняем информацию о звонке
        self.active_calls[sender] = receiver
        self.active_calls[receiver] = sender
        
        # Пересылаем предложение вызываемому пользователю
        await self.send_message(
            self.user_connections[receiver],
            {
                "type": "offer",
                "sender": sender,
                "offer": offer,
                "callType": call_type
            }
        )
        logger.info(f"Call from {sender} to {receiver} initiated")

    async def handle_answer(self, sender, receiver, answer):
        """Обработка ответа на вызов"""
        # Проверяем наличие активного вызова
        if receiver not in self.user_connections or sender not in self.active_calls:
            return
        
        # Пересылаем ответ вызывающему пользователю
        await self.send_message(
            self.user_connections[receiver],
            {
                "type": "answer",
                "sender": sender,
                "answer": answer
            }
        )
        logger.info(f"Call answer from {sender} to {receiver} sent")

    async def handle_ice_candidate(self, sender, receiver, candidate):
        """Пересылка ICE-кандидата"""
        if receiver in self.user_connections:
            await self.send_message(
                self.user_connections[receiver],
                {
                    "type": "ice-candidate",
                    "sender": sender,
                    "candidate": candidate
                }
            )

    async def handle_end_call(self, sender, receiver):
        """Завершение вызова"""
        if sender in self.active_calls:
            # Удаляем информацию о звонке
            callee = self.active_calls[sender]
            if callee in self.active_calls:
                del self.active_calls[callee]
            del self.active_calls[sender]
            
            # Уведомляем участников о завершении
            if receiver in self.user_connections:
                await self.send_message(
                    self.user_connections[receiver],
                    {"type": "end-call", "sender": sender}
                )
            
            logger.info(f"Call ended between {sender} and {receiver}")

    async def send_message(self, websocket, message):
        """Отправка зашифрованного сообщения через WebSocket"""
        try:
            json_message = json.dumps(message).encode()
            encrypted = self.encrypt_message(json_message)
            if encrypted:
                await websocket.send(encrypted)
        except Exception as e:
            logger.error(f"Error sending message: {e}")

    async def connection_handler(self, websocket):
        """Обработчик соединений WebSocket"""
        try:
            async for message in websocket:
                try:
                    # Дешифруем сообщение
                    decrypted = self.decrypt_message(message)
                    if not decrypted:
                        continue
                    
                    data = json.loads(decrypted)
                    
                    # Обработка команд
                    if data["type"] == "register":
                        if await self.register_user(websocket, data["username"]):
                            continue
                        else:
                            break
                    
                    # Проверка регистрации пользователя
                    if websocket not in self.connected_users:
                        await websocket.close()
                        break
                    
                    username = self.connected_users[websocket]
                    
                    if data["type"] == "offer":
                        await self.handle_call(
                            username,
                            data["receiver"],
                            data["offer"],
                            data.get("callType", "video")
                        )
                    
                    elif data["type"] == "answer":
                        await self.handle_answer(
                            username,
                            data["receiver"],
                            data["answer"]
                        )
                    
                    elif data["type"] == "ice-candidate":
                        await self.handle_ice_candidate(
                            username,
                            data["receiver"],
                            data["candidate"]
                        )
                    
                    elif data["type"] == "end-call":
                        await self.handle_end_call(
                            username,
                            data["receiver"]
                        )
                    
                    else:
                        logger.warning(f"Unknown message type: {data['type']}")
                
                except json.JSONDecodeError:
                    logger.error("Invalid JSON format")
                except KeyError as e:
                    logger.error(f"Missing key in message: {e}")
        
        except ConnectionClosed:
            logger.info("Connection closed by client")
        finally:
            # Очистка при отключении
            if websocket in self.connected_users:
                username = self.connected_users[websocket]
                logger.info(f"User disconnected: {username}")
                
                # Завершаем активные звонки
                if username in self.active_calls:
                    callee = self.active_calls[username]
                    await self.handle_end_call(username, callee)
                
                # Удаляем из регистрации
                del self.connected_users[websocket]
                if username in self.user_connections:
                    del self.user_connections[username]

async def main():
    # Создаем и запускаем сервер
    server = SignalingServer()
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")
    
    async with serve(
        server.connection_handler, 
        host, 
        port,
        ping_interval=20,
        ping_timeout=60
    ):
        logger.info(f"Signaling server started on {host}:{port}")
        await asyncio.Future()  # Бесконечное ожидание

if __name__ == "__main__":
    asyncio.run(main())
