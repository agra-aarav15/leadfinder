"""Smoke-test the widget's WebSocket path: connect -> greeting -> send 2 msgs."""
import asyncio
import json

import httpx


async def main():
    async with httpx.AsyncClient() as _:
        pass
    import websockets

    vid = f"smoke_{__import__('time').time_ns()}"  # fresh session every run
    uri = f"ws://localhost:8787/ws/{vid}"
    async with websockets.connect(uri) as ws:
        greeting = json.loads(await ws.recv())
        print("GREETING:", greeting["text"][:80])
        for msg in ["hi there, what do you guys do?",
                    "sure - I'm Sam, sam@coffeecorner.shop, need more customers"]:
            await ws.send(msg)
            reply = json.loads(await ws.recv())
            print(f"SENT : {msg[:60]}\nREPLY: {reply['text'][:80]}")
    print("WS SMOKE TEST PASSED")


asyncio.run(main())
