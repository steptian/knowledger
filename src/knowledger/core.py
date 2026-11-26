"""core.py — the mechanical gates, add, snapshot, selftest.

Gate 1 structure · Gate 2 term drift · Gate 3 block ownership · Gate 4 staleness ·
Gate 5 approval lives in the carrier (git PR etc.), not here.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path

from . import fm
from .config import Config
from .ingest import iter_cards, read_text


class GateError(Exception):
    pass


# ---------- glossary ----------
def load_glossary(cfg: Config) -> tuple[list[dict], set[str]]:
    p = cfg.ontology / "glossary.csv"
    if not p.exists():
        raise GateError(f"缺术语表: {p.relative_to(cfg.root)}")
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    aliases: set[str] = set()
    for r in rows:
        aliases.add(r["标准术语"])
        for a in re.split(r"[;；,，/]", r.get("别名", "")):
            if a.strip():
                aliases.add(a.strip())
    return rows, aliases


# ---------- gate 1 + 3 ----------
def check_instance(f: dict, cfg: Config) -> list[str]:
    errs = []
    for k in cfg.common_required:
        if k not in f or f[k] in ("", [], None):
            errs.append(f"关1 缺字段 {k}")
    if f.get("status") and f["status"] not in cfg.status_enum:
        errs.append(f"关1 status 非法: {f['status']}")
    t = cfg.types.get(f.get("type", ""))
    if f.get("type") and t is None:
        errs.append(f"关1 type 非法: {f['type']}")
    if t:
        for k in t.required:
            if k not in f or f[k] in ("", [], None):
                errs.append(f"关1 {f['type']} 缺 {k}")
        for field_, allowed in t.enum.items():
            if field_ in f and f[field_] not in allowed:
                errs.append(f"关1 {f['type']}.{field_} 不在 {allowed}: {f[field_]}")
    srcs = f.get("sources", [])
    if isinstance(srcs, str):
        errs.append("关1 sources 必须是列表（doc + version），不能是字符串")
    else:
        for s in srcs:
            if not isinstance(s, dict) or "doc" not in s or "version" not in s:
                errs.append(f"关1 sources 未类型化: {s}")
    rels = f.get("relates_to", [])
    if isinstance(rels, list):
        for r in rels:
            if not isinstance(r, dict):
                continue
            if r.get("rel") not in cfg.relations:
                errs.append(f"关1 关系非法: {r.get('rel')}")
            # 关 6：active 实例的每条边必须有人审过（verdict ∈ ok / wrong）；draft 不查
            rv = r.get("review")
            if f.get("status") == "active" and rv not in ("ok", "wrong"):
                errs.append(f"关6 边 {f.get('id')} --{r.get('rel')}--> {r.get('to') or r.get('from')} 未经人审（需 review.verdict: ok|wrong）")
    blk, owner = f.get("block"), f.get("owner")
    if blk and owner:
        if blk not in cfg.blocks:
            errs.append(f"关3 未定义的块: {blk}")
        else:
            owners = cfg.blocks[blk].owners or []
            if owner not in owners:
                errs.append(f"关3 {owner} 无 {blk} 块写权（该块 owners: {cfg.blocks[blk].owners}）")
    return errs


def check(cfg: Config) -> int:
    cards = iter_cards(cfg, include_staging=False)
    bad = 0
    for p, f, _ in cards:
        errs = check_instance(f, cfg)
        if errs:
            bad += 1
            print(f"✗ {p.relative_to(cfg.root)}")
            for e in errs:
                print(f"    {e}")
    print(f"\n{len(cards)} 条实例，{bad} 条不通过")
    return 1 if bad else 0


# ---------- gate 2 ----------
def lint(cfg: Config, upstream: str | None) -> int:
    _, aliases = load_glossary(cfg)
    total = known = 0
    unknown: dict[str, list[str]] = {}
    for p, f, _ in iter_cards(cfg, include_staging=False):
        for w in set(re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,6}", f.get("name", ""))):
            total += 1
            if w in aliases:
                known += 1
            else:
                unknown.setdefault(w, []).append(p.name)
    cov = known / total if total else 0
    print(f"术语覆盖率（实例 name 候选词口径）: {cov:.0%}  ({known}/{total})")
    if unknown:
        print("\n待归一队列（Top 20）:")
        for w, files in sorted(unknown.items(), key=lambda kv: -len(kv[1]))[:20]:
            print(f"  {w:<14} ← {', '.join(sorted(set(files)))}")
    if upstream:
        up = Path(upstream)
        if up.exists():
            cur = {str(f.relative_to(up)): hashlib.sha256(f.read_bytes()).hexdigest()[:12]
                   for f in sorted(up.rglob("*")) if f.is_file()}
            prev_p = cfg.ontology / "upstream_fingerprint.json"
            try:
                prev = json.loads(prev_p.read_text(encoding="utf-8")) if prev_p.exists() else {}
            except json.JSONDecodeError as e:
                raise GateError(f"{prev_p.name} 不是合法 JSON: {e}") from e
            changed = [k for k in cur if k in prev and prev[k] != cur[k]]
            print(f"\n上游指纹（{up}）: 变化 {len(changed)} · 新增 {len([k for k in cur if k not in prev])}")
            for k in changed:
                print(f"  待同步队列 ← {k} 已变")
            prev_p.write_text(json.dumps(cur, ensure_ascii=False, indent=1))
    return 0


# ---------- gate 4 ----------
def snapshot(cfg: Config) -> int:
    s = cfg.snapshot
    if s.exists():
        if not (s / "MANIFEST.json").exists():
            raise GateError(f"{s.relative_to(cfg.root)}/ 存在但没有 MANIFEST.json，不是 knowledger 生成的，拒绝覆盖")
        try:
            shutil.rmtree(s)
        except OSError as e:
            raise GateError(f"删除旧快照失败: {e}") from e
    s.mkdir(parents=True)
    kept = archived = 0
    for p, f, _ in iter_cards(cfg, include_staging=False):
        rel = p.relative_to(cfg.blocks_dir)
        deprecated = f.get("status") in ("deprecated", "已废弃")
        dst = (s / "_archive" / rel) if deprecated else (s / rel)
        kept += 0 if deprecated else 1
        archived += 1 if deprecated else 0
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
    (s / "MANIFEST.json").write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "active": kept, "deprecated_archived": archived,
        "note": "AI 只读 snapshot/；_archive/ 不要挂给 AI。",
    }, ensure_ascii=False, indent=1))
    print(f"snapshot：active {kept} 条 / deprecated 归档 {archived} 条")
    return 0


# ---------- add ----------
def add(cfg: Config, *, type_: str, id_: str, name: str, owner: str, block: str, doc: str | None, ver: str) -> int:
    if block not in cfg.blocks:
        print(f"✗ 未定义的块: {block}（可用：{', '.join(cfg.blocks)}）")
        return 1
    if cfg.blocks[block].owners is None or owner not in cfg.blocks[block].owners:
        print(f"✗ 关3 {owner} 无 {block} 块写权（owners: {cfg.blocks[block].owners}）")
        return 1
    spec = cfg.types.get(type_)
    if spec is None:
        print(f"✗ 未定义的类型: {type_}（可用：{', '.join(cfg.types)}）")
        return 1
    if spec.block != block:
        print(f"✗ 类型 {type_} 属于 {spec.block} 块，不属于 {block}")
        return 1
    dst = cfg.blocks_dir / block / type_.lower() / f"{id_}.md"
    if dst.exists():
        print(f"✗ 已存在: {dst.relative_to(cfg.root)}")
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    f = {"id": id_, "type": type_, "name": name, "owner": owner, "block": block, "status": "draft",
         "valid_from": date.today().isoformat(),
         "sources": [{"doc": doc, "version": int(ver)}] if doc else [], "relates_to": []}
    body = "\n## 定义\n（一句话说清）\n\n## 待验证 / 争议\n无\n"
    dst.write_text(fm.dump(f, body), encoding="utf-8")
    print(f"已创建 {dst.relative_to(cfg.root)}（status=draft）→ 补 sources 与定义后 `knowledger check`")
    return 0


# ---------- selftest ----------
def selftest(cfg: Config) -> int:
    bad_dir = cfg.blocks_dir
    bad_cases = sorted(bad_dir.rglob("_bad*.md")) if bad_dir.exists() else []
    if not bad_cases:
        print("✗ 没找到 _bad*.md 样例（init 会放一条在 blocks/<首块>/examples/）")
        return 1
    ok = True
    for p in bad_cases:
        f, _ = fm.read_card(p)
        errs = check_instance(f, cfg)
        if errs:
            print(f"✓ 正确拦住 {p.name}: {errs[0]} …")
        else:
            ok = False
            print(f"✗ 漏放 {p.name}")
    snapshot(cfg)
    leaks = [p for p in cfg.snapshot.rglob("*.md")
             if fm.read_card(p)[0].get("status") in ("deprecated", "已废弃") and "_archive" not in p.parts]
    if leaks:
        ok = False
        print(f"✗ 快照 active 区仍有 deprecated: {leaks}")
    else:
        print("✓ 快照 active 区无 deprecated（关 4）")
    return 0 if ok else 1
