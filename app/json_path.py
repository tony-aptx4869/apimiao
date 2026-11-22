"""Resolve a small JSON path subset shared by field discovery and collection."""
import re

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def path_tokens(path):
    text = str(path or "").strip()
    if not text:
        return []
    tokens = []
    pos = 0
    for match in _TOKEN.finditer(text):
        if match.start() != pos:
            raise ValueError(f"Invalid JSON path: {path}")
        if match.group(1) is not None:
            tokens.append(match.group(1))
        else:
            tokens.append(int(match.group(2)))
        pos = match.end()
    if pos != len(text):
        raise ValueError(f"Invalid JSON path: {path}")
    return tokens


def resolve_root(data, path):
    """Resolve dotted object keys and numeric array indexes. Empty path returns data."""
    current = data
    for token in path_tokens(path):
        if isinstance(token, int):
            if not isinstance(current, (list, tuple)) or token >= len(current):
                raise ValueError(f"JSON path index out of range: {path}")
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                raise ValueError(f"JSON path not found: {path}")
            current = current[token]
    return current
