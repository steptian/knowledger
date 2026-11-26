"""config.py — the ONLY place that knows about knowledger.yaml.

Config file format: JSON-compatible YAML subset (flow style). We don't ship PyYAML.
Every hardcoded value in the original scripts lives here now.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Config is missing or malformed. Say which line, don't guess."""


@dataclass
class Block:
    name: str
    owners: list[str] | None      # None = unowned → gate 3 rejects everyone
    hints: list[str]
    default_type: str


@dataclass
class TypeSpec:
    name: str
    block: str
    required: list[str]
    enum: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Config:
    name: str
    blocks: dict[str, Block]
    types: dict[str, TypeSpec]
    relations: list[str]
    section_noise: list[str]
    status_enum: list[str]
    common_required: list[str]
    root: Path

    @property
    def raw(self) -> Path: return self.root / "raw"
    @property
    def staging(self) -> Path: return self.root / "staging"
    @property
    def blocks_dir(self) -> Path: return self.root / "blocks"
    @property
    def ontology(self) -> Path: return self.root / "ontology"
    @property
    def build(self) -> Path: return self.root / "build"
    @property
    def snapshot(self) -> Path: return self.root / "snapshot"

    def owners_of(self, block: str) -> list[str] | None:
        b = self.blocks.get(block)
        return b.owners if b else None


def _find_root(start: Path) -> Path:
    """Walk upward until knowledger.yaml is found. Like git. kbase.yaml 是旧名，仍认。"""
    p = start.resolve()
    for cand in [p, *p.parents]:
        if (cand / "knowledger.yaml").exists() or (cand / "kbase.yaml").exists():
            return cand
            return cand
    raise ConfigError(
        f"没找到 knowledger.yaml（从 {start} 往上找到根目录都没有）。先在工作目录运行 `knowledger init`。"
    )


def _parse_scalar(v: str):
    v = v.strip()
    if v in ("null", "~", ""):
        return None
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_parse_scalar(x) for x in inner.split(",")] if inner else []
    if v.startswith("{") and v.endswith("}"):
        out = {}
        inner = v[1:-1].strip()
        if not inner:
            return out
        for part in re.split(r",\s*(?=[\w\u4e00-\u9fa5]+\s*:)", inner):
            k, _, val = part.partition(":")
            out[k.strip()] = _parse_scalar(val)
        return out
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v.strip("'\"")


def _parse_line_yaml(text: str) -> dict:
    """Parse the YAML subset we emit in init: 2-level nesting, flow collections, comments.
    Deliberately small. If a user needs full YAML they can write knowledger.json instead."""
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, sep, val = line.strip().partition(":")
        if not sep:
            raise ConfigError(f"knowledger.yaml 第 {lineno} 行：应为 `key: value`，得到 `{line.strip()}`")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        key = key.strip()
        if val.strip() == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
        else:
            parent[key] = _parse_scalar(val)
    return root


def load(start: Path | None = None) -> Config:
    root = _find_root(start or Path.cwd())
    yml, js = root / "knowledger.yaml", root / "knowledger.json"
    if not yml.exists() and (root / "kbase.yaml").exists():
        yml = root / "kbase.yaml"   # 旧名兼容：老实例不用改名就能继续用
    if js.exists():
        try:
            data = json.loads(js.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"knowledger.json 不是合法 JSON: {e}") from e
    elif yml.exists():
        data = _parse_line_yaml(yml.read_text(encoding="utf-8"))
    else:
        raise ConfigError("knowledger.yaml 不存在")
    try:
        blocks = {
            n: Block(n, b.get("owners"), list(b.get("hints", [])), b["default_type"])
            for n, b in data["blocks"].items()
        }
        types = {
            n: TypeSpec(n, t["block"], list(t.get("required", [])), dict(t.get("enum", {})))
            for n, t in data["types"].items()
        }
        cfg = Config(
            name=data["name"],
            blocks=blocks,
            types=types,
            relations=list(data["relations"]),
            section_noise=list(data.get("section_noise", [])),
            status_enum=list(data["status_enum"]),
            common_required=list(data.get("common_required", ["id", "type", "name", "owner", "block", "status", "sources"])),
            root=root,
        )
    except (KeyError, TypeError) as e:
        raise ConfigError(f"knowledger.yaml 缺字段或结构不对: {e}") from e
    # cross-check: every type's block exists; every block's default_type exists
    for t in types.values():
        if t.block not in blocks:
            raise ConfigError(f"type {t.name} 指向未定义的 block `{t.block}`")
    for b in blocks.values():
        if b.default_type not in types:
            raise ConfigError(f"block {b.name} 的 default_type `{b.default_type}` 未在 types 里定义")
    return cfg
