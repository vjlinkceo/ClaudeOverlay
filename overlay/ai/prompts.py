"""System prompt and user message assembly.

The conversation history is append-only: the system prompt never changes, and the topic and reply
language travel inside new messages. Editing earlier turns invalidates the prompt cache and thinking
blocks, and on newer API accounts it is rejected with a 400.
"""

from __future__ import annotations

SYSTEM_PROMPT = """You are an assistant in a small always-on-top window. The user is on a call, \
in an interview or a lecture, watching a video or solving a task, and reads your answer at a \
glance. The answer is needed right away: latency-sensitive, begin your visible answer immediately.

What a message can contain
- <transcript>: automatic speech transcription, lines "[hh:mm:ss] Speaker: text". "Them" is the \
computer audio (interviewer, lecturer, video), "Me" is the user's microphone. Without speaker labels \
the speaker is unknown (usually a lecturer or the other side of the call). "(still speaking)" marks \
a phrase that is still being said. The transcription has recognition errors: restore the meaning \
and terms from context ("джанго" is Django, "пост грес" is PostgreSQL).
- A screenshot, if attached.
- <typed_question>: a question the user typed. It takes priority over everything else.
- <context>: the topic and key terms from the user. Use them for terminology and depth, and use \
the listed terms' spelling when the transcript garbles them. A new <context> replaces the previous one.

How to choose what to answer
1. If there is a <typed_question>, answer it; the transcript and screenshot are context.
2. Otherwise find the latest question or task addressed to the user in the transcript: a direct \
question, a request ("tell me about…", "explain…", "give an example…"), a task, or a follow-up to \
an earlier question. Search the whole transcript, not just its start or end: questions often get \
buried in long speech.
3. A question may be split by a pause or someone else's line: put it back together. A short \
follow-up ("why?", "and without an index?") refers to the previous topic.
4. "Me" lines are the user's answers. Do not re-answer a question the user already answered \
fully; if the answer was incomplete or wrong, briefly complete or correct it.
5. A rhetorical question the speaker immediately answered is not a question to the user.
6. If several questions to the user were asked since your last answer, answer all of them in \
order, each as a short separate item, the last one in more detail.
7. Ignore small talk and service phrases ("can you hear me?", "let me share my screen").
8. If the screenshot shows a task, a test or code, solve what is on the screenshot; the speech \
hints at what exactly is being asked.
9. If there is no question, say in one line what is being discussed and give the most useful hint \
on the topic. Do not invent a question.
10. Questions you already answered in earlier messages are closed: do not repeat those answers \
unless asked again.
11. <earlier_conversation> summarizes answers from before the chat was restarted; use it for \
follow-up questions.

Answer format
- The <answer_style> block in the message sets the length and structure of the answer. Follow \
the latest one.
- First line: "❓ " and the gist of the chosen question in a few words, so the user immediately \
sees what you are answering. Skip this line for a <typed_question>.
- No preamble and no restating the question. Bold only key terms.
- The window renders markdown: bold, `inline code`, ``` blocks, lists, small headings and \
tables. Simple formulas go in $…$ (fractions, roots, scripts, Greek letters, sums, integrals); \
for a formula in plain text use Unicode: x², √x, ×, ≤, →, π, Σ.
- The window cannot typeset matrices, determinants or systems of equations: put them in a \
``` block, one row per line, columns aligned with spaces (``` blocks use a monospace font). \
Example:
```
A = ( 2  1 )    det A = 2·3 − 1·5 = 1
    ( 5  3 )
```
- Code goes in ``` blocks.
- Reply in the language of the question (Russian, Ukrainian or English) unless the message says \
otherwise."""

MODE_STYLES = {
    "quick": "Quick answer. Right away 1–3 sentences with the answer itself. Then, only if useful, "
    "up to 4 one-line items: key details, an example, steps. Multiple-choice question: name the correct "
    "option first. Code or an error: the cause and the fixed code.",
    "interview": "Interview answer the user will say out loud. First person, natural spoken language, "
    "confident and concise: 3–6 short sentences, no lists unless the question asks to enumerate. Lead "
    "with the direct answer, then one concrete example, trade-off or detail from practice. For questions "
    "about experience or behavior use: situation → what I did → result. Bold 1–3 key terms the user "
    "can glance at while speaking.",
    "explain": "Detailed explanation for learning. Start with a 1–2 sentence answer, then explain step "
    "by step: the idea and intuition, how it works, a small worked example, common mistakes. Short "
    "paragraphs and lists; about 150–300 words unless the topic needs more.",
    "solve": "Step-by-step solution. One line with the given data, then numbered steps with every "
    "calculation shown (matrices and aligned calculations in ``` blocks). Check the result where "
    "possible (substitution, multiplication back, units). Finish with a separate line "
    '"**Answer:** …"; for a multiple-choice question name the option there. Do not skip arithmetic '
    "steps that are easy to get wrong.",
}

RECAP_ANSWER_CHARS = 700

LANGUAGE_NAMES = {"ru": "Russian", "uk": "Ukrainian", "en": "English"}

FOLLOWUPS = {
    "shorter": "Shorten your previous answer to 2–3 sentences I can say out loud. Keep its language.",
    "more": "Expand your previous answer with key details, an example and common pitfalls. Keep its language.",
    "deeper": "Re-examine your previous answer carefully and give the most accurate and complete "
    "answer to the same question. Keep its language.",
}
DEEPER_PROFILE, DEEPER_EFFORT = "opus", "high"


SUMMARY_WINDOW_TASK = (
    "Summarize what was said in the last {minutes} minutes for someone who was not paying attention: "
    "3–6 short bullet points with the key points and any names or numbers that were mentioned, then "
    'the questions or requests addressed to the user ("Me"), if any, and decisions taken. '
    "Start with the summary itself: no ❓ line, no preamble."
)
CALL_SUMMARY_TASK = (
    "This is the whole transcript of the call or lecture. Write its summary with bold section titles: "
    "topics discussed; decisions and agreements; questions asked to the user and how they were answered; "
    "action items (who, what, by when); open questions. Short bullets with the concrete names, numbers "
    "and dates from the transcript; skip empty sections. Start with the first section: no ❓ line, "
    "no preamble."
)


def build_summary_content(
    transcript: str, *, labeled: bool, minutes: int | None, answer_language: str = "auto", recap: str = ""
) -> list[dict]:
    """A recap of the last minutes, or the summary of everything heard."""
    source = "PC audio and microphone, speakers labeled" if labeled else "speakers not labeled"
    parts = [recap] if recap else []
    parts.append(
        f'<transcript_window minutes="{minutes or "all"}" source="{source}">\n{transcript}\n</transcript_window>'
    )
    task = SUMMARY_WINDOW_TASK.format(minutes=minutes) if minutes else CALL_SUMMARY_TASK
    if answer_language in LANGUAGE_NAMES:
        task += f" Reply in {LANGUAGE_NAMES[answer_language]}."
    parts.append(task)
    return [{"type": "text", "text": "\n\n".join(parts)}]


def build_context(topic: str, terms: str) -> str:
    lines = []
    if topic.strip():
        lines.append(f"Topic: {topic.strip()}")
    if terms.strip():
        lines.append(f"Terms: {terms.strip()}")
    return "\n".join(lines)


def build_recap(exchanges: list[tuple[str, str]]) -> str:
    """(question, answer) pairs from before a chat restart -> a short <earlier_conversation> block."""
    items = []
    for i, (question, answer) in enumerate(exchanges, 1):
        answer = answer.strip()
        if len(answer) > RECAP_ANSWER_CHARS:
            answer = answer[:RECAP_ANSWER_CHARS].rstrip() + "…"
        items.append(f"{i}. {question.strip()}\n{answer}" if question.strip() else f"{i}. {answer}")
    if not items:
        return ""
    body = "\n\n".join(items)
    intro = "The chat was restarted to stay fast. Your last answers:"
    return f"<earlier_conversation>\n{intro}\n\n{body}\n</earlier_conversation>"


def build_user_content(
    *,
    transcript: str = "",
    labeled: bool = False,
    typed: str = "",
    image_b64: str | None = None,
    context: str | None = None,
    answer_language: str = "auto",
    style: str | None = None,
    recap: str = "",
) -> list[dict]:
    """context=None means unchanged since the previous message; "" means it was cleared."""
    parts = [recap] if recap else []
    if context is not None:
        parts.append(f"<context>\n{context.strip() or 'no topic set'}\n</context>")
    if transcript:
        source = "PC audio and microphone, speakers labeled" if labeled else "speakers not labeled"
        parts.append(f'<transcript source="{source}">\n{transcript}\n</transcript>')
    if typed:
        parts.append(f"<typed_question>\n{typed}\n</typed_question>")
    if style:
        parts.append(f"<answer_style>\n{style}\n</answer_style>")

    if typed:
        task = "Answer the <typed_question>."
    elif transcript and image_b64:
        task = "Find the question in the transcript and answer it using the screenshot."
    elif transcript:
        task = "Find the question in the transcript and answer it."
    else:
        task = "Solve the task or answer the question on the screenshot."
    if answer_language in LANGUAGE_NAMES:
        task += f" Reply in {LANGUAGE_NAMES[answer_language]}."
    parts.append(task)

    content: list[dict] = []
    if image_b64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}})
    content.append({"type": "text", "text": "\n\n".join(parts)})
    return content
