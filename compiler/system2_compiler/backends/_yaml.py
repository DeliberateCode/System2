"""Deterministic, stdlib-only block-YAML serializer (emit-only; no parser)."""

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
    """Literal block header with explicit indentation + chomping indicators."""
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
    if re.fullmatch(r"[-+]?0[xX][0-9a-fA-F]+", text):
        return True
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
