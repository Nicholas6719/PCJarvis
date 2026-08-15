"""Working on text he has copied: proofread, rewrite, summarise, translate.

Everything runs through the local model, so the clipboard never leaves the
machine -- which matters more here than anywhere else, since the clipboard is
where passwords and half-written messages live.

Two rules throughout. The result goes back on the clipboard so he can paste it
straight away, and the spoken reply is a short confirmation rather than the
text itself: having four paragraphs read aloud is not useful, and he asked for
the rewrite so he could paste it, not hear it.
"""
from __future__ import annotations

import logging

from .registry import tool

log = logging.getLogger("jarvis.tools.text")

# Past this the reply gets slow and the model starts losing the thread. Say so
# rather than silently working on a fragment.
MAX_CHARS = 4000

_brain = None


def bind(brain) -> None:
    """Share the local model. Without it these tools refuse rather than guess."""
    global _brain
    _brain = brain


def _clipboard() -> tuple[str, str]:
    """(text, problem). Never raises, and never reads a Windows error aloud.

    The clipboard belongs to the interactive desktop, so a locked screen
    fails with 'Error calling OpenClipboard (WinError 0: The operation
    completed successfully)' -- which is both untrue and unspeakable. It
    also fails transiently whenever another program has the clipboard
    open, which is common and passes in milliseconds, so it is worth two
    quick retries before giving up.
    """
    import time as _time

    last = None
    for attempt in range(3):
        try:
            import pyperclip

            text = (pyperclip.paste() or '').strip()
        except Exception as e:
            last = e
            _time.sleep(0.15 * (attempt + 1))
            continue
        if not text:
            return '', 'There is nothing on the clipboard.'
        return text, ''

    log.debug('clipboard unreachable: %s', last)
    return '', ('I cannot reach the clipboard at the moment. That usually '
                'means the screen is locked, or another program is holding '
                'it. Unlock the screen and ask me again.')


def _put(text: str) -> bool:
    import time as _time

    for attempt in range(3):
        try:
            import pyperclip

            pyperclip.copy(text)
            return True
        except Exception:
            _time.sleep(0.15 * (attempt + 1))
    log.exception('could not write the clipboard')
    return False

async def _run(instruction: str, text: str, budget: int) -> tuple[str, str]:
    """(result, problem). The model is asked for the text and nothing else."""
    if _brain is None:
        return "", "My language model is not available, so I cannot do that."

    truncated = len(text) > MAX_CHARS
    body = text[:MAX_CHARS]
    prompt = (f"{instruction}\n\n"
              f"Return only the resulting text. No preamble, no explanation, "
              f"no quotation marks around it.\n\n"
              f"---\n{body}\n---")
    try:
        result = (await _brain.quick(prompt, max_tokens=budget)).strip()
    except Exception as e:
        log.exception("model call failed")
        return "", f"The model failed on that: {e}"
    if not result:
        return "", "The model returned nothing, so I have left the clipboard alone."

    # Models like to wrap the answer in quotes or a code fence despite being
    # told not to. Strip the wrapper rather than paste it.
    for fence in ("```", "'''"):
        if result.startswith(fence):
            result = result.strip(fence).strip()
    if len(result) > 1 and result[0] == result[-1] and result[0] in "\"'":
        result = result[1:-1].strip()

    note = (" I worked on the first four thousand characters; it was longer "
            "than that." if truncated else "")
    return result, note


def _tokens_for(text: str) -> int:
    """Roughly a token per four characters, with headroom and a floor."""
    return max(120, min(1400, int(len(text) / 3) + 80))


@tool(category="text", speak_while_running=True)
async def proofread_clipboard() -> str:
    """Fix spelling, grammar and punctuation in the copied text.

    Use for "fix this", "proofread this", "check my spelling".
    """
    text, problem = _clipboard()
    if problem:
        return problem
    result, note = await _run(
        "Correct any spelling, grammar and punctuation mistakes in the text "
        "below. Keep the wording, tone and meaning exactly as they are -- "
        "change only what is wrong.", text, _tokens_for(text))
    if not result:
        return note or "I could not proofread that."
    if not _put(result):
        return "I proofread it but could not put it back on the clipboard."
    changed = result.strip() != text.strip()
    if not changed:
        return "I found nothing to correct; it already reads correctly."
    return f"Proofread and copied back to your clipboard.{note}"


@tool(category="text", speak_while_running=True)
async def rewrite_clipboard(style: str = "clearer") -> str:
    """Rewrite the copied text in a different style.

    Args:
        style: How to rewrite it -- "formal", "friendlier", "shorter",
            "simpler", "more professional". Defaults to clearer.
    """
    text, problem = _clipboard()
    if problem:
        return problem
    style = (style or "clearer").strip()
    result, note = await _run(
        f"Rewrite the text below to be {style}. Preserve the meaning and any "
        f"facts, names or numbers exactly.", text, _tokens_for(text))
    if not result:
        return note or "I could not rewrite that."
    if not _put(result):
        return "I rewrote it but could not put it back on the clipboard."
    return f"Rewritten to be {style}, and copied back to your clipboard.{note}"


@tool(category="text", speak_while_running=True)
async def summarise_clipboard() -> str:
    """Summarise the copied text in a sentence or two, and say it aloud.

    Use for "summarise this", "what does this say", "give me the gist".
    """
    text, problem = _clipboard()
    if problem:
        return problem
    result, note = await _run(
        "Summarise the text below in at most two sentences, as you would say "
        "it out loud.", text, 200)
    if not result:
        return note or "I could not summarise that."
    # The one case where the result IS the answer, so it is spoken rather than
    # copied -- a summary he has to paste to read defeats the point.
    return f"{result}{note}"


@tool(category="text", speak_while_running=True)
async def translate_clipboard(language: str) -> str:
    """Translate the copied text into another language.

    Args:
        language: The language to translate into, e.g. "Spanish", "French".
    """
    text, problem = _clipboard()
    if problem:
        return problem
    language = (language or "").strip()
    if not language:
        return "Tell me which language to translate it into."
    result, note = await _run(
        f"Translate the text below into {language}.", text, _tokens_for(text))
    if not result:
        return note or f"I could not translate that into {language}."
    if not _put(result):
        return f"I translated it but could not put it back on the clipboard."
    return f"Translated into {language} and copied back to your clipboard.{note}"
