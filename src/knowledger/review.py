"""review.py — the three human-review actions, each at its smallest unit.

edges  : one edge A --rel--> B  → ok | wrong         (gate 6; build refuses unreviewed edges)
defs   : one card's ## 定义    → edit against source  (no CLI action; opens the card)
tree   : contains edges        → indented tree        (reparent = edit the edge's from)

claim  : staging card → owner + blocks/<块>/     (gate 3 table; step zero — unclaimed cards never compile)
Design rule (Palantir Proposal / HITL literature): review at the smallest independently
judgeable unit, with evidence next to the verdict. Nobody "approves an ontology".
"""
from __future__ import annotations

import re
from datetime import datetime

from . import fm
from .config import Config
from .ingest import iter_cards


def _edges(cfg: Config) -> list[dict]:
    """All edges from blocks/ (+ reviewed staging), with card refs."""
    out = []
    for p, f, body in iter_cards(cfg, include_staging=True):
        for i, r in enumerate(f.get("relates_to", []) or []):
            if not isinstance(r, dict):
                continue
            rvw = {"verdict": r.get("review"), "by": r.get("review_by"), "note": r.get("review_note")}
            out.append({"card": f.get("id"), "path": p, "idx": i, "rel": r.get("rel"),
                        "from": r.get("from"), "to": r.get("to"), "review": rvw,
                        "block": f.get("block"), "status": f.get("status")})
    return out


def _definition(body: str) -> str:
    m = re.search(r"## 定义\s*\n(.*?)(?=\n## |\Z)", body, re.S)
    return m.group(1).strip() if m else "（无 ## 定义 段）"


def _by_id(cfg: Config) -> dict[str, tuple[dict, str]]:
    return {str(f.get("id")): (f, b) for _, f, b in iter_cards(cfg, include_staging=True) if f.get("id")}


def _source_lines(cfg: Config, body: str, names: list[str]) -> list[str]:
    """Lines in the source doc where any of `names` appears. List all, don't guess."""
    m = re.search(r"`(raw/[^`]+)`", body)
    if not m:
        return []
    src = cfg.root / m.group(1)
    if not src.exists() or src.suffix.lower() not in {".md", ".txt", ".html", ".csv", ".json"}:
        return []
    hits = []
    for i, line in enumerate(src.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        if any(n and n in line for n in names):
            hits.append(f"  L{i}: {line.strip()[:160]}")
    return hits[:12]


def run(cfg: Config, what: str, block: str | None) -> int:
    if what == "cards":
        from .ingest import review as _r
        return _r(cfg, block)
    if what == "edges":
        return _review_edges(cfg, block)
    if what == "defs":
        return _review_defs(cfg, block)
    return _review_tree(cfg, block)


def _review_edges(cfg: Config, block: str | None) -> int:
    edges = _edges(cfg)
    if block:
        edges = [e for e in edges if e["block"] == block]
    pending = [e for e in edges if e["review"].get("verdict") not in ("ok", "wrong")]
    cards = _by_id(cfg)
    print(f"边：共 {len(edges)} 条，待审 {len(pending)} 条\n")
    for e in pending:
        # 子卡 e["card"] 声明这条边；from 边 = 「我(card) 属于 from」，to 边 = 「我 指向 to」
        if e["from"]:
            left_id, right_id = e["from"], e["card"]
        else:
            left_id, right_id = e["card"], e["to"]
        a, b = cards.get(left_id), cards.get(right_id)
        fa, fb = (a[0] if a else {}), (b[0] if b else {})
        src_card = cards.get(e["card"])
        arrow = f'{left_id} --{e["rel"]}--> {right_id}'
        print(f"◆ {arrow}")
        print(f"  左: {fa.get('name', '?')} ｜ {fa.get('type', '')} ｜ {fa.get('block', '')}")
        print(f"  右: {fb.get('name', '?')} ｜ {fb.get('type', '')} ｜ {fb.get('block', '')}")
        if src_card:
            hits = _source_lines(cfg, src_card[1], [fa.get("name", ""), fb.get("name", "")])
            if hits:
                print("  源文出现处:")
                print("\n".join(hits))
        print(f"  → knowledger approve {e['card']} {e['rel']} {e['to'] or e['from']} ok|wrong --by <你> [备注]\n")
    print("审完 → `knowledger check`（关 6：active 实例的边未经审则不过）→ `knowledger build`")
    return 0


def _review_defs(cfg: Config, block: str | None) -> int:
    pending = []
    for p, f, body in iter_cards(cfg, include_staging=False):
        if block and f.get("block") != block:
            continue
        d = _definition(body)
        if not d or d.startswith("（一句话") or "待确认" in d or f.get("status") == "draft":
            pending.append((p, f, d))
    print(f"定义待审：{len(pending)} 张\n")
    for p, f, d in pending:
        print(f"◆ {f.get('id')} {f.get('name')} ｜ {f.get('block')} ｜ status={f.get('status')}")
        print(f"  定义: {d[:140]}{'…' if len(d) > 140 else ''}")
        print(f"  → 编辑 {p.relative_to(cfg.root)} 的 ## 定义 段；对照 ## 来源 里的 raw/ 文件\n")
    return 0


def _review_tree(cfg: Config, block: str | None) -> int:
    """contains edges → tree. Reassigning a parent = editing the child's contains.from."""
    cards = _by_id(cfg)
    children: dict[str, list[str]] = {}
    has_parent = set()
    for e in _edges(cfg):
        if e["rel"] != "contains":
            continue
        parent, child = e["from"], e["card"]   # child card declares `contains from: parent`
        if parent and child:
            children.setdefault(parent, []).append(child)
            has_parent.add(child)
    roots = [cid for cid in cards if cid not in has_parent and cards[cid][0].get("type") in cfg.types
             and (not block or cards[cid][0].get("block") == block)]
    roots.sort(key=lambda c: (cards[c][0].get("block", ""), c))

    def walk(cid: str, depth: int):
        f = cards[cid][0]
        rv = sum(1 for e in _edges(cfg) if e["card"] == cid and e["rel"] == "contains"
                 and e["review"].get("verdict") in ("ok", "wrong"))
        mark = "" if rv or cid in roots else "  ⏳ contains 未经审"
        print("  " * depth + f"{f.get('name', cid)}  ({f.get('type')}, {cid}){mark}")
        for ch in sorted(children.get(cid, []), key=lambda c: cards[c][0].get("name", "")):
            walk(ch, depth + 1)

    print("层级树（contains 边）\n")
    for r in roots:
        walk(r, 0)
    print("\n换父节点 = 改子卡 relates_to 里那条 contains 的 from；改完 `knowledger approve` 这条边")
    return 0


def open_html(cfg: Config) -> int:
    import webbrowser
    p = cfg.build / "graph.html"
    if not p.exists():
        print("先 `knowledger build`")
        return 1
    print(f"{p}\n  图是看的，改是 CLI：选中节点 → 右侧「交给 Agent」→ 复制 → 贴给 WorkBuddy / 豆包")
    webbrowser.open(p.resolve().as_uri())
    return 0


def mermaid_cmd(cfg: Config) -> int:
    p = cfg.build / "graph.mmd"
    if not p.exists():
        print("先 `knowledger build`")
        return 1
    print(p.read_text(encoding="utf-8"))
    return 0


class ReviewDenied(Exception):
    """审核人不是该块 owner —— 关 6 的权限面。"""


def approve(cfg: Config, card: str, rel: str, other: str, verdict: str, note: str, by: str) -> int:
    if verdict not in ("ok", "wrong"):
        print("✗ verdict 只能是 ok 或 wrong")
        return 1
    for p, f, body in iter_cards(cfg, include_staging=True):
        if f.get("id") != card:
            continue
        # 审核权限 = 该块的写权限（和关 3 同一张表）。Agent 不能自编 by 绕过关 6。
        blk = cfg.blocks.get(f.get("block", ""))
        owners = (blk.owners if blk else None) or []
        if by not in owners:
            raise ReviewDenied(f"{by} 不是 {f.get('block')} 块的 owner，无权审边（该块 owners: {owners}）。by 必须是真人口述的 owner 名字。")
        rels = f.get("relates_to", []) or []
        hit = False
        for r in rels:
            if not isinstance(r, dict) or r.get("rel") != rel:
                continue
            if (r.get("to") or r.get("from")) == other:
                r["review"] = verdict
                r["review_by"] = by
                r["review_at"] = datetime.now().isoformat(timespec="seconds")
                if note:
                    r["review_note"] = note
                hit = True
        if not hit:
            print(f"✗ {card} 上没有边 {rel} → {other}")
            return 1
        p.write_text(fm.dump(f, body), encoding="utf-8")
        print(f"✓ {card} --{rel}--> {other}  {verdict}" + (f"  （{note}）" if note else ""))
        return 0
    print(f"✗ 找不到卡 {card}")
    return 1


# ---------- claim ----------
def claim(cfg: Config, card: str, block: str | None, owner: str | None, by: str) -> int:
    """认领一张 staging 卡：写 owner（可纠正 block），文件归位 blocks/<块>/。status 保持 draft。
    权限与关 3 / approve 同一张表：by 必须是目标块的 owner。没人认的卡进不了编译产物。"""
    if not cfg.staging.exists():
        print("staging/ 为空，先 `knowledger cards`")
        return 1
    for p in sorted(cfg.staging.glob("*.md")):
        f, body = fm.read_card(p)
        if f.get("id") != card:
            continue
        blk = block or f.get("block", "")
        if blk not in cfg.blocks:
            print(f"✗ 未定义的块: {blk}（可用：{', '.join(cfg.blocks)}）")
            return 1
        owners = cfg.blocks[blk].owners or []
        if not owners:
            print(f"✗ {blk} 块还没有 owners（knowledger.yaml 里是 null）——先定责任人再认领")
            return 1
        if by not in owners:
            raise ReviewDenied(f"{by} 不是 {blk} 块的 owner，无权认领（该块 owners: {owners}）。by 必须是真人口述的 owner 名字。")
        owner = owner or by
        if owner not in owners:
            print(f"✗ owner {owner} 不在 {blk} 块的 owners 里: {owners}")
            return 1
        f["owner"], f["block"] = owner, blk
        dst = cfg.blocks_dir / blk / str(f.get("type") or "doc").lower() / f"{card}.md"
        if dst.exists():
            print(f"✗ 已存在: {dst.relative_to(cfg.root)}")
            return 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(fm.dump(f, body), encoding="utf-8")
        p.unlink()
        print(f"✓ {card} 认领：owner {owner} @ {blk} → {dst.relative_to(cfg.root)}（status 仍为 draft）")
        print("  下一步：补 ## 定义（knowledger set-definition / MCP knowledger_set_definition）→ 要建关系先 review edges → knowledger check")
        return 0
    print(f"✗ staging/ 里找不到卡 {card}")
    return 1
