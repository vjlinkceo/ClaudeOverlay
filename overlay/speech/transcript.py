"""What was heard since the last question: utterances, question detection, echo filtering."""

from __future__ import annotations

import collections
import re
import time
from dataclasses import dataclass
from typing import NamedTuple

PROMPT_SPEAKERS = {"system": "Them", "mic": "Me"}


@dataclass
class Utterance:
    source: str
    text: str
    t_start: float
    t_end: float


class Line(NamedTuple):
    time: str
    source: str
    text: str
    partial: bool


_LEAD = (
    r"(?:(?:а|и|ну|так|вот|окей|ок|хорошо|ладно|тогда|слушай(?:те)?|скажи(?:те)?|подскажи(?:те)?|"
    r"пожалуйста|і|та|отже|добре|скажіть|підкажіть|also|so|okay|ok|and|well|now|then|alright|"
    r"right|please)[\s,]+){0,3}"
)
_QWORDS = (
    r"что|чем|чего|чему|как|какой|какая|какое|какие|каким|какую|каких|почему|зачем|когда|где|куда|"
    r"откуда|сколько|кто|кого|кому|можно ли|можете|могли бы|сможете|в ч[её]м|есть ли|знаете ли|"
    r"правда ли|верно ли|а если|что если|"
    r"що|чим|як|чому|навіщо|коли|де|куди|звідки|який|яка|яке|які|якими|скільки|хто|кого|чи|"
    r"можете|змогли б|в ч[оo]му|у ч[оo]му|"
    r"what|how|why|when|where|which|who|whom|whose|can you|could you|would you|will you|do you|"
    r"did you|have you|are you|is there|are there|is it|should|what's|how's|what if"
)
_REQUESTS = (
    r"расскажи(?:те)?|объясни(?:те)?|назови(?:те)?|приведи(?:те)?|опиши(?:те)?|поясни(?:те)?|"
    r"перечисли(?:те)?|сравни(?:те)?|напиши(?:те)?|реши(?:те)?|докажи(?:те)?|покажи(?:те)?|"
    r"предложи(?:те)?|оцени(?:те)?|дай(?:те)? определение|давай(?:те)? (?:обсудим|поговорим|разбер[её]м)|"
    r"розкажи(?:ть)?|розкажіть|поясни(?:ть)?|поясніть|назви|назвіть|наведи|наведіть|опиши|опишіть|"
    r"порівняй(?:те)?|напиши|напишіть|розв['’]яжи|розв['’]яжіть|доведи|доведіть|покажи|покажіть|"
    r"tell me|tell us|explain|describe|name|give me|give an example|walk me through|write|compare|"
    r"implement|solve|define|what about|how about|talk about"
)
_QUESTION_START = re.compile(rf"^{_LEAD}(?:{_QWORDS}|{_REQUESTS})(?![\w'’-])", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def detect_question(text: str, last: int | None = None) -> str | None:
    """'strong' if a sentence ends with '?', 'weak' if a sentence starts with a question word or a
    request, otherwise None. `last` limits the check to the last N sentences."""
    found = None
    sentences = split_sentences(text)
    for sentence in sentences[-last:] if last else sentences:
        if sentence.rstrip("\"'»)").endswith("?"):
            return "strong"
        if _QUESTION_START.search(sentence.lstrip("-—–«\"' ")):
            found = "weak"
    return found


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def is_echo(candidate: Utterance, other: Utterance, window: float = 4.0, min_overlap: float = 0.6) -> bool:
    """Without headphones the microphone hears the speakers: the same speech arrives from both sources."""
    if candidate.source == other.source:
        return False
    if candidate.t_end < other.t_start - window or candidate.t_start > other.t_end + window:
        return False
    cand_words = _words(candidate.text)
    if not cand_words:
        return False
    other_words = set(_words(other.text))
    return sum(w in other_words for w in cand_words) / len(cand_words) >= min_overlap


def _clock(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))


class Transcript:
    MERGE_GAP = 1.5
    MERGE_MAX = 600

    def __init__(self):
        self.items: list[Utterance] = []
        self.partials: dict[str, str] = {}
        self.recent: collections.deque[Utterance] = collections.deque(maxlen=30)

    def add(self, utt: Utterance, echo_filter: bool = False) -> Utterance | None:
        """Add an utterance (merged with the previous one from the same speaker if close).
        Returns None if it is an echo of PC audio picked up by the microphone."""
        self.partials.pop(utt.source, None)
        if echo_filter:
            if utt.source == "mic" and any(is_echo(utt, o) for o in self.recent if o.source == "system"):
                return None
            if utt.source == "system":
                self.items = [i for i in self.items if not (i.source == "mic" and is_echo(i, utt))]
        self.recent.append(Utterance(utt.source, utt.text, utt.t_start, utt.t_end))
        last = self.items[-1] if self.items else None
        if (
            last
            and last.source == utt.source
            and utt.t_start - last.t_end <= self.MERGE_GAP
            and len(last.text) + len(utt.text) < self.MERGE_MAX
        ):
            last.text = f"{last.text} {utt.text}"
            last.t_end = max(last.t_end, utt.t_end)
            return last
        self.items.append(utt)
        return utt

    def set_partial(self, source: str, text: str) -> None:
        if text:
            self.partials[source] = text
        else:
            self.partials.pop(source, None)

    def clear(self) -> None:
        """Called after sending; live partials stay because the person is still talking."""
        self.items = []

    def restore(self, items: list[Utterance]) -> None:
        """Put back what was sent with a question that failed, before anything heard since."""
        self.items = items + self.items

    def wipe(self) -> None:
        self.items = []
        self.partials.clear()
        self.recent.clear()

    def lines(self) -> list[Line]:
        out = [Line(_clock(u.t_start), u.source, u.text, False) for u in self.items]
        out += [Line("…", source, text, True) for source, text in self.partials.items() if text]
        return out

    def text_for_prompt(self, labeled: bool, max_chars: int) -> str:
        rendered = []
        for line in self.lines():
            who = PROMPT_SPEAKERS.get(line.source, "") if labeled else ""
            if line.partial:
                who = f"{who} (still speaking)".strip()
            rendered.append(f"[{line.time}] {who}: {line.text}" if who else f"[{line.time}] {line.text}")
        total, kept = 0, []
        for line in reversed(rendered):
            total += len(line) + 1
            if total > max_chars and kept:
                kept.append("[…] (earlier transcript omitted)")
                break
            kept.append(line)
        return "\n".join(reversed(kept))
