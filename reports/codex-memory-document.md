# Codex 报告：可编辑长期记忆文档

更新时间：2026-10-02  
实现状态：代码完成，已整合回原工作区，隔离与原工作区测试通过，正式运行目录文档已生成

## 范围

只实现人可看、可改的长期记忆 Markdown，不做向量、图记忆、自动压缩或 Game/Comic 集成。

## 变更

- 新增 core/memory/markdown_document.py
  - 渲染带稳定 ID 注释的 companion.md。
  - 解析人工新增、修改、删除的条目。
  - 使用 SHA-256 检测人工修改。
- 修改 core/memory/store.py
  - initialize、remember、supersede、revoke、compact_namespace 后刷新文档。
  - 自动导入 pending 的人工修改。
  - companion.md 保存稳定偏好、约束、事实和摘要。
  - views/*.md 按 namespace 保存完整 active 记忆。
  - 导入失败时保留人工文档，不用旧投影覆盖。
- 新增 tools/memory_export.py
  - 手动同步或 --force 重建 Markdown。
- 新增 tests/test_memory_markdown.py
  - 覆盖新增、修改、删除、namespace、episode 不误撤销、导入失败保护和 hash。

## 人工编辑语义

- 新增 \`- 文本\` -> 创建新记忆。
- 修改已有条目正文 -> 更新同一记忆 ID，并保留 JSONL 历史快照。
- 删除已有条目 -> revoke，不物理删除历史。
- 修改 metadata 注释里的 namespace / importance / confidence / tags -> 同步更新。
- 把条目移到另一个 \`## kind\` 标题下 -> kind 以标题为准。

## 文件

运行目录预期：

\`\`\`text
runtime/companion/memory/
├─ memory.jsonl
├─ memory.sqlite3
├─ companion.md
├─ companion.md.sha256
└─ views/
   ├─ index.md
   └─ general.md
\`\`\`

## 验证

隔离 worktree 测试：

\`\`\`powershell
python -m pytest tests/test_memory_markdown.py tests/test_memory_store.py tests/test_memory_extractor.py tests/test_companion_context.py tests/test_reading_context.py -q
\`\`\`

结果：

\`\`\`text
40 passed, 3 warnings
\`\`\`

真实 84 条记忆副本 smoke：

- 复制 memory.jsonl 到隔离目录。
- 自动生成 companion.md，约 10.9 KB。
- 生成 views/general.md 和 views/index.md。
- 人工修改一条已有记忆。
- 人工新增一条记忆。
- 再次同步后两条均能被 recall，未产生重复条目。

## 原工作区整合结果

- 原 integration worktree 测试：32 passed，3 warnings。
- 正式运行目录已生成：
  - companion.md：10,909 bytes
  - companion.md.sha256：65 bytes
  - views/general.md：24,457 bytes
  - views/index.md：120 bytes
- 生成前后 memory.jsonl 哈希一致。
- 生成前后 memory.sqlite3 哈希一致。
- 当前 active 记忆：84 条。

## 未做

- 未重启正在运行的 Companion。
- 未修改正式 memory.jsonl 或 memory.sqlite3 内容。
- 尚未做正式 Companion 重启后的启动加载验证。
- 未做向量、图记忆和自动压缩。


## 中文迁移与角色边界

- 提取提示词已改为默认输出简体中文。
- 现有 84 条 active 记忆已迁移为中文。
- 新增 memory_scope=global|character 分类。
- 运行时按当前角色写入和召回 character:<id>。
- 迁移后的边界：general 56 条，character:kurisu 2 条，character:yachiyo 27 条。
- 红莉栖与八千代的身份、关系、承诺和互动事件已隔离。
- memory.jsonl 迁移前均生成备份。
- 回归测试：36 passed, 3 warnings。


## 分角色文档

- 主文档 companion.md 只保存共享记忆，不再混入角色私有内容。
- characters/character-kurisu.md 保存红莉栖稳定私有记忆。
- characters/character-yachiyo.md 保存八千代稳定私有记忆。
- 每份可编辑文档有独立 .sha256 检测人工修改。
- views/ 保留每个 namespace 的完整 active 视图。
- 桌面文件夹：C:\Users\violet\Desktop\Amadeus-Companion-记忆。
- 回归测试：37 passed, 3 warnings。


## 论文全文保留策略

- Zotero 论文全文默认保留 6 小时。
- 到期后生成中文阅读报告，包含标题、核心问题、方法、实验/结论、限制和 Companion 补充。
- 报告写入 runtime/companion/reading/reports/<book-id>.md。
- 原始 chunks 替换为一条 reading-summary，避免全文无限增长。
- 总结失败时保留原文，下一轮重试。
- 只压缩 kind=zotero，默认不处理小说和漫画。
- 环境变量：AMADEUS_READING_RETENTION_ENABLED、AMADEUS_READING_FULLTEXT_TTL_HOURS。
- 回归测试：40 passed, 3 warnings。
