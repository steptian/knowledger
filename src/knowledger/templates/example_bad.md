---
id: BAD-001
type: Feature
name: 故意违规样例
owner: 不存在的人
block: __FIRST_BLOCK__
status: active
valid_from: 2026-01-01
sources: 这是一段自由文本出处
relates_to:
  - rel: 不是合法关系
    to: EXAMPLE-001
---

## 定义
故意违规：sources 是自由文本（关 1）、关系名不在白名单（关 1）、owner 无写权（关 3）。
`knowledger selftest` 断言本条必须被拦住。看懂了就删掉这个文件。
