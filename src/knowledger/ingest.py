"""ingest.py — documents in, index cards out, humans review.

Machine does the coarse work (infer block, slice candidate items); humans make the
judgement call (whose block, which items become instances). Nobody writes frontmatter
from scratch.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path

from . import fm
from .config import Config

TEXT_EXT = {".md", ".txt", ".html", ".csv", ".json"}
BINARY_EXT = {".doc", ".docx", ".pdf"}     # card created, body not extracted (needs OCR)
EXT_OK = TEXT_EXT | BINARY_EXT

API_ROW = re.compile(r"^\|\s*(GET|POST|PUT|DELETE|PATCH)\s*\|\s*`?([^|`]+)`?\s*\|\s*([^|]+?)\s*\|")
H3 = re.compile(r"^###\s+[\d.]+\s*(.+)$")
BOLD_BULLET = re.compile(r"^\s*-\s*\*\*([^*]{2,30})\*\*[：:]\s*(.+)$")
SRC_LINE = re.compile(r"`(raw/[^`]+)`")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def read_text(p: Path) -> str:
    if p.suffix.lower() in BINARY_EXT:
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


# ---------- scan ----------
def scan(cfg: Config, sources: list[str]) -> int:
    n = 0
    for src in sources:
        src_p = Path(src)
        if not src_p.exists():
            print(f"  ⚠ 不存在，跳过: {src}")
            continue
        files = [src_p] if src_p.is_file() else [f for f in sorted(src_p.rglob("*")) if f.is_file()]
        anchor = src_p.name if src_p.is_dir() else "misc"
        for f in files:
            if f.suffix.lower() not in EXT_OK or f.name.startswith("."):
                continue
            rel = f.relative_to(src_p) if src_p.is_dir() else Path(f.name)
            dst = cfg.raw / anchor / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and sha(dst) == sha(f):
                continue
            shutil.copy2(f, dst)
            n += 1
            print(f"  + {dst.relative_to(cfg.root)}")
    print(f"\nscan：新增/更新 {n} 份 → raw/")
    return 0


# ---------- cards ----------
def infer_block(cfg: Config, text: str, rel: Path) -> str:
    """Path hints first (dir name == a block name), then keyword score per block."""
    parts = {p.lower() for p in rel.parts}
    for b in cfg.blocks:
        if b in parts:
            return b
    score = dict.fromkeys(cfg.blocks, 0)
    for b, spec in cfg.blocks.items():
        for w in spec.hints:
            if w in text:
                score[b] += 1
    best = max(score, key=lambda k: score[k])
    if score[best] == 0:
        return next(iter(cfg.blocks))  # first declared block is the fallback
    return best


def extract_candidates(cfg: Config, text: str) -> list[dict]:
    out: list[dict] = []
    noise = set(cfg.section_noise)
    for line in text.splitlines():
        m = API_ROW.match(line)
        if m:
            out.append({"kind": "api", "title": m.group(3).strip(), "method": m.group(1), "path": m.group(2).strip()})
            continue
        m = H3.match(line)
        if m and not any(n in m.group(1) for n in noise):
            out.append({"kind": "section", "title": m.group(1).strip()})
            continue
        m = BOLD_BULLET.match(line)
        if m:
            out.append({"kind": "bullet", "title": m.group(1).strip(), "note": m.group(2).strip()[:120]})
    seen, uniq = set(), []
    for c in out:
        if c["title"] not in seen:
            seen.add(c["title"])
            uniq.append(c)
    return uniq


def candidate_tag(c: dict) -> str:
    if c["kind"] == "api":
        return f"`{c['method']} {c['path']}`"
    return "§" if c["kind"] == "section" else "•"


def parse_candidates(body: str) -> list[dict]:
    """Inverse of the numbered list we write into cards (used by compile)."""
    out = []
    for line in body.splitlines():
        m = re.match(r"^\d+\.\s+(.*)$", line)
        if not m:
            continue
        item = m.group(1)
        api = re.match(r"`(GET|POST|PUT|DELETE|PATCH)\s+([^`]+)`\s*(.+)", item)
        if api:
            out.append({"kind": "api", "method": api.group(1), "path": api.group(2).strip(), "title": api.group(3).strip()})
            continue
        sec = re.match(r"^§\s+(.+)$", item)
        if sec:
            out.append({"kind": "section", "title": sec.group(1).strip()})
            continue
        bul = re.match(r"^•\s+(.+?)(?:\u3000|$)", item)
        if bul:
            out.append({"kind": "bullet", "title": bul.group(1).strip()})
    return out


@dataclass
class ManifestItem:
    doc_id: str
    path: str
    sha: str
    block: str
    type: str
    candidates: int
    needs_ocr: bool


def cards(cfg: Config) -> int:
    if not cfg.raw.exists() or not any(cfg.raw.rglob("*")):
        print("raw/ 是空的，先 `knowledger scan <目录>`")
        return 1
    cfg.staging.mkdir(exist_ok=True)
    manifest: list[ManifestItem] = []
    today = date.today().isoformat()
    for p in sorted(cfg.raw.rglob("*")):
        if not p.is_file() or p == cfg.raw / "README.md":   # 只跳过归档区自述；子目录里的 README.md 是正经文档
            continue
        rel = p.relative_to(cfg.raw)
        text = read_text(p)
        block = infer_block(cfg, text, rel) if text else next(iter(cfg.blocks))
        cands = extract_candidates(cfg, text) if text else []
        doc_id = "D-" + sha(p)[:8].upper()
        needs_ocr = not text
        manifest.append(ManifestItem(doc_id, str(rel), sha(p), block, cfg.blocks[block].default_type, len(cands), needs_ocr))
        out = cfg.staging / f"{doc_id}.md"
        existing = fm.read_card(out)[0] if out.exists() else {}
        owner = existing.get("owner", "待审")  # re-running cards must not wipe a human's review
        lines = [
            "---", f"id: {doc_id}", "type: Doc", f"name: {p.name}", f"owner: {owner}", f"block: {block}",
            "status: draft", f"valid_from: {today}", "version: 1",
            "sources:", f"  - doc: {doc_id}", "    version: 1",
            "relates_to: []", "---", "",
            "## 来源", f"`raw/{rel}`　sha `{sha(p)[:8]}`", "",
            "## 机器推断（人审：对就留，错就改，不确定标待确认）",
            f"- 归属块：**{block}**（依据：路径 / 关键词计分）",
            f"- 对象类：{cfg.blocks[block].default_type}",
        ]
        if needs_ocr:
            lines.append("- ⚠ 二进制文档（doc / docx / pdf）：未抽正文，需 OCR 后重跑 cards")
        lines += ["", f"## 候选条目（{len(cands)} 条；人审后决定哪些升为实例）", ""]
        for i, c in enumerate(cands, 1):
            extra = f"　{c['note']}" if c.get("note") else ""
            lines.append(f"{i}. {candidate_tag(c)} {c['title']}{extra}")
        if not cands:
            lines.append("（未切出候选条目：文档可能没有结构化表行 / 三级标题；人读后手工补）")
        lines += ["", "## 待验证 / 争议", "待人审：归属块、owner、哪些候选条目升为实例。"]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    import json
    (cfg.ontology / "raw_manifest.json").write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "docs": len(manifest), "candidates": sum(m.candidates for m in manifest),
        "items": [asdict(m) for m in manifest],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"cards：{len(manifest)} 张 → staging/，候选条目 {sum(m.candidates for m in manifest)} 条")
    return 0


# ---------- review ----------
def review(cfg: Config, block: str | None) -> int:
    if not cfg.staging.exists():
        print("staging/ 为空，先 `knowledger cards`")
        return 1
    by_block: dict[str, int] = {}
    needs_ocr = 0
    listed = []
    for c in sorted(cfg.staging.glob("*.md")):
        f, body = fm.read_card(c)
        b = f.get("block", "?")
        by_block[b] = by_block.get(b, 0) + 1
        if "需 OCR" in body:
            needs_ocr += 1
        if block and b == block:
            listed.append(f"  {c.name}  owner={f.get('owner')}  {f.get('name')}")
    print(f"staging/ 共 {sum(by_block.values())} 张待审")
    print("按推断块分布：" + "，".join(f"{b} {n}" for b, n in sorted(by_block.items())))
    if needs_ocr:
        print(f"其中 {needs_ocr} 张是二进制文档，需 OCR")
    if block:
        print(f"\n--- {block} 块 ---")
        print("\n".join(listed) if listed else "  （无）")
    print("\n审一张卡 = 改 owner / block，勾候选条目升实例；改完移到 blocks/<块>/，跑 `knowledger check`")
    return 0


def iter_cards(cfg: Config, include_staging: bool) -> list[tuple[Path, dict, str]]:
    out = []
    if cfg.blocks_dir.exists():
        for p in sorted(cfg.blocks_dir.rglob("*.md")):
            if p.name.startswith("_"):
                continue
            f, body = fm.read_card(p)
            out.append((p, f, body))
    if include_staging and cfg.staging.exists():
        for p in sorted(cfg.staging.rglob("*.md")):
            f, body = fm.read_card(p)
            if f.get("owner", "待审") != "待审":
                out.append((p, f, body))
    return out


def source_of(body: str, cfg: Config) -> tuple[str, Path | None]:
    m = SRC_LINE.search(body)
    if not m:
        return "", None
    rel = m.group(1)
    p = cfg.root / rel
    return rel, (p if p.exists() else None)
