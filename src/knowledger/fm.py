"""fm.py — minimal YAML frontmatter parser shared by every module.

Supports exactly what knowledger cards use: scalars, lists of flat dicts, inline comments.
Not a YAML parser. If you need one, your card format drifted.
"""
from __future__ import annotations

import re


def parse(text: str) -> tuple[dict, str]:
    """Return (frontmatter, body). Missing/broken frontmatter → ({}, text)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm_text = text[3:end].strip("\n")
    body = text[end + 4:]
    fm: dict = {}
    cur = None
    for raw in fm_text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0 and ":" in stripped and not stripped.startswith("- "):
            k, _, v = stripped.partition(":")
            k = k.strip()
            v = v.split("  #")[0].strip()
            if v == "":
                fm[k] = []
                cur = k
            else:
                fm[k] = _scalar(v)
                cur = None
        elif cur and stripped.startswith("- "):
            item: dict = {}
            for part in stripped[2:].split(","):
                if ":" in part:
                    kk, _, vv = part.partition(":")
                    item[kk.strip()] = _scalar(vv)
            fm[cur].append(item)
        elif cur and ":" in stripped and fm[cur] and isinstance(fm[cur][-1], dict):
            kk, _, vv = stripped.partition(":")
            fm[cur][-1][kk.strip()] = _scalar(vv)
    return fm, body


def _scalar(v: str):
    v = v.strip()
    if v.lower() == "null":
        return None
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v.strip("'\"")


def dump(fm: dict, body: str = "") -> str:
    """Inverse of parse for the subset we emit. Stable ordering: dict insertion order."""
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                lines.append(f"{k}:")
                for item in v:
                    if isinstance(item, dict):
                        pairs = list(item.items())
                        lines.append(f"  - {pairs[0][0]}: {pairs[0][1]}")
                        for kk, vv in pairs[1:]:
                            lines.append(f"    {kk}: {vv}")
                    else:
                        lines.append(f"  - {item}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif v is None:
            lines.append(f"{k}: null")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + ("\n" + body if body else "\n")


def read_card(p) -> tuple[dict, str]:
    from pathlib import Path
    return parse(Path(p).read_text(encoding="utf-8"))
