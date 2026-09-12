from collections import OrderedDict

from .models import STAGE_ORDER, TranscriptEvent


class CaptionStore:
    """In-memory segment store with monotonic version/stage guarantees.

    Persistence will be added later; this class deliberately owns only realtime state.
    """

    def __init__(self, max_segments: int = 100) -> None:
        self.max_segments = max_segments
        self._segments: OrderedDict[str, TranscriptEvent] = OrderedDict()

    def apply(self, event: TranscriptEvent) -> TranscriptEvent:
        current = self._segments.get(event.segment_id)
        if current is not None:
            if current.committed:
                raise ValueError(f"segment {event.segment_id} is already committed")
            if event.version <= current.version:
                raise ValueError("event version must increase monotonically")
            if STAGE_ORDER[event.stage] < STAGE_ORDER[current.stage]:
                raise ValueError("caption stage cannot move backwards")

        self._segments[event.segment_id] = event
        self._segments.move_to_end(event.segment_id)
        while len(self._segments) > self.max_segments:
            self._segments.popitem(last=False)
        return event

    def snapshot(self) -> list[TranscriptEvent]:
        return list(self._segments.values())

    def clear(self) -> None:
        self._segments.clear()
