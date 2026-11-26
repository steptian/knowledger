"""格式支持测试：docx/xlsx 零依赖抽取 + 旁车缓存 + ocr_cmd 插件点 + lark 缺外部命令分支。"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R_NS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def run(*args, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "knowledger.cli", *args],
                          cwd=cwd, capture_output=True, text=True, env=env)


@pytest.fixture()
def kb(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    (tmp_path / "docs").mkdir(parents=True)
    r = run("init", str(root), "--name", "t", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    return root


def make_docx(path: Path, paras: list[tuple[str | None, str]]) -> None:
    ps = []
    for style, text in paras:
        st = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        ps.append(f"<w:p>{st}<w:r><w:t>{text}</w:t></w:r></w:p>")
    xml = f'<?xml version="1.0"?><w:document {W_NS}><w:body>{"".join(ps)}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)


def make_xlsx(path: Path, sheet: str, rows: list[list[str | float]], rels: bool = True) -> None:
    """sharedStrings 方式：字符串进 sst，数字内联。rels=False 模拟缺 worksheet 关系的非标准生成器。"""
    strings, idx = [], {}
    grid = []
    for row in rows:
        cells = []
        for v in row:
            if isinstance(v, str):
                if v not in idx:
                    idx[v] = len(strings)
                    strings.append(v)
                cells.append(f'<c t="s"><v>{idx[v]}</v></c>')
            else:
                cells.append(f"<c><v>{v}</v></c>")
        grid.append(f"<row>{''.join(cells)}</row>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml",
                   f'<?xml version="1.0"?><workbook {S_NS} {R_NS}><sheets>'
                   f'<sheet name="{sheet}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        if rels:
            z.writestr("xl/_rels/workbook.xml.rels",
                       '<?xml version="1.0"?><Relationships '
                       'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml",
                   '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<?xml version="1.0"?><worksheet {S_NS}><sheetData>{"".join(grid)}</sheetData></worksheet>')


def test_docx_extraction_and_sidecar(kb: Path, tmp_path: Path):
    make_docx(tmp_path / "docs" / "退货.docx", [
        ("Heading1", "退货模块"), ("Heading3", "3.1 退款规则"), (None, "普通正文段落。")])
    r = run("scan", str(tmp_path / "docs"), cwd=kb)
    assert r.returncode == 0, r.stderr
    assert (kb / "raw" / "docs" / "退货.docx").exists()
    r = run("cards", cwd=kb)
    assert r.returncode == 0, r.stderr
    sidecar = (kb / "raw" / "docs" / "退货.docx.md").read_text(encoding="utf-8")
    assert sidecar.startswith("<!-- knowledger 旁车") and "### 3.1 退款规则" in sidecar
    cards = list((kb / "staging").glob("*.md"))
    assert len(cards) == 1, "旁车不得单独建卡"
    body = cards[0].read_text(encoding="utf-8")
    assert "§ 退款规则" in body and "需 OCR" not in body


def test_sidecar_manual_edit_wins(kb: Path, tmp_path: Path):
    make_docx(tmp_path / "docs" / "a.docx", [("Heading3", "1.1 原规则")])
    run("scan", str(tmp_path / "docs"), cwd=kb)
    run("cards", cwd=kb)
    sidecar = kb / "raw" / "docs" / "a.docx.md"
    sidecar.write_text(sidecar.read_text(encoding="utf-8").replace("1.1 原规则", "1.1 手工修正"), encoding="utf-8")
    r = run("cards", cwd=kb)
    assert r.returncode == 0, r.stderr
    assert "手工修正" in next((kb / "staging").glob("*.md")).read_text(encoding="utf-8")


def test_xlsx_extraction(kb: Path, tmp_path: Path):
    make_xlsx(tmp_path / "docs" / "接口.xlsx", "价格",
              [["方法", "路径", "说明"], ["GET", "/orders", "创建订单"], [9.9]])
    run("scan", str(tmp_path / "docs"), cwd=kb)
    r = run("cards", cwd=kb)
    assert r.returncode == 0, r.stderr
    sidecar = (kb / "raw" / "docs" / "接口.xlsx.md").read_text(encoding="utf-8")
    assert "## Sheet: 价格" in sidecar and "| GET | /orders | 创建订单 |" in sidecar and "| 9.9 |" in sidecar
    body = next((kb / "staging").glob("*.md")).read_text(encoding="utf-8")
    assert "GET /orders" in body, "xlsx 里的 API 表行应被切为候选条目"
    assert "需 OCR" not in body


def test_xlsx_without_worksheet_rels_falls_back(kb: Path, tmp_path: Path):
    """非标准生成器的 rels 缺 worksheet 关系 → 按 rIdN↔sheetN.xml 惯例回退，不得静默丢 sheet。"""
    make_xlsx(tmp_path / "docs" / "接口.xlsx", "核心业务", [["GET", "/a"]], rels=False)
    run("scan", str(tmp_path / "docs"), cwd=kb)
    r = run("cards", cwd=kb)
    assert r.returncode == 0, r.stderr
    sidecar = (kb / "raw" / "docs" / "接口.xlsx.md").read_text(encoding="utf-8")
    assert "## Sheet: 核心业务" in sidecar and "| GET | /a |" in sidecar


def test_xlsx_newline_in_cell_becomes_space(kb: Path, tmp_path: Path):
    """真实数据单元格内含换行 → 清成空格，pipe 行不被切断。"""
    make_xlsx(tmp_path / "docs" / "b.xlsx", "表", [["款名", "SKU"], ["长款名\n（括号注）", "蓝"]])
    run("scan", str(tmp_path / "docs"), cwd=kb)
    r = run("cards", cwd=kb)
    assert r.returncode == 0, r.stderr
    sidecar = (kb / "raw" / "docs" / "b.xlsx.md").read_text(encoding="utf-8")
    assert "| 长款名 （括号注） | 蓝 |" in sidecar, "换行应被清成空格且同行成表"


def test_broken_docx_does_not_kill_cards(kb: Path, tmp_path: Path):
    """F1：一个畸形 docx 不得杀死整轮 cards，其余文件照常建卡。"""
    (tmp_path / "docs" / "坏文件.docx").write_bytes(b"not a zip at all")
    (tmp_path / "docs" / "正常.md").write_text("### 1.1 有效条目\n", encoding="utf-8")
    run("scan", str(tmp_path / "docs"), cwd=kb)
    r = run("cards", cwd=kb)
    assert r.returncode == 0, r.stderr
    assert "抽取失败" in r.stdout
    bodies = {c.read_text(encoding="utf-8") for c in (kb / "staging").glob("*.md")}
    assert any("有效条目" in b for b in bodies), "其余文件必须照常建卡"
    assert any("需 OCR" in b for b in bodies), "坏 docx 归入需 OCR 路径"


def test_ocr_cmd_fills_binary_card(kb: Path, tmp_path: Path):
    (kb / "knowledger.yaml").write_text(
        (kb / "knowledger.yaml").read_text(encoding="utf-8") + '\nocr_cmd: "echo OCR正文"\n', encoding="utf-8")
    (tmp_path / "docs" / "报告.pdf").write_bytes(b"")
    run("scan", str(tmp_path / "docs"), cwd=kb)
    r = run("cards", cwd=kb)
    assert r.returncode == 0, r.stderr
    assert "OCR正文" in (kb / "raw" / "docs" / "报告.pdf.md").read_text(encoding="utf-8")
    assert "需 OCR" not in next((kb / "staging").glob("*.md")).read_text(encoding="utf-8")


def test_doc_without_ocr_stays_needs_ocr(kb: Path, tmp_path: Path):
    (tmp_path / "docs" / "旧.doc").write_bytes(b"")
    run("scan", str(tmp_path / "docs"), cwd=kb)
    r = run("cards", cwd=kb)
    assert r.returncode == 0, r.stderr
    body = next((kb / "staging").glob("*.md")).read_text(encoding="utf-8")
    assert "需 OCR" in body
    assert not (kb / "raw" / "docs" / "旧.doc.md").exists()


def test_lark_requires_external_cli(kb: Path, tmp_path: Path):
    r = run("lark", "https://xx.feishu.cn/wiki/ABC", cwd=kb, env={"PATH": "/nonexistent"})
    assert r.returncode == 2
    assert "lark-cli" in (r.stdout + r.stderr)
