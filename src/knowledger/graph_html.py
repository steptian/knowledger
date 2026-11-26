"""graph_html.py — build/graph.html: a single-file, dependency-free view over graph.json.

Purpose is narrow: help a human SEE the graph and COPY a command for an Agent to run.
Editing happens in the CLI (knowledger approve ...), not by dragging on the canvas.
"""
from __future__ import annotations

from pathlib import Path

from .config import Config

TEMPLATE = Path(__file__).parent / "templates" / "graph.html"


def write_html(cfg: Config) -> Path:
    data = (cfg.build / "graph.json").read_text(encoding="utf-8")
    html = TEMPLATE.read_text(encoding="utf-8")
    # "</" inside a <script> block would close the script; escape it for the JSON payload
    html = html.replace("__NAME__", cfg.name).replace("__DATA__", data.replace("</", "<\\/"))
    out = cfg.build / "graph.html"
    out.write_text(html, encoding="utf-8")
    return out
