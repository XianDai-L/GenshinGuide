# AI 交接文档 · 原神攻略助手（Genshin Guide Overlay）

> 目的：让接手的 AI / 开发者 10 分钟内理解项目全貌、约定与坑，能直接开工。
> 最后更新：2026-09-11

---

## 0. 30 秒速览

**是什么**：原神游戏内悬浮窗攻略助手（PySide6）。游戏无边框窗口化下按住 Alt 释放鼠标即可操作悬浮窗，输入问题即时回答；另含「补成就模式」管理成就进度（总览 / 卡片 / 撤销）。

**怎么跑**：

```bash
pip install -r requirements.txt
python setup_data.py     # 首次：拉数据 + 建索引（约 25 分钟，需代理访问境外数据源）
python main.py           # 启动悬浮窗
```

**核心文件**：`main.py`（UI+问答流程）、`rag.py`（检索）、`agent.py`（Agent）、`achievement.py` + `achieve_ui.py`（补成就）

**当前状态**：功能完整可用；评测集 62 题 100% 通过；已开源 GitHub（数据不入库）

---

## 1. 项目定位

- **目标用户**：懂技术的原神玩家（GitHub 分发，需自行拉数据）
- **使用场景**：游戏内 / 直播时边玩边查，不打断操作
- **合规原则**：数据不入仓库，由抓取脚本从公开 API 拉取；仓库只分发代码

---

## 2. 技术栈

| 组件 | 用途 |
|---|---|
| Python 3.9+ / PySide6 (Qt6) | 悬浮窗 UI |
| FastEmbed (BGE 中文, ONNX) | 向量化（本地、离线） |
| ChromaDB | 向量索引 |
| OpenAI 兼容 LLM API | Agent function calling（DeepSeek / 通义 / GLM） |
| ambr (安柏计划 API) | 数据抓取 |

---

## 3. 架构分层

```
UI 层       main.py（悬浮窗 / 问答 / 日志面板 / 模式切换）
            achieve_ui.py（补成就面板）
   ↓
流程层      SearchWorker(QThread)：规则检索 → Agent 兜底 → 降级
   ↓
检索层      rag.py（字段级匹配 / RAG / 关系表 / 天赋解析）
            knowledge.py（元素反应等通用问答）
   ↓
Agent 层    agent.py（4 个工具 + tool calling 循环）
   ↓
LLM 层      llm.py（OpenAI 兼容，支持 tools 参数）
   ↓
数据层      fetch_ambr.py / fetch_guides.py / achievement.py
            docs/gamedata/*.txt + data/*.json + index/
   ↓
可观测      logs/qa.jsonl + 内置「日志」面板
```

---

## 4. 文件职责速查

| 文件 | 职责 | 关键函数/类 |
|---|---|---|
| `main.py` | 悬浮窗主程序、问答流程、日志面板、模式切换 | `_search_reply()` `SearchWorker.run()` `LogPanel` `OverlayWindow.switch_mode()` |
| `rag.py` | 字段级匹配、RAG 检索、关系表、天赋升级解析 | `search_character()` `search()` `parse_talent_upgrade()` `load_relations()` `is_ready()` |
| `knowledge.py` | 通用问答库（元素反应等） | `find_answer()` |
| `llm.py` | LLM 调用 | `_chat()`（支持 tools） `generate_answer()` |
| `agent.py` | Agent：4 工具 + 对话循环 | `run_agent()` `_execute_tool()` `TOOLS` `SYSTEM_PROMPT` |
| `achievement.py` | 成就数据层 | `import_uiaf()` `is_done()` `set_done()` `card_list()` `save_state()` |
| `achieve_ui.py` | 补成就 UI | `AchievePanel` `SetCard` `AchRow` `Toast` |
| `fetch_ambr.py` | 数据抓取（含 CLI 参数） | `fetch_characters/weapons/artifacts/materials/achievements` |
| `fetch_guides.py` | 关系表生成 | `main()` |
| `setup_data.py` | 一键数据准备 | `main()` |
| `eval/eval_qa.py` | 评测脚本 | `main()`（`--agent` 开关） |

---

## 5. 核心数据流

### 5.1 问答流程（四层检索，逐层降级）

```
玩家提问
  ↓ SearchWorker（QThread，不阻塞 UI）
① 字段级精确匹配  rag.search_character()
     · OBJECTIVE_KEYWORDS 命中 → 提取【字段】
     · 强命中 field  → 直接返回，不走 LLM（零成本）
     · 弱命中 full（返回完整档案）→ 下沉
② 关键词库        knowledge.find_answer()
③ RAG 向量检索    rag.search(top_k=2, MIN_SCORE=0.25)
④ Agent 兜底      agent.run_agent()（仅配置 API Key 时）
     · 把 ①③ 已检索的 context 注入 prompt，避免"有答案却答不存在"
  ↓ 全部失败 → NO_RESULT_MSG + 写日志
```

**触发策略**：`field` / `keyword` 直接答（免费、毫秒级）；`full` / `rag` / 未命中 → 交给 Agent（配了 key 时）。

### 5.2 Agent 工具（`agent.py:TOOLS`）

| 工具 | 用途 |
|---|---|
| `search_entity` | 按名查角色/武器/圣遗物/材料档案（可指定 field） |
| `get_talent_materials` | 天赋升级材料（支持 "6升7" / "1升10" 区间） |
| `get_material_schedule` | 材料周几刷 / 秘境 / 获取途径 |
| `search_relation` | 关系表：专武 / 下位替代武器 / 推荐圣遗物 / 主词条 / 配队 / 命座使用率 |

### 5.3 补成就流程

```
Yae 导出 UIAF JSON →「导入成就」→ 存快照 data/uiaf_state.json（下次启动免重新导入）
   ↓
成就库 data/achievements.json（1845 条，含 id / 辑 / 名称 / 条件 / 原石）
   ↓
完成状态 = 本地手动标记（优先） or UIAF 官方状态
   ↓
总览（辑网格）/ 辑内列表 / 卡片模式  ← 三者共享同一数据源，自动同步
   ↓
「搜攻略」→ 系统浏览器打开 米游社搜索?keyword=成就名
```

---

## 6. 数据管道（怎么更新数据）

```bash
python fetch_ambr.py                 # 全量（约 25 分钟）
python fetch_ambr.py --character     # 仅角色（含天赋升级逐级表）
python fetch_ambr.py --weapon        # 仅武器（含精炼 1~5 合并特效）
python fetch_ambr.py --achievement   # 仅成就（生成 data/achievements.json）
python fetch_ambr.py --talent-days   # 仅天赋书刷取时间（周几 + 秘境）
python fetch_guides.py               # 关系表（专武/圣遗物/配队/命座使用率）
python rag.py rebuild                # 重建 RAG 索引（数据变更后必做）
```

- **数据源**：安柏计划 `gi.yatta.moe`（公开 API，境外，需代理）；1 秒/次礼貌限速
- **版权**：数据归米哈游，**不入仓库**；客观事实数据风险低
- **规模**：角色 121 / 武器 270 / 圣遗物 63 / 材料 820 / 成就 73 辑 1845 条

---

## 7. 关键设计决策（为什么这么做）

1. **规则层优先于 LLM**：客观字段问题有唯一答案，规则层零成本、零延迟、零幻觉；LLM 只处理复杂/组合问题（约 0.03~0.08 元/问）
2. **Agent 用 function calling 而非纯 RAG**：多步跨实体问题（"胡桃怎么养"）需要 LLM 路由 + 多次工具调用，纯向量检索做不到
3. **数据不进仓库**：版权风险 + 体积（models 90MB / index 37MB）+ 敏感信息（`settings.json` 含 key）
4. **完成状态分两层**：UIAF 官方状态（权威）+ 本地手动标记（用户打勾）。重新导入可清空手动标记 → 误触有天然兜底；撤销栈 + Toast 是即时兜底
5. **用 QTextBrowser 而非 QListWidget**：QListWidget item 高度不自适应，长文本显示成省略号（见坑 #1）
6. **后台线程 + 启动预热**：检索在主线程会冻结 UI（首次加载向量模型约 7.5s）；`rag.warmup()` 后台预热 + 双重检查锁防并发重复加载
7. **关系表自动抓取而非人工维护**：Genshin Wizard 覆盖 121 角色（专武 89 / 圣遗物 107 / 配队 109），人工维护不现实
8. **日志贯穿决策链**：`logs/qa.jsonl` 记录命中层级 / 耗时 / `rag_ready` / Agent 工具参数，是排查"没找到"的唯一依据

---

## 8. 踩过的坑（AI 必读）

### Qt / UI
1. **QListWidget item 高度不自适应** → 长文本显示省略号。项目已换 QTextBrowser；若新增列表用 QListWidget，必须 `item.setSizeHint(widget.sizeHint())`
2. **自定义 QWidget 子类 QSS 背景不生效** → 必须 `setAttribute(Qt.WA_StyledBackground, True)`（`SetCard`/`AchRow`/`Toast` 已加）
3. **QSS `font-size` 不参与 fontMetrics 测量** → 测量高度要用 `widget.setFont()` 设字体，别只用 QSS
4. **`QUrl` 在 `PySide6.QtCore`，不在 QtGui**（`QDesktopServices` 在 QtGui）
5. **QTextBrowser 占位替换**：`deletePreviousChar` / `setPosition` 在 PySide6 上行为不可靠，最终用**文档文本级重建**（定位标记 → 保留前缀 → `setPlainText`）

### 数据源
6. **ambr 库 `fetch_domains()` 崩溃**：新地区 `city=8` 超出枚举 → 改用 `client._request("dailyDungeon", use_cache=True)` 拿原始数据
7. **武器名含软连字符 `\xad`**（如 `Tome of the Eter\xadnal Flow`）→ 匹配前必须 `replace("\xad", "")`
8. **配队字段名是 `synergiestList`**（不是 `teams`），位于 `gwData.synergies` 下
9. **aza 使用率数据不可靠**（胡桃第一名武器使用率仅 1.4%）→ 只用 `gwData`

### Agent / LLM
10. **LLM 会把"专武"误当 `search_entity` 的 field** → prompt 明确工具边界 + 执行器检测"字段不存在"时返回引导语
11. **规则层找到的资料没传给 Agent** → Agent 答"资料里没有"。修复：`run_agent(context=...)` 注入已检索内容
12. **`max_tokens=800` 截断长回答**（现为 1600；`main.py:MAX_REPLY_LEN` 同步为 1200）
13. **Agent 超轮数**（`max_rounds=4`）→ 设计上会汇总已查到结果，不丢信息

### 工程 / 环境
14. **PowerShell 命令行传中文会乱码** → 写临时 `.py` 脚本文件执行（项目内所有中文查询都这么做）
15. **`import main` 与 `def main()` 命名冲突** → 评测脚本用 `import main as main_mod`
16. **模块级缓存不会自动刷新**：`rag.load_relations()` / `achievement` 缓存需重启程序才生效
17. **chromadb / fastembed 并发加载** → 双重检查锁（`_embedder_lock` / `_collection_lock`）
18. **`asyncio.run(main())` 写在模块级** → fetch 脚本已加 `if __name__ == "__main__"` 保护

---

## 9. 开发约定

- 中文查询/命令一律写临时 `.py` 脚本执行，避免 PowerShell 编码问题；**用完删除临时脚本**
- 修改 `docs/gamedata` 数据后必须 `python rag.py rebuild`
- 新增数据处理逻辑优先放数据层（`rag.py` / `achievement.py`），UI 只做展示
- 新增 UI 组件注意坑 #1 / #2
- 排查"没找到"的顺序：看 `logs/qa.jsonl` 的 `hit_type` / `rag_ready` / `rag_error`
- 数据文件（`docs/` `index/` `models/` `data/*.json` `settings.json` `logs/`）不提交 git

---

## 10. 测试与验证

**评测（首选，改检索逻辑后必跑）**：

```bash
python eval/eval_qa.py          # 规则层 54 题（免费、秒级）
python eval/eval_qa.py --agent  # 追加 Agent 8 题（需 API Key）
```

评测集 `eval/eval_set.jsonl` 共 62 题，覆盖 `field` / `keyword` / `rag` / `none` / `agent` 五类。

**UI 离屏测试**（无显示器验证渲染与逻辑）：

```python
os.environ["QT_QPA_PLATFORM"] = "offscreen"
app = QApplication(sys.argv)
panel = achieve_ui.AchievePanel()
panel.grab().save("shot.png")     # 截图检查视觉
```

**数据抽样验证**：如 `rag.search_character("胡桃突破材料")` 打印字段值

---

## 11. 当前进度与后续方向

**已完成**：
- 悬浮窗问答（四层检索 + Agent 多工具）
- 多轮对话、日志与统计面板、可观测闭环
- 补成就模式（总览 / 列表 / 卡片 / 撤销 / UIAF 导入 / 持久化）
- 数据管道（成就 id、天赋逐级表、武器精炼合并、天赋书刷取时间、关系表）
- 评测集 62 题（100% 通过）、开源准备（README / LICENSE / .gitignore / setup_data）
- 界面主题统一（原神金色 + 深色，高对比）

**后续方向（按价值排序）**：
1. **规则链路补强**：关系表的"下位替代武器 / 圣遗物主词条"已抓取但未接入规则层（目前只走 Agent）
2. **RAG 优化**：加重排（rerank）/ MMR 去重；评测集扩充到 100+ 题（含错别字、口语化问法）
3. **打包 exe**（PyInstaller）：注意 fastembed / chromadb 依赖与体积
4. **体力规划 agent**：加"刷取材料计算 / 秘境排期"工具（数据基础已具备）
5. **单元测试**：`_search_reply` / `parse_talent_upgrade` / `_execute_tool` 关键函数
6. **日志轮转**：`logs/qa.jsonl` 目前无限增长

---

## 12. 环境与配置

- `requirements.txt`：PySide6 / fastembed / chromadb / requests / ambr
- `settings.json`：LLM 配置（provider / api_key / model / use_history），**不入库**，模板见 `settings.example.json`
- 支持服务商：DeepSeek / 通义千问 / 智谱 GLM（均 OpenAI 兼容）
- 数据源需代理（`gi.yatta.moe` 为境外站点）
- 大改动前建议备份 `docs/` `index/` `models/` `data/`（数据可重建但耗时，models 需重新下载）

---

## 13. 速查：路径与常量

| 项 | 值 |
|---|---|
| 数据目录 | `docs/gamedata/`（约 1350 个 txt） |
| 索引 / 模型 | `index/` `models/` |
| 关系表 | `data/relations.json`（`fetch_guides.py` 生成） |
| 成就库 | `data/achievements.json`（`fetch_ambr.py --achievement`） |
| 手动标记 | `data/achievement_manual.json` |
| UIAF 快照 | `data/uiaf_state.json` |
| 问答日志 | `logs/qa.jsonl` |
| 米游社搜索 | `https://www.miyoushe.com/ys/search?keyword={URL编码成就名}` |
| 窗口尺寸 | 380×560（问答 / 补成就同窗口切换） |
