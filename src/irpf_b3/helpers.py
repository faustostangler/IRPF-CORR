"""Shared utilities for the irpf_b3 package.

Common functions used across multiple modules (sanitization, threading, etc.)
live here to avoid duplication.
"""
import re
import threading
import time

import unidecode


def worker_id() -> str:
    """Returns a short worker identifier (e.g. 'W0') from the current thread name."""
    name = threading.current_thread().name
    # ThreadPoolExecutor names threads as 'ThreadPoolExecutor-N_M'
    if "_" in name:
        return f"W{int(name.rsplit('_', 1)[-1])+1}"
    return name


def sanitize_filename(name: str) -> str:
    """Removes forbidden or unwanted characters from filenames."""
    if not name:
        return ""

    # 1. Remove diacritics to ASCII (e.g., "ação" -> "acao")
    s = unidecode.unidecode(name)

    # 2. Lowercase and trim outer spaces
    s = s.lower().strip()

    # 3. Strip punctuation and special characters (keeps alphanumeric, spaces, and hyphens)
    # This inherently removes dots, commas, slashes, etc.
    s = re.sub(r"[^\w\s-]", "", s)

    # 4. Collapse any internal runs of whitespace or hyphens into a single underscore
    s = re.sub(r"[-\s]+", "_", s)

    return s[:85]


def sanitize_foldername(name: str, default: str = "unknown") -> str:
    """Sanitizes a category name to be used safely as a folder name."""
    if not name:
        return default
    return sanitize_filename(name)


def progress(current: int, total: int, start_time: float = None) -> str:
    """Formats a unified progress string with optional ETA.

    Without start_time: [current/total 0.00%]
    With start_time:    [current/total 0.00%] [elapsed+remaining=total]
    """
    pct = (current / total * 100) if total > 0 else 0.0
    parts = [f"[{current}/{total} {pct:.2f}%]"]

    if start_time is not None:
        elapsed = time.time() - start_time
        avg_time = elapsed / current if current > 0 else 0
        remaining = avg_time * (total - current)
        total_time = elapsed + remaining

        def fmt(s: float) -> str:
            s = int(s)
            return f"{s // 3600}h{(s % 3600) // 60:02d}m{s % 60:02d}s"

        parts.append(f"[{fmt(elapsed)}+{fmt(remaining)}={fmt(total_time)}]")

    return " ".join(parts)


def extract_keyword_context(
    text: str,
    pattern: re.Pattern,
    context_chars: int = 500,
) -> list[str]:
    """Extract paragraph-sized context around each regex match.

    Returns deduplicated snippets with `context_chars` chars
    before and after each match, split on paragraph boundaries.
    """
    matches = list(pattern.finditer(text))
    if not matches:
        return []
        
    spans = []
    for m in matches:
        start_idx = max(0, m.start() - context_chars)
        end_idx = min(len(text), m.end() + context_chars)
        
        # Try to snap to paragraph boundaries \n\n
        para_start = text.rfind("\n\n", start_idx, m.start())
        if para_start != -1:
            start_idx = para_start + 2  # skip \n\n
            
        para_end = text.find("\n\n", m.end(), end_idx)
        if para_end != -1:
            end_idx = para_end
            
        spans.append([start_idx, end_idx])
        
    # Merge overlapping spans
    if not spans:
        return []
        
    spans.sort(key=lambda x: x[0])
    merged = [spans[0]]
    for current in spans[1:]:
        previous = merged[-1]
        if current[0] <= previous[1]:
            previous[1] = max(previous[1], current[1])
        else:
            merged.append(current)
            
    snippets = []
    for start_idx, end_idx in merged:
        snippet = text[start_idx:end_idx].strip()
        if snippet:
            snippets.append(snippet)
        
    return snippets
