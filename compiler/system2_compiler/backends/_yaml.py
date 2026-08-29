"""Deterministic, stdlib-only block-YAML serializer (emit-only; no parser).

Supports a closed subset of block YAML: mappings, sequences, scalars
(str / int / bool / None) and literal block scalars for multi-line strings.
Fixed 2-space indent, insertion-ordered keys (the caller controls order for
determinism), conservative double-quote predicate, LF endings, and exactly one
trailing newline. Any value that cannot be block-formatted falls back to a
JSON-compatible flow scalar (valid YAML, since YAML is a JSON superset).

Block literal scalars carry an EXPLICIT indentation indicator pinned to the
content indent (``|2`` at the fixed 2-space step) plus an explicit chomping
indicator: ``|2`` (clip — keep the single trailing newline) when the value ends
in ``\\n``, ``|2-`` (strip — no trailing newline) otherwise. The explicit ``|2``
stops YAML from auto-detecting the block indent from the first content line, so a
block whose first line is blank or itself indented still parses faithfully.

Correctness is gated empirically by ``test_yaml_serializer.py``'s direct unit
tests, not by a hand-written full YAML grammar.
"""

import json
import re

_NEEDS_QUOTE_RE = re.compile(r"[:#\{\}\[\],&*!|>'\"%@`]")

_RESERVED_SCALARS = frozenset(
    {
        "true",
        "false",
        "null",
        "yes",
        "no",
        "on",
        "off",
        "none",
        "~",
        "",
    }
)


def dump(data, *, indent=2):
    """Serialize ``data`` to deterministic block YAML with a single trailing newline."""
    lines = []
    if isinstance(data, dict):
        _dump_mapping(data, 0, indent, lines)
    elif isinstance(data, (list, tuple)):
        _dump_sequence(list(data), 0, indent, lines)
    else:
        lines.append(_dump_scalar(data))
    body = "\n".join(lines)
    if not body.endswith("\n"):
        body += "\n"
    return body


def _dump_mapping(obj, level, indent, lines):
    pad = " " * (level * indent)
    if not obj:
        lines.append(pad + "{}")
        return
    for key, value in obj.items():
        key_text = _dump_key(key)
        if isinstance(value, dict):
            if value:
                lines.append(f"{pad}{key_text}:")
                _dump_mapping(value, level + 1, indent, lines)
            else:
                lines.append(f"{pad}{key_text}: {{}}")
        elif isinstance(value, (list, tuple)):
            if value:
                lines.append(f"{pad}{key_text}:")
                _dump_sequence(list(value), level, indent, lines)
            else:
                lines.append(f"{pad}{key_text}: []")
        elif isinstance(value, str) and "\n" in value:
            lines.append(f"{pad}{key_text}: {_block_header(value, indent)}")
            _block_literal(value, level + 1, indent, lines)
        else:
            lines.append(f"{pad}{key_text}: {_dump_scalar(value)}")


def _dump_sequence(seq, level, indent, lines):
    pad = " " * (level * indent)
    if not seq:
        lines.append(pad + "[]")
        return
    for item in seq:
        if isinstance(item, dict):
            if not item:
                lines.append(f"{pad}- {{}}")
                continue
            first = True
            for key, value in item.items():
                key_text = _dump_key(key)
                marker = f"{pad}- " if first else f"{pad}{' ' * indent}"
                first = False
                if isinstance(value, dict):
                    if value:
                        lines.append(f"{marker}{key_text}:")
                        _dump_mapping(value, level + 2, indent, lines)
                    else:
                        lines.append(f"{marker}{key_text}: {{}}")
                elif isinstance(value, (list, tuple)):
                    if value:
                        lines.append(f"{marker}{key_text}:")
                        _dump_sequence(list(value), level + 1, indent, lines)
                    else:
                        lines.append(f"{marker}{key_text}: []")
                elif isinstance(value, str) and "\n" in value:
                    lines.append(f"{marker}{key_text}: {_block_header(value, indent)}")
                    _block_literal(value, level + 2, indent, lines)
                else:
                    lines.append(f"{marker}{key_text}: {_dump_scalar(value)}")
        elif isinstance(item, (list, tuple)):
            lines.append(f"{pad}-")
            _dump_sequence(list(item), level + 1, indent, lines)
        elif isinstance(item, str) and "\n" in item:
            lines.append(f"{pad}- {_block_header(item, indent)}")
            _block_literal(item, level + 1, indent, lines)
        else:
            lines.append(f"{pad}- {_dump_scalar(item)}")


def _block_header(text, content_indent):
    """Literal block header with explicit indentation + chomping indicators.

    ``content_indent`` is the number of spaces the content sits beyond the line
    bearing the ``|`` (the fixed 2-space step) — emitted as the YAML indentation
    indicator so the parser never auto-detects indent from a blank/indented first
    line. Chomping: clip (keep one trailing newline) when ``text`` ends in a
    newline, strip (``-``) otherwise, so trailing-newline handling is faithful.
    """
    chomp = "" if text.endswith("\n") else "-"
    return f"|{content_indent}{chomp}"


def _block_literal(text, level, indent, lines):
    pad = " " * (level * indent)
    for line in text.split("\n"):
        lines.append(f"{pad}{line}" if line else "")


def _dump_key(key):
    if not isinstance(key, str):
        key = _dump_scalar(key)
        return key
    return key if not _needs_quote(key) else json.dumps(key)


def _dump_scalar(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return value if not _needs_quote(value) else json.dumps(value)
    return json.dumps(value)


def _needs_quote(text):
    if text == "" or text != text.strip():
        return True
    if text.lower() in _RESERVED_SCALARS:
        return True
    if _NEEDS_QUOTE_RE.search(text):
        return True
    if text[0] in "-?:#&*!|>%@`,[]{}\"'":
        return True
    if _looks_numeric(text):
        return True
    return False


def _looks_numeric(text):
    try:
        int(text)
        return True
    except ValueError:
        pass
    try:
        float(text)
        return True
    except ValueError:
        return False
