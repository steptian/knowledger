# 分类树（人读版）

改这里不影响校验（校验读 knowledger.yaml）。用来自我解释：块 → 类型 → 实例。

```
__NAME__
├── blocks/product/      owner 张三 · Doc / Feature
├── blocks/engineering/  owner 李四 · Module
├── blocks/marketing/    无主（owners: null）· Article
├── raw/                 原样文档，只增不改
├── staging/             机器建的索引卡，待人审
├── ontology/            glossary.csv 术语表 · raw_manifest.json 指纹
├── eval/                黄金问答集
└── build/               编译产物（AI 挂 build/SNAPSHOT.md）
```

## 命名
- 实例文件名 = `{ID}.md`，ID 前缀按类型：`D-` `FEAT-` `MOD-` `ART-`
- 一个实例一个文件；deprecated 不删文件，改 status 即可
