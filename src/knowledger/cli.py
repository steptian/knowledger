"""cli.py — one verb per step. Run `knowledger` from anywhere inside a kb directory (walks up like git)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from . import compile as C
from . import core, ingest, review
from .config import ConfigError, load as load_cfg

TEMPLATES = Path(__file__).parent / "templates"


def _init(args) -> int:
    dest = Path(args.dir)
    if (dest / "knowledger.yaml").exists() and not args.force:
        print(f"✗ {dest}/knowledger.yaml 已存在（--force 覆盖配置，不碰数据）")
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    for sub in ("raw", "staging", "blocks", "ontology", "eval"):
        (dest / sub).mkdir(exist_ok=True)
    tpl = (TEMPLATES / "knowledger.yaml").read_text(encoding="utf-8")
    (dest / "knowledger.yaml").write_text(tpl.replace("__NAME__", args.name or dest.name), encoding="utf-8")
    for name in ("glossary.csv", "taxonomy.md", "gitignore"):
        out = dest / (".gitignore" if name == "gitignore" else f"ontology/{name}")
        if not out.exists():
            out.write_text((TEMPLATES / name).read_text(encoding="utf-8"), encoding="utf-8")
    (dest / "raw" / "README.md").write_text(
        "# raw/ · 只读归档\n\n`knowledger scan` 把文档**原样**放这里：零改名、零改结构。此后 raw 只增不改。\n", encoding="utf-8")
    # one good example + one deliberately broken, so `knowledger selftest` has teeth on day one.
    # first block = first declared in the template (product), and its owner (张三) — both in the template.
    first_block, first_owner = "product", "张三"
    ex = dest / "blocks" / first_block / "examples"
    ex.mkdir(parents=True, exist_ok=True)
    (ex / "EXAMPLE-001.md").write_text(
        (TEMPLATES / "example.md").read_text(encoding="utf-8").replace("__FIRST_BLOCK__", first_block), encoding="utf-8")
    (ex / "_bad-001.md").write_text(
        (TEMPLATES / "example_bad.md").read_text(encoding="utf-8").replace("__FIRST_BLOCK__", first_block), encoding="utf-8")
    print(f"init → {dest}/\n"
          f"  knowledger.yaml      ← 唯一配置：改名字 / 块 / owners / 类型\n"
          f"  ontology/       ← glossary.csv 术语表（3 行示例）、taxonomy.md\n"
          f"  blocks/         ← 一条好样例 + 一条故意违规（selftest 用）\n\n"
          f"下一步：编辑 knowledger.yaml 填你的块和 owner → `knowledger scan <文档目录>` → `knowledger cards`")
    return 0


def _approve(a) -> int:
    return review.approve(load_cfg(), a.card, a.rel, a.other, a.verdict, a.note, a.by)


def _serve(_) -> int:
    try:
        from .mcp_server import serve
    except ImportError:
        print("✗ 需要 MCP SDK：pip install 'knowledger[mcp]'", file=sys.stderr)
        return 2
    return serve()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="knowledger", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"knowledger {__version__}")
    sp = ap.add_subparsers(dest="cmd", required=True)

    i = sp.add_parser("init", help="在工作目录生成骨架（空模板）")
    i.add_argument("dir", nargs="?", default=".")
    i.add_argument("--name", help="知识库名字（默认目录名）")
    i.add_argument("--force", action="store_true", help="覆盖 knowledger.yaml")
    i.set_defaults(fn=_init)

    s = sp.add_parser("scan", help="文档原样进 raw/")
    s.add_argument("src", nargs="+")
    s.set_defaults(fn=lambda a: ingest.scan(load_cfg(), a.src))

    sp.add_parser("cards", help="为 raw/ 每份文档建索引卡 → staging/").set_defaults(
        fn=lambda a: ingest.cards(load_cfg()))

    sp.add_parser("check", help="关 1 结构 + 关 3 块归属 + 关 6 边已人审").set_defaults(fn=lambda a: core.check(load_cfg()))
    l = sp.add_parser("lint", help="关 2：术语覆盖率 + 待归一队列")
    l.add_argument("--upstream", help="上游目录（藏经阁同步检测）")
    l.set_defaults(fn=lambda a: core.lint(load_cfg(), a.upstream))
    sp.add_parser("snapshot", help="关 4：deprecated 物理移出快照").set_defaults(fn=lambda a: core.snapshot(load_cfg()))
    sp.add_parser("selftest", help="故意违规样例必须被拦").set_defaults(fn=lambda a: core.selftest(load_cfg()))

    a = sp.add_parser("add", help="新建一条实例骨架（不手写 frontmatter 也行）")
    for k in ("--type", "--id", "--name", "--owner", "--block"):
        a.add_argument(k, required=True)
    a.add_argument("--doc")
    a.add_argument("--ver", default="1")
    a.set_defaults(fn=lambda x: core.add(load_cfg(), type_=x.type, id_=x.id, name=x.name, owner=x.owner,
                                         block=x.block, doc=x.doc, ver=x.ver))

    sp.add_parser("build", help="编译：本体产物 + AI 快照 + rewrite 词典 → build/").set_defaults(
        fn=lambda x: C.build(load_cfg()))
    sp.add_parser("verify", help="编译三道关：格式 / 指纹 / 回溯").set_defaults(fn=lambda x: C.check_cmd(load_cfg()))
    q = sp.add_parser("recall", help="召回材料包（advisory）")
    q.add_argument("query")
    q.set_defaults(fn=lambda x: C.recall_cmd(load_cfg(), x.query))
    sp.add_parser("drift", help="哪些源文档变了 → 待重审").set_defaults(fn=lambda x: C.drift(load_cfg()))
    rv = sp.add_parser("review", help="待审清单")
    rv.add_argument("what", choices=["cards", "edges", "defs", "tree"])
    rv.add_argument("--block")
    rv.set_defaults(fn=lambda x: review.run(load_cfg(), x.what, x.block))
    cl = sp.add_parser("claim", help="认领一张 staging 卡：写 owner、归位 blocks/<块>/（权限=关 3 同表）")
    cl.add_argument("card")
    cl.add_argument("--block", help="纠正归属块（默认卡上机器推断的块）")
    cl.add_argument("--owner", help="责任人（默认同 --by）")
    cl.add_argument("--by", required=True, help="操作人，须是该块 owner")
    cl.set_defaults(fn=lambda a: review.claim(load_cfg(), a.card, a.block, a.owner, a.by))
    sp.add_parser("graph", help="输出 Mermaid 关系图文本（贴飞书/Notion 即可看）").set_defaults(
        fn=lambda x: review.mermaid_cmd(load_cfg()))
    sp.add_parser("open", help="在浏览器打开 build/graph.html（待办总览：待认领 / 需 OCR / 待审边；选中节点 → 复制给 Agent 的命令）").set_defaults(
        fn=lambda x: review.open_html(load_cfg()))
    sp.add_parser("serve", help="以 MCP server 启动（stdio）。需 pip install knowledger[mcp]").set_defaults(fn=_serve)
    ap_ = sp.add_parser("approve", help="人审一条边（关 6）")
    for k in ("card", "rel", "other", "verdict"):
        ap_.add_argument(k)
    ap_.add_argument("note", nargs="?", default="")
    ap_.add_argument("--by", required=True, help="审核人")
    ap_.set_defaults(fn=_approve)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (ConfigError, core.GateError, review.ReviewDenied) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
