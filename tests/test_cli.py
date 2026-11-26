"""End-to-end smoke test. Run: python -m pytest tests/ -q   or   python tests/test_cli.py"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

API_DOC = """# 订单模块

### 1.1 订单创建

| 方法 | 路由 | 说明 |
|---|---|---|
| POST | `/orders` | 创建订单 |
| GET | `/orders/{id}` | 查询订单 |

## 概述
结构章节，应被跳过。
"""


def run(*args, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "knowledger.cli", *args], cwd=cwd, capture_output=True, text=True)


@pytest.fixture()
def kb(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    (tmp_path / "docs" / "engineering").mkdir(parents=True)
    (tmp_path / "docs" / "engineering" / "api.md").write_text(API_DOC, encoding="utf-8")
    r = run("init", str(root), "--name", "t", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    return root


def test_full_chain(kb: Path):
    # selftest catches the deliberately-broken example that init ships
    r = run("selftest", cwd=kb); assert r.returncode == 0, r.stdout + r.stderr
    assert "正确拦住" in r.stdout

    # scan + cards: path 'engineering' → block engineering; '## 概述' skipped as noise
    run("scan", str(kb.parent / "docs" / "engineering"), cwd=kb)
    r = run("cards", cwd=kb); assert r.returncode == 0, r.stderr
    assert "候选条目 3 条" in r.stdout  # 订单创建 + 2 API rows; 概述 excluded
    cards = list((kb / "staging").glob("*.md"))
    assert len(cards) == 1
    assert "block: engineering" in cards[0].read_text()

    # unreviewed card must NOT compile
    r = run("build", cwd=kb); assert r.returncode == 0, r.stdout
    onto = json.loads((kb / "build" / "ontology.json").read_text())
    assert all(e["id"] != cards[0].stem for e in onto["entities"]), "unreviewed staging card leaked into build"

    # human review: fill owner, activate, move into blocks/
    t = cards[0].read_text().replace("owner: 待审", "owner: 李四").replace("status: draft", "status: active")
    dst = kb / "blocks" / "engineering" / "doc" / cards[0].name
    dst.parent.mkdir(parents=True)
    dst.write_text(t); cards[0].unlink()

    r = run("check", cwd=kb); assert r.returncode == 0, r.stdout  # gate 1 + 3 pass
    r = run("build", cwd=kb); assert r.returncode == 0, r.stdout
    assert (kb / "build" / "SNAPSHOT.md").exists()
    assert (kb / "build" / "rewrite.json").exists()
    r = run("verify", cwd=kb); assert r.returncode == 0, r.stdout  # three compile gates

    # recall: rewrite + candidate titles are searchable
    r = run("recall", "怎么查询订单", cwd=kb); assert r.returncode == 0, r.stderr
    hits = json.loads(r.stdout)["hits"]
    assert hits and "查询订单" in hits[0]["candidates"]


def test_gate3_rejects_outsider(kb: Path):
    r = run("add", "--type", "Module", "--id", "M-1", "--name", "x", "--owner", "陌生人", "--block", "engineering", cwd=kb)
    assert r.returncode == 1 and "关3" in r.stdout


def test_gate4_archives_deprecated(kb: Path):
    ex = next((kb / "blocks").rglob("EXAMPLE-001.md"))
    ex.write_text(ex.read_text().replace("status: active", "status: deprecated"))
    r = run("snapshot", cwd=kb); assert r.returncode == 0, r.stdout
    assert list((kb / "snapshot" / "_archive").rglob("EXAMPLE-001.md"))
    assert not [p for p in (kb / "snapshot").rglob("EXAMPLE-001.md") if "_archive" not in p.parts]


def test_gate2_drift_detects_change(kb: Path, tmp_path: Path):
    src = tmp_path / "docs" / "engineering" / "api.md"
    run("scan", str(src.parent), cwd=kb); run("cards", cwd=kb)
    r = run("drift", cwd=kb); assert "变化 0" in r.stdout
    src.write_text(src.read_text() + "\nx")
    run("scan", str(src.parent), cwd=kb)
    r = run("drift", cwd=kb); assert "变化 1" in r.stdout and "待重审" in r.stdout


def test_no_config_is_a_clean_error(tmp_path: Path):
    r = run("check", cwd=tmp_path)
    assert r.returncode == 2 and "knowledger init" in r.stderr


def test_config_cross_check(tmp_path: Path):
    run("init", str(tmp_path / "k"), cwd=tmp_path)
    cfg = tmp_path / "k" / "knowledger.yaml"
    cfg.write_text(cfg.read_text().replace("Module:  { block: engineering", "Module:  { block: nope"), encoding="utf-8")
    r = run("check", cwd=tmp_path / "k")
    assert r.returncode == 2 and "未定义的 block" in r.stderr


def test_gate6_unreviewed_edge_blocks_and_wrong_edge_drops(kb: Path):
    """The ontology-review loop: unreviewed edge → gate 6 fails; approve ok → passes;
    approve wrong → edge excluded from build artefacts."""
    ex = next((kb / "blocks").rglob("EXAMPLE-001.md"))
    doc = kb / "blocks" / "engineering" / "doc" / "D-X.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(ex.read_text().replace("id: EXAMPLE-001", "id: D-X").replace("block: product", "block: engineering")
                   .replace("owner: 张三", "owner: 李四").replace("type: Feature", "type: Doc")
                   .replace("valid_from: 2026-01-01", "valid_from: 2026-01-01\nversion: 1")
                   .replace("relates_to: []", "relates_to:\n  - rel: contains\n    from: EXAMPLE-001"), encoding="utf-8")
    # 1) unreviewed edge on an active card → gate 6
    r = run("check", cwd=kb); assert r.returncode == 1 and "关6" in r.stdout
    # 2) review edges lists it with both ends
    r = run("review", "edges", cwd=kb); assert "EXAMPLE-001 --contains--> D-X" in r.stdout
    # 3) approve ok → passes, provenance written to the card
    r = run("approve", "D-X", "contains", "EXAMPLE-001", "ok", "--by", "李四", cwd=kb)
    assert r.returncode == 0, r.stdout
    assert "review_by: 李四" in doc.read_text()
    assert run("check", cwd=kb).returncode == 0
    run("build", cwd=kb)
    assert (kb / "build" / "graph.json").exists() and (kb / "build" / "graph.mmd").exists()
    assert "D-X -->|contains| EXAMPLE-001" in run("graph", cwd=kb).stdout
    # 4) flip to wrong → edge dropped from artefacts
    run("approve", "D-X", "contains", "EXAMPLE-001", "wrong", "--by", "李四", "方向反了", cwd=kb)
    run("build", cwd=kb)
    assert "D-X -->" not in run("graph", cwd=kb).stdout
    # 5) tree shows the reviewed contains edge when it's ok again
    run("approve", "D-X", "contains", "EXAMPLE-001", "ok", "--by", "李四", cwd=kb)
    r = run("review", "tree", cwd=kb); assert "D-X" in r.stdout


def test_graph_html_is_self_contained_and_agent_ready(kb: Path):
    run("build", cwd=kb)
    html = (kb / "build" / "graph.html").read_text(encoding="utf-8")
    assert "<script" in html and "src=" not in html.split("<script")[1].split(">")[0]  # no external script
    assert "交给 Agent" in html and "knowledger approve" in html                            # agent handoff block exists
    g = json.loads((kb / "build" / "graph.json").read_text())
    assert g["nodes"] and all("definition" in n and "card" in n for n in g["nodes"])
    assert all(e["card"] for e in g["edges"])                                          # approve target is the card id


def test_cards_keeps_nested_readme(kb: Path, tmp_path: Path):
    """raw/README.md 是归档区自述，跳过；子目录里的 README.md 是正经文档，必须建卡。"""
    pkg = tmp_path / "docs" / "pkg"
    pkg.mkdir()
    (pkg / "README.md").write_text(API_DOC, encoding="utf-8")
    run("scan", str(pkg), cwd=kb)
    r = run("cards", cwd=kb)
    assert r.returncode == 0 and "1 张" in r.stdout, r.stdout + r.stderr
    cards = list((kb / "staging").glob("*.md"))
    assert len(cards) == 1 and "name: README.md" in cards[0].read_text(encoding="utf-8")


def test_build_fails_on_unreviewed_edge_but_writes_graph(kb: Path):
    """关 6：build 与 check 读同一张表——未审边 → 非零退出并点名；产物照常写出（图要能看待审边）。"""
    ex = next((kb / "blocks").rglob("EXAMPLE-001.md"))
    doc = kb / "blocks" / "engineering" / "doc" / "D-X.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(ex.read_text().replace("id: EXAMPLE-001", "id: D-X").replace("block: product", "block: engineering")
                   .replace("owner: 张三", "owner: 李四").replace("type: Feature", "type: Doc")
                   .replace("valid_from: 2026-01-01", "valid_from: 2026-01-01\nversion: 1")
                   .replace("relates_to: []", "relates_to:\n  - rel: contains\n    from: EXAMPLE-001"), encoding="utf-8")
    r = run("build", cwd=kb)
    assert r.returncode == 1 and "关6" in r.stdout, r.stdout
    assert "D-X --contains--> EXAMPLE-001" in r.stdout
    assert (kb / "build" / "graph.json").exists()
    run("approve", "D-X", "contains", "EXAMPLE-001", "ok", "--by", "李四", cwd=kb)
    assert run("build", cwd=kb).returncode == 0


def test_graph_lists_unclaimed_cards(kb: Path):
    """待认领的 staging 卡不进 nodes / AI 产物，但进 graph.json 的 unclaimed——页面才知道真实待办。"""
    run("scan", str(kb.parent / "docs" / "engineering"), cwd=kb)
    (kb / "raw" / "misc").mkdir()
    (kb / "raw" / "misc" / "spec.pdf").write_bytes(b"%PDF-1.4")
    run("cards", cwd=kb)
    assert run("build", cwd=kb).returncode == 0
    g = json.loads((kb / "build" / "graph.json").read_text(encoding="utf-8"))
    unclaimed = g["unclaimed"]
    assert {u["name"] for u in unclaimed} == {"api.md", "spec.pdf"}
    assert [u["name"] for u in unclaimed if u["needs_ocr"]] == ["spec.pdf"]
    assert {u["src"] for u in unclaimed} == {"raw/engineering/api.md", "raw/misc/spec.pdf"}
    onto = json.loads((kb / "build" / "ontology.json").read_text())
    assert not {u["id"] for u in unclaimed} & {e["id"] for e in onto["entities"]}, "unclaimed card leaked into AI artefacts"
    assert "待认领" in (kb / "build" / "graph.html").read_text(encoding="utf-8")


def test_claim_moves_staging_card_to_block(kb: Path):
    """认领 = 第 0 步审核：非 owner 拒绝；owner 认领后文件归位、check 通过。"""
    run("scan", str(kb.parent / "docs" / "engineering"), cwd=kb)
    run("cards", cwd=kb)
    card = next((kb / "staging").glob("*.md"))
    cid = card.stem
    # 非该块 owner（张三只有 product 写权）→ 拒绝并给出 owners 提示
    r = run("claim", cid, "--by", "张三", cwd=kb)
    assert r.returncode == 2 and "owners" in r.stderr and card.exists()
    # 该块 owner 认领 → 文件从 staging/ 归位 blocks/<块>/doc/
    r = run("claim", cid, "--by", "李四", cwd=kb)
    assert r.returncode == 0, r.stdout + r.stderr
    dst = kb / "blocks" / "engineering" / "doc" / f"{cid}.md"
    assert dst.exists() and not card.exists()
    t = dst.read_text(encoding="utf-8")
    assert "owner: 李四" in t and "block: engineering" in t and "status: draft" in t
    assert run("check", cwd=kb).returncode == 0
    # 认领过的卡进编译产物；重复认领报找不到
    run("build", cwd=kb)
    onto = json.loads((kb / "build" / "ontology.json").read_text())
    assert cid in {e["id"] for e in onto["entities"]}
    assert run("claim", cid, "--by", "李四", cwd=kb).returncode == 1
