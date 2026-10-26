from collections import OrderedDict

from .models import STAGE_ORDER, CaptionStage, TranscriptEvent


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
            if event.source_revision < current.source_revision:
                raise ValueError("source revision cannot regress")
            if event.source_revision > current.source_revision:
                if event.source_revision != current.source_revision + 1:
                    raise ValueError("source revision must advance by one")
                if event.stage is not CaptionStage.STABLE:
                    raise ValueError("new source revision must begin at stable stage")
                if event.translations or event.translation_states or event.committed:
                    raise ValueError("new source revision must invalidate target results")
                if event.version <= current.version:
                    raise ValueError("event version must increase monotonically")
                self._segments[event.segment_id] = event
                self._segments.move_to_end(event.segment_id)
                return event
            if event.version <= current.version:
                raise ValueError("event version must increase monotonically")
            if STAGE_ORDER[event.stage] < STAGE_ORDER[current.stage]:
                raise ValueError("caption stage cannot move backwards")
            # Per-language events are cumulative snapshots. A later event
            # must not erase or silently rewrite an already published target,
            # even if its segment version increases.
            for language, translation in current.translations.items():
                if event.translations.get(language) != translation:
                    raise ValueError("published translation cannot regress")
            for language, state in current.translation_states.items():
                if (
                    state in {"available", "failed", "timed_out", "unsupported"}
                    and event.translation_states.get(language) != state
                ):
                    raise ValueError("terminal translation state cannot regress")

        self._segments[event.segment_id] = event
        self._segments.move_to_end(event.segment_id)
        while len(self._segments) > self.max_segments:
            self._segments.popitem(last=False)
        return event

    def snapshot(self) -> list[TranscriptEvent]:
        return list(self._segments.values())

    def clear(self) -> None:
        self._segments.clear()
