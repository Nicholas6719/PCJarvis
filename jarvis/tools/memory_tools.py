"""Memory tools -- explicit remember, recall and forget.

The Memory instance is injected at startup rather than constructed here, so
that the whole application shares one database connection.
"""
from __future__ import annotations

from .registry import tool

_memory = None


def bind(memory) -> None:
    """Give the tools their memory store. Called once during startup."""
    global _memory
    _memory = memory


@tool(category="memory")
def remember(fact: str, category: str = "general") -> str:
    """Store something about the user permanently, so it survives restarts.

    Use whenever he states a preference, a detail about himself, his setup, his
    projects, or explicitly asks you to remember something.

    Args:
        fact: The fact to store, written as a complete sentence.
        category: A grouping such as preference, project, personal, or system.
    """
    if not _memory:
        return "My memory store is not available."
    return _memory.remember(fact, category)


@tool(category="memory")
def recall(query: str) -> str:
    """Search long-term memory for what you know about a subject.

    Args:
        query: What to look up.
    """
    if not _memory:
        return "My memory store is not available."
    found = _memory.recall_for(query)
    return found or f"I have nothing stored about {query}."


@tool(category="memory")
def forget(query: str) -> str:
    """Delete a stored fact from long-term memory.

    Args:
        query: Describes the fact to remove.
    """
    if not _memory:
        return "My memory store is not available."
    return _memory.forget(query)
