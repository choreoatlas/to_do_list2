from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Todo:
    id: int
    title: str
    completed: bool


def normalize_title(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("title must be a string")
    title = value.strip()
    if not title:
        raise ValueError("title must not be empty")
    if len(title) > 200:
        raise ValueError("title must be at most 200 characters")
    return title


def normalize_completed(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("completed must be a boolean")
    return value
