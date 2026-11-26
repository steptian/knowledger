# knowledger

**文档与 AI 工具之间的治理层。** 文档原样进，机器切索引卡，人按最小单元审核（一条边、一个定义、一个归属），`knowledger build` 编译出 AI 只读、可追溯的快照。

核心零第三方依赖。Python ≥ 3.10。MIT。

```
docs ──scan──▶ raw/ ──cards──▶ staging/ ──review──▶ blocks/ ──build──▶ build/SNAPSHOT.md
      (原样)             (索引卡)       (人审 3 字段)            (AI 只读产物 + 关系图)
```

## 为什么

团队不缺知识——模块文档、API 手册、会议纪要、PRD 里全都有。缺的是两件事：

1. **AI 读到的内容没人背书**——模型张口就来的每句话无法追溯到「哪天、哪个人、确认过哪条」；
2. **人审过的内容没人维护**——审完即死档，源文档改了没人知道。

knowledger 用一条流水线把两端焊住：进不要求先建 schema，出必经真人审核，中间六道机械关挡住一切「靠自觉」。

## 全流程一页图

```
            ┌─────────────────── 三个入口，同一套动词 ───────────────────┐
            │  CLI（人 + Agent）      MCP（13 工具）      HTML（看 + 复制）  │
            └───────────────────────────┬────────────────────────────────┘
                                        ▼
  任何文档 ──scan──▶  raw/  ──cards──▶  staging/  ──review──▶  blocks/  ──build──▶  build/
  .md .txt .html     原样            索引卡              唯一编辑面           可重建产物
  .docx .xlsx*       只增不改        status=draft        所有编辑落盘这里     SNAPSHOT.md
  .doc .pdf*         sha 指纹        owner=待审          ┌──────────────┐    rewrite.json
  飞书 / Notion*                    → 不进编译           │ 人审三件事：  │    graph.json/.html/.mmd
                                                        │  认领(owner) │    ontology.json
                                                        │  写定义       │    deprecated / wrong 边
                                                        │  审边 ok|wrong│    物理排除
                                                        └──────┬───────┘
                                                               │
   六道关 ─────────────────────────────────────────────────────┤
   关1 结构完整 ─ check/build      关4 时效有效 ─ snapshot/build  │
   关2 术语/源漂移 ─ lint/drift    关5 审批生效 ─ 载体（git PR）   │
   关3 块归属写入 ─ check/add/claim/build  关6 边已人审 ─ check/build/approve
                                                               │
                                        ┌──────────────────────┴──────────────────────┐
                                        ▼                      ▼                      ▼
                                  AI 工具（只读）          检索层（advisory）        人看全貌
                                  挂 build/SNAPSHOT.md    rewrite + 一跳邻居     graph.html / Mermaid
                                  永远不挂 blocks/ raw/   材料不是规则
```

\* 待做：旁车 .md（docx/xlsx）/ OCR 插件 / 飞书插件。

## 亮点

- **文档进，不是 schema 先行。** 机器按 3 条正则切候选条目（API 表行、三级标题、加粗要点），人只做裁决；没人从零手写 frontmatter。
- **六道机械关，不靠约定。** 无 owner 的卡、没人审的边、过时条目混进 AI 快照——被 `check` / `build` 代码拒绝，不是被希望拒绝。
- **最小单元人审。** 一条边 `A --rel--> B`，两端定义和源文出现行并排给你 → `ok` 或 `wrong`。没人「审批一个本体」。
- **权限 = 真名。** 所有写操作 `by` 必须是该块 owner（同一张表管 CLI 和 MCP）；Agent 自报名直接被拒并附提示。prompt 注入式越权在数据模型这一层就卡死。
- **载体中立。** markdown + frontmatter + git；产物全部可重建；AI 只挂 `build/SNAPSHOT.md`，永不直接读卡片。
- **Agent 能跑完整审核动线。** 13 个 MCP 工具与 CLI 一一对应：`knowledger_status` → 列证据 → 问人 → `knowledger_approve` → `knowledger_check` → `knowledger_build`。
- **待办总览页。** `knowledger open` 打开即是：待认领 / 需 OCR / 待审边，每项附可复制的 Agent 指令。图只看不改（编辑走表单，WebVOWL 用户研究的结论）。
- **核心零第三方依赖。** 一条 `pip install` 就能跑通全闭环；LLM / OCR / 飞书全是插件点，不是依赖。

## 与同类项目的差异

公开项目大致三簇（截至 2026-11 扫描）：**抠事实**（myKG、trikedb）、**审标注**（AnNER、Label Studio 族）、**喂 AI**（llms.txt 族）。knowledger 是少数把三段焊成一条带机械关流水线的：进不需要 LLM，出必有真人背书。

| 维度 | LLM 抽取管线 | 标注/复审工具 | AI 可读导出 | knowledger |
|---|---|---|---|---|
| 信任模型 | 模型置信分 0–1 | 标注状态+溯源 | 无治理 | 每条边指到一个真人和一天（ok/wrong + review_by/at） |
| 审核 | schema 级一次性门 / PR-diff 粒度 | 实体在文本里，非关系图 | 无 | 逐条边带两端定义+源文行的持续流程 |
| 权限 | 无 owners 概念 | 角色制（部署重） | 无 | owners 表 = 写权 = 审批权，CLI/MCP 同表 |
| 依赖 | 必须有 LLM API key 等 | Web 服务+数据库 | 站点插件 | 核心零依赖，LLM 是可插加速器 |
| 漂移治理 | schema 用途漂移检测 | 无 | 无 | sha 指纹 drift：源文档变了 → 卡自动待重审 |
| AI 侧接口 | MCP 只读 / Obsidian vault | — | llms.txt / MCP | SNAPSHOT + recall + 13 MCP 工具（含 owner 门禁的写） |

三句话版本：

- **信任**：别的项目给 AI 喂「更多上下文」，knowledger 喂「有人名背书的最小事实集」——AI 说错的每句话，可以追溯到哪天哪个人审的哪条边。
- **成本**：不需要 GPU、API key、向量库就能跑通全闭环。
- **诚实边界**：下面「不做的事」一节本身就是差异化——竞品的过度承诺区，正是 knowledger 不做的地方。

## 安装

```bash
pip install -e .          # from a checkout; PyPI publishing not done yet
knowledger --version
```

换机部署 + 上手指引（怎么装、日常动线、FAQ）：[`docs/快速上手.md`](docs/快速上手.md)

## 10 分钟上手

```bash
knowledger init my-kb && cd my-kb
$EDITOR knowledger.yaml       # 唯一配置：块、owners、类型、关系词表
knowledger selftest           # init 自带的故意违规样例必须被拦

knowledger scan ~/docs/api ~/docs/prd          # 文档 → raw/，原样不动
knowledger cards                               # raw/ → 每份一张索引卡 → staging/
knowledger review cards                        # 看按块分布，挑你的

knowledger claim D-XX --by 李四                 # 认领：写 owner、归位 blocks/<块>/
knowledger check              # 关1 结构 · 关3 归属 · 关6 边已审
knowledger review edges       # 每条边：两端 + 源文行 → 审
knowledger approve D-XX contains BF-01 ok --by zhangsan "归属正确"
knowledger build              # → build/ontology.json  SNAPSHOT.md  rewrite.json  graph.*
knowledger graph              # Mermaid 文本——贴飞书 / Notion / Obsidian
knowledger open               # 待办总览：待认领 / 需 OCR / 待审边
knowledger recall "怎么取消订单"   # 给 LLM 的材料包，不是规则
knowledger drift              # 哪些源文档变了 → 待重审
```

## 审核动线（Agent 视角，走 MCP）

`knowledger_status`（谁拥有哪个块）→ `knowledger_review_edges`（待审边带两端定义和源文行）→ *Agent 念给 owner 听，owner 说 对 / 错 / 错在哪端* → `knowledger_approve(by=<owner>)` → `knowledger_check` 必须过 → `knowledger_build`。Agent 自编一个名字，会被拒并收到提示。owner 的那一个字是全流程唯一没有工具的步骤——故意的。

## 一张索引卡长什么样

```markdown
---
id: D-5BEC93DE
type: Doc
name: 02-流向.md
owner: 李四              ← 人填
block: engineering       ← 机器推断（路径 / 关键词计分），人纠正
status: active
version: 1
sources:
  - doc: D-5BEC93DE
    version: 1
relates_to:
  - rel: contains
    from: BF-001
    review: ok           ← 关6：active 卡的每条边都要有这个
    review_by: 李四
    review_at: 2026-11-24T08:31:21
---
## 来源
`raw/engineering/02-流向.md`　sha `5bec93de`
## 机器推断
- 归属块：**engineering**
## 候选条目（25 条）
1. § 流向清洗
2. `POST /start` 启动流向清洗任务    ← 一行 = 一个候选功能
## 定义
（人写，一句话）
```

## 六道关

| 关 | 拦什么 | 在哪执行 |
|---|---|---|
| 关1 结构 | 缺必填字段；sources 非 `[{doc, version}]`；关系名不在白名单 | `check`、`build` |
| 关2 术语/源漂移 | 实例名不在术语表；raw/ 源 sha 变化（报告） | `lint`、`drift` |
| 关3 块归属写入 | 写入者不在该块 `owners` | `check`、`add`、`claim`、`build` |
| 关4 时效 | deprecated 条目混进 AI 快照 | `snapshot`、`build` |
| 关5 审批 | 在载体上——git PR / 分支保护，不在这个包里 | — |
| 关6 边已人审 | active 卡上任何边缺 `review: ok\|wrong` | `check`、`build`、`approve` |

## 配置 = 一个文件

`knowledger.yaml`（JSON 兼容的 YAML 子集，不引 PyYAML）：

```yaml
name: 我的知识库
blocks:
  product:     { owners: [张三], hints: [需求, 功能, PRD], default_type: Feature }
  engineering: { owners: [李四], hints: [API, 路由, Service],  default_type: Module }
  marketing:   { owners: null,   hints: [文章, 关键字],        default_type: Article }
types:
  Doc:     { block: product,     required: [name, owner, status, sources, version] }
  Feature: { block: product,     required: [name, owner, status, sources] }
  Module:  { block: engineering, required: [name, owner, status, sources] }
  Article: { block: marketing,   required: [title, owner, status, sources, reviewer] }
relations: [contains, applies_to, verified_by, cites, supersedes]
section_noise: [概述, 快速配置, 完整示例]   # 文档章节标题，不是业务对象
status_enum: [active, deprecated, draft]
common_required: [id, type, name, owner, block, status, sources]
```

`owners: null` = 无人拥有 → 关 3 拒绝所有人。哪天有人认领，填上名字即可，别的都不用动。

## 支持的格式

| 输入 | 抽取？ | 方式 |
|---|---|---|
| .md .txt .html .csv .json | ✅ 全文 | 直接 |
| .docx .xlsx | 计划中，零依赖（zipfile + xml） | 旁车 `.md` |
| .doc .pdf | 仅建卡，正文空 | 需 OCR——留了插件点（未实现） |
| 飞书 / Notion 链接 | 计划中 | 需凭证——不进核心包 |
| 图片 / 音频 / 视频 | ❌ 拒绝 | — |

## 方法 vs 实现

**方法（不要改）：** 文档优先、最小单元审核、六道机械关、recall 给材料不给规则。

**实现（撑不住了就换）：**
- 候选切分 = 3 条正则。纯散文切不出东西——故意的，那部分人自己读。
- 召回 = 中文 bigram 重叠 + 别名改写 + 一跳邻居。中文够用；有了基线再换向量。
- `graph.html` 布局 = 极简力导向，≤80 节点。再多用 Mermaid 或外部工具。

## 不做的事

- 不做拖线连边的图编辑器。审核者是领域专家不是本体工程师，拖线产生的方向错误比表单多。
- 核心包不带 OCR、飞书/Notion 抓取、向量库——每个都要凭证或依赖。插件点已留。
- 不做实时协作。git 冲突就是协作。
- 不做 OWL 推理机。九种关系类型，人审比配推理机快。

## 设计依据

最小审核单元（边 / 定义 / 树拆成三种动作）和「图看、表改」的分工，依据：Palantir Ontology Proposal 模型（按资源拆任务、逐条批、留变更记录）与 WebVOWL vs Protégé 用户研究（节点连线图帮助**非专家**更快更准地理解，专家无差别）。差异对照基于 2026-11-24 对 trikedb / myKG / AnNER / llms.txt 族的公开扫描（含各项目自述的局限，如 trikedb 基准为检索+grounding 组合效果、非官方 WebQSP 指标）。

## License

MIT
