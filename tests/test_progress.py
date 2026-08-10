import asyncio
import json

from core.progress import ProgressEvent, ProgressHub


def test_event_stream_uses_json_for_delimiters_entities_and_newlines():
    async def collect_one_event():
        hub = ProgressHub()
        hub.register("job-1")
        message = 'line 1|</script>&entity;\nline 2'
        await hub.emit(
            "job-1",
            ProgressEvent(
                stage="warning",
                message=message,
                progress=8,
                detail={"path": 'C:/exports/<unsafe>|name.xlsx'},
            ),
        )
        stream = hub.event_stream("job-1")
        event_data = await anext(stream)
        await stream.aclose()
        return message, event_data

    message, event_data = asyncio.run(collect_one_event())

    assert event_data.startswith("data: ")
    assert event_data.endswith("\n\n")
    payload = json.loads(event_data.removeprefix("data: ").removesuffix("\n\n"))
    assert payload == {
        "stage": "warning",
        "message": message,
        "progress": 8,
        "detail": {"path": 'C:/exports/<unsafe>|name.xlsx'},
    }
