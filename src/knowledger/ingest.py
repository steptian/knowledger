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
DOCX_EXT = {".docx", ".xlsx"}              # 零依赖抽取（zipfile + ElementTree），旁车 .md 缓存
BINARY_EXT = {".doc", ".pdf"}              # 仅建卡正文空；配 ocr_cmd 后 cards 会调它
EXT_OK = TEXT_EXT | DOCX_EXT | BINARY_EXT
SIDECAR_ENDS = tuple(e + ".md" for e in sorted(DOCX_EXT | BINARY_EXT))   # a.docx → a.docx.md
MAX_XML = 512 * 1024 * 1024   # 单个 OOXML 部件解压上限：防 zip 炸弹（实测 deflate 可达 1000:1）


def _zread(z, name: str) -> bytes:
    if z.getinfo(name).file_size > MAX_XML:
        raise ValueError(f"{name} 解压后超 512MB，疑似 zip 炸弹，拒绝解析")
    return z.read(name)

API_ROW = re.compile(r"^\|\s*(GET|POST|PUT|DELETE|PATCH)\s*\|\s*`?([^|`]+)`?\s*\|\s*([^|]+?)\s*\|")
H3 = re.compile(r"^###\s+[\d.]+\s*(.+)$")
BOLD_BULLET = re.compile(r"^\s*-\s*\*\*([^*]{2,30})\*\*[：:]\s*(.+)$")
SRC_LINE = re.compile(r"`(raw/[^`]+)`")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def read_text(p: Path) -> str:
    if p.suffix.lower() in BINARY_EXT | DOCX_EXT:
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def extract_docx(p: Path) -> str:
    """word/document.xml → 文本；Heading 样式映射 #/##/###（### 正好喂候选切分）。表格段落按普通行处理。"""
    import xml.etree.ElementTree as ET
    import zipfile
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(p) as z:
        root = ET.fromstring(_zread(z, "word/document.xml"))
    out = []
    for para in root.iter(f"{W}p"):
        st = para.find(f"{W}pPr/{W}pStyle")
        m = re.match(r"(?:[Hh]eading\s*)?(\d)$", st.get(f"{W}val", "")) if st is not None else None
        text = "".join(t.text or "" for t in para.iter(f"{W}t")).strip()
        text = re.sub(r"\s*\r?\n\s*", " ", text)   # 单元格/段落内换行会切断下游 pipe 行与标题匹配
        if text:
            out.append(("#" * int(m.group(1)) + " " + text) if m and 1 <= int(m.group(1)) <= 6 else text)
    return "\n\n".join(out)


def _xlsx_cell(c, shared: list[str]) -> str:
    """单个 xlsx 单元格 → 文本。sharedStrings / 内联字符串 / 数值（公式取缓存值）。"""
    NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    if c.get("t") == "s":
        v = c.find(f"{NS}v")
        return shared[int(v.text)] if v is not None and v.text else ""
    if c.get("t") == "inlineStr":
        return "".join(t.text or "" for t in c.iter(f"{NS}t"))
    v = c.find(f"{NS}v")
    return v.text if v is not None and v.text else ""


def extract_xlsx(p: Path) -> str:
    """每张 sheet → `## Sheet: 名` + pipe 表行（sharedStrings / 内联字符串 / 数值，公式取缓存值）。"""
    import xml.etree.ElementTree as ET
    import zipfile
    NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    RN = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    def rid(el) -> str:            # 属性大小写兜底：标准是 r:id（小写），部分生成器写 Id
        return el.get(f"{RN}id") or el.get(f"{RN}Id") or ""
    with zipfile.ZipFile(p) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            sroot = ET.fromstring(_zread(z, "xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{NS}t")) for si in sroot.findall(f"{NS}si")]
        wb = ET.fromstring(_zread(z, "xl/workbook.xml"))
        names = z.namelist()
        rmap = {}
        if "xl/_rels/workbook.xml.rels" in names:
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            rmap = {rid(r): r.get("Target") for r in rels}
        out = []
        for s in wb.iter(f"{NS}sheet"):
            target = rmap.get(rid(s), "")
            if not target:   # 部分生成器的 rels 缺 worksheet 条目：按 rIdN↔sheetN.xml 命名惯例回退
                n = rid(s).removeprefix("rId")
                target = f"worksheets/sheet{n}.xml" if f"xl/worksheets/sheet{n}.xml" in names else ""
            if not target:
                continue
            path = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
            lines = [f"## Sheet: {s.get('name')}", ""]
            for row in ET.fromstring(_zread(z, path)).iter(f"{NS}row"):
                cells = [re.sub(r"\s*\r?\n\s*", " ", _xlsx_cell(c, shared)) for c in row.iter(f"{NS}c")]
                if any(x.strip() for x in cells):
                    lines.append("| " + " | ".join(cells) + " |")
            if len(lines) > 2:
                out.append("\n".join(lines))
    return "\n\n".join(out)


def run_ocr(tmpl: str, p: Path) -> str:
    """ocr_cmd 插件点：模板含 %s 则替换为引号包住的路径，否则追加在末尾；stdout 即正文。"""
    import shlex
    import subprocess
    cmd = tmpl.replace("%s", shlex.quote(str(p))) if "%s" in tmpl else f"{tmpl} {shlex.quote(str(p))}"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        print(f"  ⚠ OCR 超时（600s）：{p.name}")
        return ""
    if r.returncode != 0 or not r.stdout.strip():
        print(f"  ⚠ OCR 失败 {p.name}：{(r.stderr or '无输出').strip()[:200]}")
        return ""
    return r.stdout


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


# ---------- lark（飞书，外部命令） ----------
def lark(cfg: Config, urls: list[str]) -> int:
    """lark-cli docs +fetch 拉 markdown 落到 raw/feishu/，后续 cards 无感知。lark-cli 缺失时给出指路。"""
    import json
    import subprocess
    out_dir = cfg.raw / "feishu"
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = 0
    for url in urls:
        try:
            r = subprocess.run(
                ["lark-cli", "docs", "+fetch", "--doc", url, "--doc-format", "markdown", "--scope", "full"],
                capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            print("✗ 找不到 lark-cli。飞书抓取是外部命令（核心零依赖）：安装后重试。")
            return 2
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            print(f"  ⚠ {url}：输出不是 JSON：{(r.stderr or r.stdout).strip()[:200]}")
            continue
        if not data.get("ok"):
            msg = (data.get("error") or {}).get("message", "未知错误")
            print(f"  ⚠ {url}：{str(msg)[:200]}")
            continue
        try:
            content = data["data"]["document"]["content"]
        except (KeyError, TypeError) as e:
            print(f"  ⚠ {url}：输出缺字段（lark-cli schema 漂移？）：{e}")
            continue
        m = re.match(r"<title>(.*?)</title>", content, re.S)
        token = url.rstrip("/").split("/")[-1].split("?")[0]
        name = re.sub(r'[\\/:*?"<>|\s]+', "-", m.group(1) if m else token).strip("-.")[:60] or f"lark-{token}"
        out = out_dir / f"{name}.md"
        if out.exists() and out.read_text(encoding="utf-8", errors="ignore") != content + "\n":
            out = out_dir / f"{name}-{token[:8]}.md"   # 同名不同文：追加 token，遵守 raw/ 只增不改
        try:
            out.write_text(content + "\n", encoding="utf-8")
        except (OSError, ValueError) as e:
            print(f"  ⚠ {url}：写盘失败：{e}")
            continue
        print(f"  + {out.relative_to(cfg.root)}")
        ok += 1
    print(f"lark：{ok}/{len(urls)} 份 → raw/feishu/；接着 `knowledger cards`")
    return 0 if ok else 1


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


def _body_text(cfg: Config, p: Path, sidecar: Path) -> str:
    """旁车优先（人审修正以它为准）→ docx/xlsx 零依赖抽取 → ocr_cmd 插件 → 纯文本。单文件失败不断整轮。"""
    if sidecar.exists():
        return read_text(sidecar)
    if p.suffix.lower() in DOCX_EXT:
        try:
            text = extract_docx(p) if p.suffix.lower() == ".docx" else extract_xlsx(p)
        except Exception as e:
            print(f"  ⚠ 抽取失败 {p.name}：{type(e).__name__}: {e}")
            return ""
        if text:
            sidecar.write_text(
                f"<!-- knowledger 旁车：机器抽取自 {p.name}；可手工修正，重跑 cards 以本文件为准，删除则重新抽取。 -->\n\n{text}\n",
                encoding="utf-8")
        return text
    if p.suffix.lower() in BINARY_EXT and cfg.ocr_cmd:
        text = run_ocr(cfg.ocr_cmd, p)
        if text:
            sidecar.write_text(
                f"<!-- knowledger 旁车：OCR 自 {p.name}；可手工修正，删除则下次重跑 OCR。 -->\n\n{text}\n",
                encoding="utf-8")
        return text
    return read_text(p)


def cards(cfg: Config) -> int:
    if not cfg.raw.exists() or not any(cfg.raw.rglob("*")):
        print("raw/ 是空的，先 `knowledger scan <目录>`")
        return 1
    cfg.staging.mkdir(exist_ok=True)
    manifest: list[ManifestItem] = []
    today = date.today().isoformat()
    for p in sorted(cfg.raw.rglob("*")):
        if not p.is_file() or p == cfg.raw / "README.md":
            continue
        if p.name.endswith(SIDECAR_ENDS) and p.with_name(p.name[:-3]).exists():
            continue   # 旁车；但对应原件已不在的 .docx.md 是正经文档，照常建卡
        rel = p.relative_to(cfg.raw)
        text = _body_text(cfg, p, p.with_name(p.name + ".md"))   # 旁车：a.docx → a.docx.md
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
            lines.append("- ⚠ 二进制文档（doc / pdf / docx / xlsx）：无正文，需 OCR 或手工补旁车 .md 后重跑 cards")
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
