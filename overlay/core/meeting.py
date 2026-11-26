"""Everything heard in this session, kept in memory only, for recaps and the call summary.

The transcript panel forgets phrases once they are sent with a question; this log keeps them until
New chat or the idle clear, and is never written to disk by the app itself.
"""

from __future__ import annotations

import time

from overlay.speech.transcript import Transcript, Utterance


class MeetingLog:
    def __init__(self):
        self.items: list[Utterance] = []
        self.started: float | None = None

    def append(self, utt: Utterance) -> None:
        if self.started is None:
            self.started = utt.t_start
        self.items.append(Utterance(utt.source, utt.text, utt.t_start, utt.t_end))

    def wipe(self) -> None:
        self.items = []
        self.started = None

    def text(self, minutes: float | None, labeled: bool, max_chars: int, now: float | None = None) -> str:
        """The last `minutes` of speech (all of it for None) as transcript lines, newest kept when too long."""
        since = ((now or time.time()) - minutes * 60) if minutes else None
        merged = Transcript()
        for utt in self.items:
            if since is None or utt.t_end >= since:
                merged.add(Utterance(utt.source, utt.text, utt.t_start, utt.t_end))
        return merged.text_for_prompt(labeled, max_chars)
