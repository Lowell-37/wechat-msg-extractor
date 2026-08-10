import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProgressEvent:
    stage: str
    message: str
    progress: int  # 0-100
    detail: dict[str, Any] = field(default_factory=dict)


class ProgressHub:
    def __init__(self) -> None:
        self._listeners: dict[str, asyncio.Queue[ProgressEvent]] = {}

    def register(self, job_id: str) -> asyncio.Queue[ProgressEvent]:
        queue: asyncio.Queue[ProgressEvent] = asyncio.Queue()
        self._listeners[job_id] = queue
        return queue

    def unregister(self, job_id: str) -> None:
        self._listeners.pop(job_id, None)

    def clear(self) -> None:
        self._listeners.clear()

    async def emit(self, job_id: str, event: ProgressEvent) -> None:
        queue = self._listeners.get(job_id)
        if queue:
            await queue.put(event)

    async def event_stream(self, job_id: str) -> AsyncGenerator[str, None]:
        if job_id not in self._listeners:
            self.register(job_id)
        queue = self._listeners[job_id]
        try:
            while True:
                event = await queue.get()
                payload = json.dumps(
                    {
                        "stage": event.stage,
                        "message": event.message,
                        "progress": event.progress,
                        "detail": event.detail,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                yield f"data: {payload}\n\n"
                if event.stage in ("done", "error"):
                    break
        finally:
            self.unregister(job_id)


progress_hub = ProgressHub()
