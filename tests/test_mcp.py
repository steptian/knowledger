"""MCP server over real stdio. Skipped if mcp SDK isn't installed. Run: python -m pytest tests/ -q"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


async def _session(cwd: Path):
    params = StdioServerParameters(command=sys.executable, args=["-m", "knowledger.cli", "serve"],
                                   env={"PYTHONIOENCODING": "utf-8", "PATH": os.environ.get("PATH", "")}, cwd=str(cwd))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            yield s


async def _call(s, name, **kw):
    res = await s.call_tool(name, kw)
    return json.loads(res.content[0].text)


@pytest.fixture()
def kb(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    subprocess.run([sys.executable, "-m", "knowledger.cli", "init", str(root), "--name", "t"], cwd=tmp_path, check=True, capture_output=True)
    # a card with an unreviewed edge, active, owned by 张三 (product owner in the template)
    doc = root / "blocks" / "product" / "doc" / "D-1.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("---\nid: D-1\ntype: Doc\nname: 文档\nowner: 张三\nblock: product\nstatus: active\nversion: 1\nvalid_from: 2026-01-01\nsources:\n  - doc: D-1\n    version: 1\nrelates_to:\n  - rel: contains\n    from: EXAMPLE-001\n---\n\n## 定义\n测试文档\n", encoding="utf-8")
    return root


def test_mcp_tools_list_and_review_loop(kb: Path):
    import asyncio

    async def run():
        async for s in _session(kb):
            tools = {t.name for t in (await s.list_tools()).tools}
            assert {"knowledger_status", "knowledger_review_edges", "knowledger_approve", "knowledger_check", "knowledger_build", "knowledger_recall"} <= tools
            st = await _call(s, "knowledger_status")
            assert st["ok"] and st["blocks"]["product"]["owners"] == ["张三"] and st["edges_pending_review"] >= 1
            edges = await _call(s, "knowledger_review_edges")
            assert edges["ok"] and any("contains" in e["edge"] for e in edges["edges"])
            # gate 6 + ownership: a non-owner (agent-invented name) is refused with a hint
            denied = await _call(s, "knowledger_approve", card="D-1", rel="contains", other="EXAMPLE-001", verdict="ok", by="Agent")
            assert not denied["ok"] and "owners" in denied["error"] and "hint" in denied
            chk = await _call(s, "knowledger_check"); assert not chk["passed"], "unreviewed edge on active card must fail gate 6"
            # build must not be a way around gate 6 either: artefacts written, passed=false
            b0 = await _call(s, "knowledger_build"); assert not b0["passed"] and "关6" in b0["report"] and (kb / "build" / "graph.json").exists()
            ok = await _call(s, "knowledger_approve", card="D-1", rel="contains", other="EXAMPLE-001", verdict="ok", by="张三", note="归属对")
            assert ok["ok"] and ok["by"] == "张三"
            chk = await _call(s, "knowledger_check"); assert chk["passed"]
            b = await _call(s, "knowledger_build"); assert b["passed"] and b["snapshot"].endswith("SNAPSHOT.md")
            rc = await _call(s, "knowledger_recall", query="文档")
            assert rc["hits"] and rc["hits"][0]["id"] == "D-1"
    asyncio.run(run())



def test_mcp_claim(kb: Path):
    import asyncio

    (kb / "raw" / "misc").mkdir(parents=True)
    (kb / "raw" / "misc" / "note.md").write_text("# 笔记\n", encoding="utf-8")
    subprocess.run([sys.executable, "-m", "knowledger.cli", "cards"], cwd=kb, check=True, capture_output=True)
    cid = next((kb / "staging").glob("*.md")).stem

    async def run():
        async for s in _session(kb):
            # agent 自编名 → 拒绝 + hint
            denied = await _call(s, "knowledger_claim", card=cid, by="Agent")
            assert not denied["ok"] and "owners" in denied["error"] and "hint" in denied
            # owner 认领 → 文件归位，check 通过
            ok = await _call(s, "knowledger_claim", card=cid, by="张三")
            assert ok["ok"] and "认领" in ok["report"]
            assert (kb / "blocks" / "product" / "doc" / f"{cid}.md").exists()
            await _call(s, "knowledger_approve", card="D-1", rel="contains", other="EXAMPLE-001", verdict="ok", by="张三")  # fixture 卡自带的未审边，与本测试无关
            chk = await _call(s, "knowledger_check"); assert chk["passed"]

    asyncio.run(run())
