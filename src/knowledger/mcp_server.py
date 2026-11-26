"""mcp_server.py — knowledger as an MCP server (stdio).

Optional: `pip install knowledger[mcp]`. Not part of the zero-dependency core.

Design: tools mirror the CLI one-to-one so an agent that learns the CLI learns the MCP.
Read tools are safe; write tools (approve / move / add) record who did it — the agent
must pass `by` (the human's name it is acting for). Gate 6 and gate 3 still apply.

Run from any directory inside a kb (walks up like the CLI):
    knowledger serve                       # stdio; add to a client's mcpServers config
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP          # mcp SDK 1.x（pyproject 已锁 <2）
except ImportError:  # pragma: no cover
    sys.stderr.write("mcp SDK 不可用或版本不兼容：需要 1.x。装：pip install 'knowledger[mcp]'；若装了 2.x：pip install 'mcp<2'\n")
    raise SystemExit(2)

from . import compile as C
from . import core, ingest, review
from .config import Config, ConfigError, load as load_cfg

mcp = FastMCP("knowledger")

_cfg: Config | None = None


def cfg() -> Config:
    """Config is resolved once from the server's cwd (the kb directory the client launched it in)."""
    global _cfg
    if _cfg is None:
        _cfg = load_cfg()
    return _cfg


def _ok(**kw) -> str:
    return json.dumps({"ok": True, **kw}, ensure_ascii=False)


def _err(msg: str, **kw) -> str:
    return json.dumps({"ok": False, "error": msg, **kw}, ensure_ascii=False)


# ---------------- read ----------------
@mcp.tool()
def knowledger_status() -> str:
    """知识库概况：块、owner、卡数、待审边数。先调这个了解结构再调别的。"""
    c = cfg()
    cards = ingest.iter_cards(c, include_staging=False)
    staging = list(c.staging.glob("*.md")) if c.staging.exists() else []
    edges = review._edges(c)
    pending = [e for e in edges if e["review"].get("verdict") not in ("ok", "wrong")]
    return _ok(name=c.name, root=str(c.root),
               blocks={b: {"owners": s.owners, "default_type": s.default_type} for b, s in c.blocks.items()},
               types={t: s.block for t, s in c.types.items()}, relations=c.relations,
               instances=len(cards), staging_cards=len(staging), edges=len(edges), edges_pending_review=len(pending))


@mcp.tool()
def knowledger_recall(query: str, limit: int = 8) -> str:
    """按查询召回相关实例（含候选条目和一跳邻居）。返回的是材料，不是规则。

    Args:
        query: 自然语言问题或关键词；别名会自动归一到标准术语。
        limit: 最多返回几条，默认 8。
    """
    c = cfg()
    p = c.build / "ontology.json"
    onto = C._read_json(p, c.root) if p.exists() else C.extract(c)
    return json.dumps(C.recall(onto, query, limit), ensure_ascii=False)


@mcp.tool()
def knowledger_get(card_id: str) -> str:
    """读一张卡的完整内容：frontmatter、定义、候选条目、关系。

    Args:
        card_id: 如 SF-001 或 D-5BEC93DE。
    """
    c = cfg()
    for p, f, body in ingest.iter_cards(c, include_staging=True):
        if f.get("id") == card_id:
            return _ok(path=str(p.relative_to(c.root)), frontmatter=f, body=body)
    return _err(f"找不到卡 {card_id}")


@mcp.tool()
def knowledger_review_edges(block: str | None = None, only_pending: bool = True) -> str:
    """列出关系边及审核证据。每条边带两端定义和源文档里名字出现的行——用来向人确认「这条关系对不对」。

    Args:
        block: 只看某个块；None 看全部。
        only_pending: True 只返回未经人审的边。
    """
    c = cfg()
    cards = review._by_id(c)
    edges = review._edges(c)
    if block:
        edges = [e for e in edges if e["block"] == block]
    if only_pending:
        edges = [e for e in edges if e["review"].get("verdict") not in ("ok", "wrong")]
    out = []
    for e in edges:
        left, right = (e["from"], e["card"]) if e["from"] else (e["card"], e["to"])
        lf, rf = cards.get(left, ({}, ""))[0], cards.get(right, ({}, ""))[0]
        hits = review._source_lines(c, cards.get(e["card"], ({}, ""))[1], [lf.get("name", ""), rf.get("name", "")])
        out.append({"edge": f"{left} --{e['rel']}--> {right}", "card": e["card"], "rel": e["rel"],
                    "other": e["to"] or e["from"], "review": e["review"],
                    "left": {"id": left, "name": lf.get("name"), "type": lf.get("type"), "definition": review._definition(cards.get(left, ("", ""))[1])[:200]},
                    "right": {"id": right, "name": rf.get("name"), "type": rf.get("type"), "definition": review._definition(cards.get(right, ("", ""))[1])[:200]},
                    "source_lines": [h.strip() for h in hits]})
    return _ok(count=len(out), edges=out,
               next="对每条边问人「对 / 错 / 错在哪端」，然后用 knowledger_approve 记录 verdict")


@mcp.tool()
def knowledger_tree(block: str | None = None) -> str:
    """contains 边构成的层级树（大功能 → 小功能 → 场景）。用于确认层级关系。

    Args:
        block: 只看某个块；None 看全部。
    """
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        review.run(cfg(), "tree", block)
    return _ok(tree=buf.getvalue())


@mcp.tool()
def knowledger_check() -> str:
    """跑机械关 1/3/6（结构、块归属、边已审）。返回不通过的卡和原因。写操作后必须调一次。"""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = core.check(cfg())
    return _ok(passed=(rc == 0), report=buf.getvalue())


@mcp.tool()
def knowledger_drift() -> str:
    """哪些源文档在上次建卡后变了 → 需要重审。"""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        C.drift(cfg())
    return _ok(report=buf.getvalue())


# ---------------- write (agent acts for a named human) ----------------
@mcp.tool()
def knowledger_approve(card: str, rel: str, other: str, verdict: str, by: str, note: str = "") -> str:
    """人审一条关系边。这是「关系正确性」从口头确认变成数据的唯一方式（关 6）。

    Args:
        card: 声明这条边的卡 id，如 SF-001。
        rel: 关系名，须在 knowledger.yaml 的 relations 里，如 contains。
        other: 边另一端的卡 id。
        verdict: ok 或 wrong。wrong 的边不会进编译产物。
        by: 审核人姓名——必须是该块 owner 之一（knowledger_status 能查到），且是真人口述的。非 owner 会被拒绝。
        note: 可选备注，如「方向反了」。
    """
    try:
        rc = review.approve(cfg(), card, rel, other, verdict, note, by)
    except review.ReviewDenied as e:
        return _err(str(e), hint="by 必须是该块 owner；用 knowledger_status 查 owners，然后向那位真人确认后再调")
    except (ConfigError, core.GateError) as e:
        return _err(str(e))
    return _ok(card=card, edge=f"{card} --{rel}--> {other}", verdict=verdict, by=by) if rc == 0 else _err("approve 失败，看参数")

@mcp.tool()
def knowledger_claim(card: str, block: str | None = None, owner: str | None = None, by: str = "") -> str:
    """认领一张 staging 卡：写 owner（可纠正归属块），文件从 staging/ 归位到 blocks/<块>/，status 保持 draft。
    没人认的卡进不了编译产物——认领是审核的第 0 步。

    Args:
        card: staging 卡 id（D- 开头；knowledger_review_cards / 页面待办总览可查）。
        block: 归属块；不传则用卡上机器推断的块。人纠正时传新块名。
        owner: 责任人，默认等同 by。
        by: 操作人姓名——必须是该块 owner 之一（knowledger_status 能查到），且是真人口述的。非 owner 会被拒绝。
    """
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = review.claim(cfg(), card, block, owner, by)
    except review.ReviewDenied as e:
        return _err(str(e), hint="by 必须是该块 owner；用 knowledger_status 查 owners，然后向那位真人确认后再调")
    except (ConfigError, core.GateError) as e:
        return _err(str(e))
    return _ok(card=card, report=buf.getvalue()) if rc == 0 else _err(buf.getvalue().strip())

@mcp.tool()
def knowledger_move(card: str, new_parent: str, by: str) -> str:
    """改层级：把一张卡的 contains 边指向新的父节点（旧边标 wrong，新边待审）。

    Args:
        card: 子卡 id。
        new_parent: 新父卡 id。
        by: 操作人姓名。
    """
    c = cfg()
    for p, f, body in ingest.iter_cards(c, include_staging=True):
        if f.get("id") != card:
            continue
        rels = f.get("relates_to", []) or []
        old = [r for r in rels if isinstance(r, dict) and r.get("rel") == "contains"]
        for r in old:
            r["review"], r["review_by"] = "wrong", by
        rels.append({"rel": "contains", "from": new_parent, "review_by": by, "review_note": f"moved from {[r.get('from') for r in old]}"})
        f["relates_to"] = rels
        from . import fm
        p.write_text(fm.dump(f, body), encoding="utf-8")
        return _ok(card=card, new_parent=new_parent, next=f"knowledger_approve(card='{card}', rel='contains', other='{new_parent}', verdict='ok', by='{by}')")
    return _err(f"找不到卡 {card}")


@mcp.tool()
def knowledger_set_definition(card: str, definition: str, by: str) -> str:
    """写或改一张卡的「## 定义」段。定义是给人读的一句话，不是规则。

    Args:
        card: 卡 id。
        definition: 新定义文本（一句话）。
        by: 操作人姓名，写进 frontmatter 的 definition_by。
    """
    import re
    from . import fm
    c = cfg()
    for p, f, body in ingest.iter_cards(c, include_staging=True):
        if f.get("id") != card:
            continue
        if "## 定义" in body:
            body = re.sub(r"(## 定义\s*\n)(.*?)(?=\n## |\Z)", lambda m: m.group(1) + definition + "\n", body, count=1, flags=re.S)
        else:
            body = body.rstrip("\n") + f"\n\n## 定义\n{definition}\n"
        f["definition_by"] = by
        p.write_text(fm.dump(f, body), encoding="utf-8")
        return _ok(card=card, definition=definition)
    return _err(f"找不到卡 {card}")


@mcp.tool()
def knowledger_add_card(type: str, id: str, name: str, block: str, owner: str, doc: str | None = None, by: str | None = None) -> str:
    """新建一条实例骨架（status=draft）。owner 必须对该块有写权（关 3）。

    Args:
        type: 类型名，须在 knowledger.yaml 的 types 里。
        id: 实例 id，如 SF-002。
        name: 名称。
        block: 所属块。
        owner: 责任人（需在该块 owners 里）。
        doc: 可选，来源 Doc 的 id。
        by: 等同 owner，可省。
    """
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = core.add(cfg(), type_=type, id_=id, name=name, owner=owner, block=block, doc=doc, ver="1")
    return _ok(card=id, path=buf.getvalue().strip()) if rc == 0 else _err(buf.getvalue().strip())


@mcp.tool()
def knowledger_build() -> str:
    """编译：本体产物 + AI 快照 + 关系图。关 1/3/6 未过则 passed=false（产物照常写出），按 report 处理后重试。返回统计和问题列表。"""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = C.build(cfg())
    return _ok(passed=(rc == 0), report=buf.getvalue(), snapshot=str(cfg().build / "SNAPSHOT.md"))


def serve() -> int:
    """Entry point for `knowledger serve`. Resolves config lazily so the server starts fast."""
    try:
        load_cfg()
    except ConfigError as e:
        sys.stderr.write(f"✗ {e}\n")
        return 2
    mcp.run(transport="stdio")
    return 0
