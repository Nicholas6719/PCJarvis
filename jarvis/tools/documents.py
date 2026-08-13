"""Creating files: PDFs, text documents, and conversation exports.

These exist because of a specific failure. Asked to "create a PDF of our
conversation", JARVIS said he had done it -- twice -- and had not, because no
tool existed that could. A model with no capability and a helpful disposition
will invent success, so the fix is a real capability rather than a sterner
prompt.

Everything lands in the user's Documents\\JARVIS folder and the tools report the
actual path, so a claim is always checkable.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from .registry import tool

log = logging.getLogger("jarvis.tools.documents")

OUTPUT_DIR = Path.home() / "Documents" / "JARVIS"
_SAFE = re.compile(r"[^A-Za-z0-9 ._-]")

# Where he might ask for a file to land. Asked for a PDF "on my desktop"
# the tool had no way to honour it, so the file went to Documents while he
# was told it was on the Desktop -- true from the tool's point of view and
# useless from his.
LOCATIONS = {
    "desktop": Path.home() / "Desktop",
    "documents": Path.home() / "Documents",
    "downloads": Path.home() / "Downloads",
    "jarvis": OUTPUT_DIR,
}


def _resolve_dir(location: str) -> tuple[Path, str]:
    """Return the folder to write into, and how to describe it aloud."""
    key = (location or "").lower().strip()
    key = key.removeprefix("my ").removeprefix("the ")
    key = key.replace(" folder", "").strip(" .")
    if not key:
        return OUTPUT_DIR, "your Documents, JARVIS folder"
    if key in LOCATIONS:
        target = LOCATIONS[key]
        target.mkdir(parents=True, exist_ok=True)
        return target, ("your Desktop" if key == "desktop"
                        else f"your {target.name} folder")
    candidate = Path(location).expanduser()
    if candidate.is_dir():
        return candidate, candidate.name
    return OUTPUT_DIR, "your Documents, JARVIS folder"

# fpdf's core fonts are Latin-1 only, and a smart quote from the model raises
# mid-render. Fold the usual suspects rather than lose the document.
_LATIN1 = {
    "—": " - ", "–": " - ", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "•": "-",
    " ": " ", "→": "->", "≥": ">=", "≤": "<=",
}


def _safe_name(name: str, suffix: str, folder: Path | None = None) -> Path:
    stem = _SAFE.sub("", (name or "").strip()) or f"jarvis_{time.strftime('%Y%m%d_%H%M%S')}"
    if stem.lower().endswith(suffix):
        stem = stem[: -len(suffix)]
    target = folder or OUTPUT_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target / f"{stem}{suffix}"


def _latin1(text: str) -> str:
    for src, dst in _LATIN1.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


def _break_long_words(text: str, limit: int = 40) -> str:
    """Split words too long to fit a line.

    fpdf cannot wrap inside a word, so one unbroken 200-character string -- a
    URL, a file path, a base64 blob pasted into the conversation -- raises
    "Not enough horizontal space to render a single character" and loses the
    whole document. Breaking them is far better than failing.
    """
    out = []
    for word in text.split(" "):
        while len(word) > limit:
            out.append(word[:limit])
            word = word[limit:]
        out.append(word)
    return " ".join(out)


def _write_pdf(path: Path, title: str, blocks: list[tuple[str, str]]) -> None:
    """blocks: (speaker_or_heading, body). An empty speaker is plain prose."""
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # Compute the usable width explicitly. Passing 0 asks fpdf to infer it, and
    # when the cursor is anywhere unexpected that inference yields a width of
    # zero and the render dies.
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def block(text: str, size: int, style: str = "",
              colour: tuple[int, int, int] = (20, 20, 20), height: float = 6):
        text = _break_long_words(_latin1(text)).strip()
        if not text:
            return
        pdf.set_font("Helvetica", style, size)
        pdf.set_text_color(*colour)
        pdf.set_x(pdf.l_margin)
        try:
            pdf.multi_cell(width, height, text)
        except Exception:
            # Never lose the document over one awkward line.
            log.debug("skipped an unrenderable line", exc_info=True)

    block(title, 16, "B", (15, 15, 15), 9)
    block(time.strftime("%A, %d %B %Y at %I:%M %p"), 9, "", (120, 120, 120))
    pdf.ln(4)

    for speaker, body in blocks:
        if speaker:
            colour = (150, 100, 20) if speaker.lower() == "jarvis" else (30, 90, 130)
            block(speaker.upper(), 10, "B", colour, 5)
        block(body, 11)
        pdf.ln(3)

    pdf.output(str(path))


# ══════════════════════════════════════════════════════════════════
_memory = None


def bind(memory) -> None:
    """Share the conversation store, so exports have something to export."""
    global _memory
    _memory = memory


@tool(category="documents")
def export_conversation(filename: str = "", turns: int = 40,
                        as_pdf: bool = True, location: str = "") -> str:
    """Save the conversation so far to a PDF (or text file) in Documents\\JARVIS.

    Use whenever asked to save, export, write up or make a document of what has
    been discussed.

    Args:
        filename: What to call it. Leave empty for a timestamped name.
        turns: How many recent turns to include.
        as_pdf: True for a PDF, False for a plain text file.
        location: Where to put it -- "desktop", "documents", "downloads",
            or a full folder path. Pass whatever he asked for.
    """
    if _memory is None:
        return "My conversation store is not available, so I cannot export it."

    try:
        history = _memory.recent_turns(max(1, min(int(turns), 400)))
    except Exception as e:
        return f"Could not read the conversation: {e}"
    if not history:
        return "There is no conversation recorded yet to export."

    blocks = [
        ("JARVIS" if t["role"] == "assistant" else "Nicholas",
         (t["content"] or "").strip())
        for t in history if (t.get("content") or "").strip()
    ]
    title = "Conversation with JARVIS"

    folder, spoken_where = _resolve_dir(location)
    try:
        if as_pdf:
            path = _safe_name(filename or "conversation", ".pdf", folder)
            _write_pdf(path, title, blocks)
        else:
            path = _safe_name(filename or "conversation", ".txt", folder)
            body = "\n\n".join(f"{who}:\n{text}" for who, text in blocks)
            path.write_text(f"{title}\n{'=' * len(title)}\n\n{body}",
                            encoding="utf-8")
    except Exception as e:
        log.exception("export failed")
        return f"I could not write the file: {e}"

    # Confirm from the file that is actually on disk, not from the intent.
    if not path.exists():
        return "I tried to write the file but it is not there."
    return f"Saved {len(blocks)} messages to {path.name} on {spoken_where}."


@tool(category="documents")
def create_pdf(title: str, content: str, filename: str = "",
               location: str = "") -> str:
    """Create a PDF document with the given title and body text.

    Args:
        title: The heading at the top of the document.
        content: The body text. Blank lines separate paragraphs.
        filename: What to call it. Defaults to the title.
        location: "desktop", "documents", "downloads", or a folder path.
    """
    paragraphs = [("", p.strip()) for p in content.split("\n\n") if p.strip()]
    if not paragraphs:
        return "There was no content to put in the document."
    folder, spoken_where = _resolve_dir(location)
    try:
        path = _safe_name(filename or title, ".pdf", folder)
        _write_pdf(path, title, paragraphs)
    except Exception as e:
        log.exception("pdf creation failed")
        return f"I could not create the PDF: {e}"
    if not path.exists():
        return "I tried to create the PDF but it is not there."
    return f"Created {path.name} on {spoken_where}."


@tool(category="documents")
def save_text_file(filename: str, content: str, location: str = "") -> str:
    """Save text to a file in Documents\\JARVIS.

    Args:
        filename: Name for the file, including its extension.
        content: What to write into it.
        location: "desktop", "documents", "downloads", or a folder path.
    """
    suffix = Path(filename).suffix or ".txt"
    folder, spoken_where = _resolve_dir(location)
    try:
        path = _safe_name(Path(filename).stem, suffix, folder)
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"I could not save that file: {e}"
    return f"Saved {path.name} on {spoken_where}."


@tool(category="documents")
def list_documents() -> str:
    """List the documents previously created in Documents\\JARVIS."""
    if not OUTPUT_DIR.exists():
        return "I have not created any documents yet."
    files = sorted(OUTPUT_DIR.iterdir(), key=lambda p: -p.stat().st_mtime)[:15]
    if not files:
        return "I have not created any documents yet."
    lines = ["Documents I have created:"]
    for f in files:
        lines.append(f"- {f.name} ({f.stat().st_size/1024:.0f}KB, "
                     f"{time.strftime('%d %b %H:%M', time.localtime(f.stat().st_mtime))})")
    return "\n".join(lines)
