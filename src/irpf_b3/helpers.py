"""Shared utilities for the irpf_b3 package.

Common functions used across multiple modules (sanitization, threading, etc.)
live here to avoid duplication.
"""
import re
import threading

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
