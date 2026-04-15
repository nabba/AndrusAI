import websockets
import asyncio

class CodeCollab:
    def __init__(self):
        self.clients = set()

    async def handle_connection(self, websocket, path):
        self.clients.add(websocket)
        try:
            async for message in websocket:
                await self.broadcast(message, websocket)
        finally:
            self.clients.remove(websocket)

    async def broadcast(self, message, sender):
        for client in self.clients:
            if client != sender:
                await client.send(message)