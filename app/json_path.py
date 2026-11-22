"""Shared JSON path helpers for field discovery, collection, and SQL lookup."""
import re

_TOKEN = re.compile(r"\['((?:\\.|[^'\\])*)'\]|\[(\d+)\]|([^.\[\]]+)")
_SIMPLE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def _unquote(text):
    return re.sub(r"\\(.)", r"\1", text)


def _quote(text):
    return text.replace("\\", "\\\\").replace("'", "\\'")


def classify_json_type(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return "string"


def path_tokens(path):
    text = str(path or "").strip()
    if text.startswith("$"):
        text = text[1:]
    if not text:
        return []
    tokens = []
    pos = 0
    for match in _TOKEN.finditer(text):
        while pos < len(text) and text[pos] == ".":
            pos += 1
        if match.start() != pos:
            raise ValueError(f"Invalid JSON path: {path}")
        if match.group(1) is not None:
            tokens.append(_unquote(match.group(1)))
        elif match.group(2) is not None:
            tokens.append(int(match.group(2)))
        else:
            tokens.append(match.group(3))
        pos = match.end()
    if pos != len(text):
        raise ValueError(f"Invalid JSON path: {path}")
    return tokens


def format_path(tokens):
    out = ""
    for token in tokens:
        if isinstance(token, int):
            out += f"[{token}]"
        elif _SIMPLE_KEY.match(str(token)):
            out += ("." if out else "") + token
        else:
            out += "['" + _quote(str(token)) + "']"
    return out


def path_label(path):
    tokens = path_tokens(path)
    if not tokens:
        return ""
    last = tokens[-1]
    return f"[{last}]" if isinstance(last, int) else str(last)


def iter_terminal_paths(value, prefix=None):
    """Yield (path, value) for every terminal value in a JSON structure."""
    tokens = list(prefix or [])
    if isinstance(value, dict):
        if not value:
            yield format_path(tokens), value
            return
        for key, item in value.items():
            yield from iter_terminal_paths(item, tokens + [str(key)])
        return
    if isinstance(value, list):
        if not value:
            yield format_path(tokens), value
            return
        for index, item in enumerate(value):
            yield from iter_terminal_paths(item, tokens + [index])
        return
    yield format_path(tokens), value


def resolve_root(data, path):
    """Resolve dotted keys, quoted keys, and numeric array indexes."""
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


def to_sqlite_json_path(path):
    """Convert a field key to a SQLite json_extract path.

    Data rows store flat keys such as ``result.prov``. The whole key is one
    literal JSON object member, so dots and brackets are never path syntax.
    """
    key = str(path) if path is not None else ""
    return '$."' + key.replace('"', '""') + '"'
