import asyncio
from typing import AsyncGenerator
from dataclasses import dataclass, field


@dataclass
class ProgressEvent:
    stage: str
    message: str
    progress: int  # 0-100
    detail: dict = field(default_factory=dict)


class ProgressHub:
    def __init__(self):
        self._listeners: dict[str, asyncio.Queue] = {}

    def register(self, session_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        self._listeners[session_id] = queue
        return queue

    def unregister(self, session_id: str):
        self._listeners.pop(session_id, None)

    async def emit(self, session_id: str, event: ProgressEvent):
        queue = self._listeners.get(session_id)
        if queue:
            await queue.put(event)

    async def event_stream(self, session_id: str) -> AsyncGenerator[str, None]:
        # 如果已有 queue 则复用（预注册场景），否则新建
        if session_id not in self._listeners:
            self.register(session_id)
        queue = self._listeners[session_id]
        try:
            while True:
                event = await queue.get()
                yield f"data: {event.stage}|{event.message}|{event.progress}\n\n"
                if event.stage in ("done", "error"):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            self.unregister(session_id)


# Global instance
progress_hub = ProgressHub()
