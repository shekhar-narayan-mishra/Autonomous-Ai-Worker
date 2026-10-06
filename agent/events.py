import asyncio
import contextvars

current_event_bus = contextvars.ContextVar("current_event_bus", default=None)

class EventBus:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.response_future = None

    async def emit(self, event_type: str, data: dict):
        await self.queue.put({"type": event_type, "data": data})

    async def ask(self, question: str) -> str:
        await self.emit("clarification_request", {"question": question})
        self.response_future = asyncio.Future()
        return await self.response_future

    async def request_approval(self, action: str, args: dict, diff: str) -> bool:
        await self.emit("approval_request", {"action": action, "args": args, "diff": diff})
        self.response_future = asyncio.Future()
        ans = await self.response_future
        return ans.lower() in ["y", "yes", "true", "approve"]

    def respond(self, response: str):
        if self.response_future and not self.response_future.done():
            self.response_future.set_result(response)
