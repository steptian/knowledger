"""compile.py — extract / check (three gates) / recall / drift.

Ported from personal-growth/scripts/ontology.py. Source there was SQL in code;
here it's reviewed cards. Same three moves: extract with fingerprints and anchors,
check format / drift / traceability, recall materials (advisory, never rules).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from . import fm
from .config import Config
from .core import GateError, check_instance, load_glossary
from .ingest import iter_cards, parse_candidates, source_of


def _read_json(p: Path, root: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise GateError(f"{p.relative_to(root)} 读不了: {e}") from e


# ---------- terms ----------
def bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    out: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fa5]+", s):
        out.update(run[i:i + 2] for i in range(len(run) - 1))
        if len(run) == 1:
            out.add(run)
    out.update(re.findall(r"[A-Za-z0-9_]+", s.lower()))
    return out


def terms(s: str) -> set[str]:
    return bigrams(s) | set(re.findall(r"[\u4e00-\u9fa5]{2,6}", s))


# ---------- extract ----------
def extract(cfg: Config) -> dict:
    grow, aliases = load_glossary(cfg)
    entities, edges = [], []
    for p, f, body in iter_cards(cfg, include_staging=True):
        if f.get("status") in ("deprecated", "已废弃"):
            continue
        src_rel, src_path = source_of(body, cfg)
        cands = parse_candidates(body) if f.get("type") == "Doc" else []
        entities.append({
            "id": f.get("id"), "type": f.get("type"), "name": f.get("name"), "owner": f.get("owner"),
            "block": f.get("block"), "status": f.get("status"), "sources": f.get("sources", []),
            "evidence": {"file": str(p.relative_to(cfg.root)), "symbol": f"{f.get('type')}:{f.get('id')}",
                         "src": src_rel,
                         "src_sha": hashlib.sha256(src_path.read_bytes()).hexdigest()[:12] if src_path else None},
            "candidates": cands,
            "glossary_hits": sorted({a for a in aliases if a in (f.get("name") or "")}),
        })
        for r in f.get("relates_to", []) or []:
            if not isinstance(r, dict) or r.get("rel") not in cfg.relations:
                continue
            rvw = {"verdict": r.get("review"), "by": r.get("review_by"), "at": r.get("review_at"), "note": r.get("review_note")}
            if rvw["verdict"] == "wrong":
                continue  # 人审判错的边不进产物
            other = r.get("from") or r.get("to")
            if other:
                edges.append({"rel": r["rel"], "from": f.get("id"), "other": other,
                              "direction": "in" if r.get("from") else "out",
                              "review": rvw,
                              "evidence": {"file": str(p.relative_to(cfg.root)), "symbol": f"{f.get('id')}#{r['rel']}"}})
    rewrite: dict[str, str] = {}
    for r in grow:
        std = r["标准术语"]
        rewrite[std] = std
        for a in re.split(r"[;；,，/]", r.get("别名", "")):
            if a.strip():
                rewrite[a.strip()] = std
    return {
        "meta": {"name": cfg.name, "generated_at": datetime.now().isoformat(timespec="seconds"),
                 "method": "extract/check/recall, ported from personal-growth ontology.py",
                 "cards": len(entities), "edges": len(edges), "rewrite_terms": len(rewrite)},
        "entities": entities, "edges": edges, "glossary": grow, "rewrite": rewrite,
        "types": {n: {"block": t.block} for n, t in cfg.types.items()},
        "relations": cfg.relations,
    }


# ---------- check ----------
def check(onto: dict, cfg: Config, fresh: dict | None = None) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for e in onto["entities"]:
        for k in ("id", "type", "name", "owner", "block", "status"):
            if not str(e.get(k, "")).strip():
                problems.append(f"[格式] {e.get('id')} 缺字段 {k}")
        if e["id"] in seen:
            problems.append(f"[格式] 重复 id {e['id']}")
        seen.add(e["id"])
        if not e["evidence"].get("symbol"):
            problems.append(f"[格式] {e['id']} 缺锚点")
        if e["type"] not in cfg.types:
            problems.append(f"[格式] {e['id']} type 非法: {e['type']}")
        if not e.get("sources"):
            problems.append(f"[格式] {e['id']} sources 为空")
    for ed in onto["edges"]:
        if ed["rel"] not in cfg.relations:
            problems.append(f"[格式] 边 {ed['evidence']['symbol']} 关系非法 {ed['rel']}")
    if not onto["entities"]:
        problems.append("[格式] 一张卡都没编进去")
    if not onto["rewrite"]:
        problems.append("[格式] rewrite 表为空 —— glossary.csv 坏了")

    if fresh is not None:
        fmap = {e["id"]: e["evidence"].get("src_sha") for e in fresh["entities"]}
        for e in onto["entities"]:
            if e["id"] not in fmap:
                problems.append(f"[指纹] {e['id']} 在源里已找不到")
                continue
            old_sha, got = e["evidence"].get("src_sha"), fmap[e["id"]]
            if old_sha is None and got is None:
                continue  # hand-written instance without a raw source: gates 1 & 3 only
            if got is None:
                problems.append(f"[指纹] {e['id']} 的源文档已消失")
            elif old_sha != got:
                problems.append(f"[指纹] {e['id']} 源漂移 {old_sha} → {got}")

    for e in onto["entities"]:
        ev = e["evidence"]
        if not (cfg.root / ev["file"]).exists():
            problems.append(f"[回溯] {e['id']} 卡文件不存在: {ev['file']}")
        if ev.get("src") and not (cfg.root / ev["src"]).exists():
            problems.append(f"[回溯] {e['id']} 源文档不存在: {ev['src']}")
    return problems


# ---------- recall ----------
def recall(onto: dict, query: str, limit: int = 8) -> dict:
    q = query
    for alias in sorted(onto["rewrite"], key=len, reverse=True):
        if alias in q:
            q = q.replace(alias, onto["rewrite"][alias])
    qt = terms(q)
    scored = []
    for e in onto["entities"]:
        if e["status"] in ("deprecated", "已废弃"):
            continue
        blob = " ".join([e["name"] or "", e["type"] or "", " ".join(e["glossary_hits"]),
                         " ".join(c["title"] for c in e["candidates"])])
        s = len(qt & terms(blob))
        if s:
            scored.append((s, e["id"], e))
    scored.sort(key=lambda x: (-x[0], x[1]))
    hits = [e for _, _, e in scored[:limit]]
    ids = {e["id"] for e in hits}
    nbrs = [ed for ed in onto["edges"] if ed["from"] in ids or ed["other"] in ids]
    return {
        "query": query, "rewritten": q if q != query else None,
        "note": "以下是材料（advisory），不是硬规则；用不用怎么用交给模型判断。",
        "hits": [{"id": e["id"], "type": e["type"], "name": e["name"], "owner": e["owner"], "block": e["block"],
                  "src": e["evidence"]["src"], "candidates": [c["title"] for c in e["candidates"][:12]]} for e in hits],
        "edges": nbrs,
    }


# ---------- snapshot ----------
def write_snapshot(cfg: Config, onto: dict) -> Path:
    cfg.build.mkdir(exist_ok=True)
    out = cfg.build / "SNAPSHOT.md"
    lines = [f"# {cfg.name} · AI 只读快照", "",
             f"> 生成 {onto['meta']['generated_at']} · {onto['meta']['cards']} 张卡 · deprecated 已排除 · 别把这文件当规则", ""]
    for blk in cfg.blocks:
        grp = [e for e in onto["entities"] if e["block"] == blk and e["status"] not in ("deprecated", "已废弃")]
        if not grp:
            continue
        lines += [f"## {blk}（{len(grp)}）", ""]
        for e in grp:
            lines.append(f"### {e['type']} {e['id']} · {e['name']}")
            lines.append(f"owner {e['owner']} · status {e['status']} · 源 `{e['evidence']['src']}`")
            if e["glossary_hits"]:
                lines.append(f"术语：{'、'.join(e['glossary_hits'])}")
            for c in e["candidates"][:15]:
                tag = f"`{c['method']} {c['path']}` " if c["kind"] == "api" else ""
                lines.append(f"- {tag}{c['title']}")
            lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def write_graph(cfg: Config, onto: dict) -> Path:
    """nodes + edges for graph.html / Mermaid. Deprecated excluded."""
    # 自包含：定义 + 候选条目 + 源文片段都带上，html / Agent 读这一个文件就够
    def _def(src):
        if not src:
            return ""
        try:
            t = (cfg.root / src).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        m = re.search(r"## 定义\s*\n(.*?)(?=\n## |\Z)", t, re.S)
        return m.group(1).strip()[:200] if m else ""
    nodes = [{"id": e["id"], "name": e["name"], "type": e["type"], "block": e["block"],
              "status": e["status"], "owner": e["owner"], "src": e["evidence"]["src"],
              "candidates": [c["title"] for c in e["candidates"][:20]],
              "card": e["evidence"]["file"]} for e in onto["entities"]]
    by_id = {e["id"]: e for e in onto["entities"]}
    for n in nodes:
        ent = by_id[n["id"]]
        body = ""
        try:
            body = (cfg.root / ent["evidence"]["file"]).read_text(encoding="utf-8")
        except OSError:
            pass
        m = re.search(r"## 定义\s*\n(.*?)(?=\n## |\Z)", body, re.S)
        n["definition"] = m.group(1).strip()[:240] if m else ""
    edges = [{"from": ed["from"], "to": ed["other"], "rel": ed["rel"], "direction": ed["direction"],
              "reviewed": bool((ed.get("review") or {}).get("verdict")),
              "review": ed.get("review") or {}, "card": ed["from"]} for ed in onto["edges"]]
    # 待认领 = staging 里还没填 owner 的卡。不进 nodes / AI 产物（没人认的内容不算知识），页面靠它显示真实待办
    unclaimed = []
    for p in sorted(cfg.staging.glob("*.md")) if cfg.staging.exists() else []:
        f, body = fm.read_card(p)
        if f.get("owner", "待审") == "待审":
            src = re.search(r"`(raw/[^`]+)`", body)
            unclaimed.append({"id": f.get("id"), "name": f.get("name"), "block": f.get("block"),
                              "card": str(p.relative_to(cfg.root)), "src": src.group(1) if src else "",
                              "needs_ocr": "需 OCR" in body})
    out = cfg.build / "graph.json"
    out.write_text(json.dumps({"name": cfg.name, "nodes": nodes, "edges": edges, "unclaimed": unclaimed,
                               "relations": cfg.relations}, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def mermaid(cfg: Config, onto: dict) -> str:
    by_id = {e["id"]: e for e in onto["entities"]}
    lines = ["graph LR"]
    for e in onto["entities"]:
        shape = f'{e["id"]}["{e["name"]}<br/><small>{e["type"]} · {e["block"]}</small>"]'
        lines.append(f"  {shape}")
    for ed in onto["edges"]:
        if ed["other"] not in by_id:
            continue
        tag = ed["rel"] + ("" if (ed.get("review") or {}).get("verdict") else " ⏳")
        lines.append(f'  {ed["from"]} -->|{tag}| {ed["other"]}')
    return "\n".join(lines) + "\n"


def build(cfg: Config) -> int:
    onto = extract(cfg)
    cfg.build.mkdir(exist_ok=True)
    (cfg.build / "ontology.json").write_text(json.dumps(onto, ensure_ascii=False, indent=1), encoding="utf-8")
    (cfg.build / "rewrite.json").write_text(json.dumps(onto["rewrite"], ensure_ascii=False, indent=1), encoding="utf-8")
    write_graph(cfg, onto)
    (cfg.build / "graph.mmd").write_text(mermaid(cfg, onto), encoding="utf-8")
    from .graph_html import write_html
    write_html(cfg)
    snap = write_snapshot(cfg, onto)
    # 关 1/3/6 与 `knowledger check` 读同一张表。产物照常写出（图要能看待审项），不过关就非零退出
    probs = check(onto, cfg) + [
        f"{p.relative_to(cfg.root)}: {e}"
        for p, f, _ in iter_cards(cfg, include_staging=False) for e in check_instance(f, cfg)]
    for p in probs:
        print(f"  ✗ {p}")
    m = onto["meta"]
    print(f"build：{m['cards']} 张卡 → {m['edges']} 条边 → {m['rewrite_terms']} 个 rewrite 词；{len(probs)} 个问题 → {snap.relative_to(cfg.root)}")
    if probs:
        print("  产物已写出但未过关（清单见上）：处理后 `knowledger check` → `knowledger build`")
    return 1 if probs else 0


def check_cmd(cfg: Config) -> int:
    p = cfg.build / "ontology.json"
    if not p.exists():
        print("先 `knowledger build`")
        return 1
    onto = _read_json(p, cfg.root)
    probs = check(onto, cfg, fresh=extract(cfg))
    for q in probs:
        print(f"  ✗ {q}")
    print(f"三道关：{len(probs)} 个问题")
    return 1 if probs else 0


def recall_cmd(cfg: Config, query: str) -> int:
    p = cfg.build / "ontology.json"
    if not p.exists():
        print("先 `knowledger build`")
        return 1
    onto = _read_json(p, cfg.root)
    print(json.dumps(recall(onto, query), ensure_ascii=False, indent=1))
    return 0


def drift(cfg: Config) -> int:
    """Gate 2 alone: which raw/ sources changed since last `cards`."""
    mp = cfg.ontology / "raw_manifest.json"
    if not mp.exists():
        print("先 `knowledger cards`")
        return 1
    manifest = _read_json(mp, cfg.root)
    changed, gone = [], []
    for it in manifest["items"]:
        f = cfg.raw / it["path"]
        if not f.exists():
            gone.append(it["path"])
        elif hashlib.sha256(f.read_bytes()).hexdigest()[:12] != it["sha"]:
            changed.append(it["path"])
    for c in changed:
        print(f"  待重审 ← {c} 源已变")
    for g in gone:
        print(f"  已删除 ← {g}")
    print(f"源漂移：变化 {len(changed)} · 消失 {len(gone)}")
    return 0
