#!/usr/bin/env python3
"""DeepSeek OCR 参考实现 —— knowledger 的 ocr_cmd 插件点用。

knowledger.yaml 里配一行（%s 处替换为文件路径）：
    ocr_cmd: "python3 /path/to/plugins/ocr_deepseek.py %s"

环境变量 DEEPSEEK_API_KEY 必填。API: POST https://api.deepseek.com/v1/tools/ocr
（multipart/form-data，purpose=text_recognition，响应 pages[].text；支持 PDF/DOC/DOCX/PPT/图片）
标准库实现，零第三方依赖。识别失败退非零码并往 stderr 写原因。
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: ocr_deepseek.py <文件>", file=sys.stderr)
        return 2
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("缺环境变量 DEEPSEEK_API_KEY", file=sys.stderr)
        return 2
    path = sys.argv[1]
    try:
        with open(path, "rb") as f:
            file_bytes = f.read()
    except OSError as e:
        print(f"读不了 {path}: {e}", file=sys.stderr)
        return 2
    boundary = uuid.uuid4().hex
    safe_name = re.sub(r'["\r\n]', "", os.path.basename(path)) or "doc"   # 防引号/CRLF 破坏表单 part
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\ntext_recognition\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/tools/ocr", data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            out = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"OCR API 失败: {e}", file=sys.stderr)
        return 1
    print("\n\n".join(pg.get("text", "") for pg in out.get("pages", [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
