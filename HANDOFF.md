# AI 交接文档 · 原神攻略助手（Genshin Guide Overlay）

> 目的：让接手的 AI / 开发者 10 分钟内理解项目全貌、约定与坑，能直接开工。
> 最后更新：2026-09-12

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

**当前状态**：功能完整可用；评测集 88 题 100% 通过（must/must_not 双判定）；已开源 GitHub（数据不入库）。
近期优化：**检索精准化**（关系表进规则层 / 字段块 BGE 索引 / 默认回复模板含专武）、**角色图标渲染**（异步 + 缓存 + 失败静默）、**按具体成就名查询**、窗口位置记忆、聊天气泡（助手左 / 用户右）+ 长消息不打断阅读、补成就 UI（默认卡片 / Tab 计数 / 未导入提示 / 返回引导）、取消离线实体继承。

**已知限制**（都不影响主流程，详见第 11 节）：关系表 `玩法` 数据被源端截断不可用；32/121 角色缺专武；4 个角色的主词条夹带英文注释。

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
| ChromaDB | 向量索引（字段块） |
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
检索层      rag.py（默认回复模板 / 字段级匹配 / 关系表 / 成就名 / RAG / 天赋解析）
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
| `main.py` | 悬浮窗主程序、问答流程（气泡 + 图标渲染）、日志面板、模式切换、窗口位置记忆 | `_search_reply()` `_relation_intents()` `_related_weapon_profile()` `_titled()` `ChatBubble` `IconLoader` `_split_icon()` `SearchWorker.run()` `LogPanel` `switch_mode()` `_set_achieve_mode()` `_restore_or_place()` |
| `rag.py` | 默认回复模板、字段级匹配、关系表、成就名查询、RAG 检索、天赋升级解析 | `search_character()` `search_achievement()` `search_relation()` `get_relation()` `_default_text()` `_extract_fields()` `parse_fields()` `parse_achievement_lines()` `load_field_chunks()` `build_index()` `search()` `parse_talent_upgrade()` `load_relations()` `is_ready()` |
| `knowledge.py` | 通用问答库（元素反应等） | `find_answer()` |
| `llm.py` | LLM 调用 | `_chat()`（支持 tools） `generate_answer()` |
| `agent.py` | Agent：4 工具 + 对话循环 | `run_agent()` `_execute_tool()` `TOOLS` `SYSTEM_PROMPT` |
| `achievement.py` | 成就数据层 | `import_uiaf()` `is_done()` `set_done()` `card_list()` `counts()` `save_state()` |
| `achieve_ui.py` | 补成就 UI | `AchievePanel` `SetCard` `AchRow` `Toast` `back_to_chat`（返回问答信号） |
| `fetch_ambr.py` | 数据抓取（含 CLI 参数） | `fetch_characters/weapons/artifacts/materials/achievements` `fill_achievement_params()` `iter_nodes()` |
| `fetch_guides.py` | 关系表生成 | `main()` |
| `setup_data.py` | 一键数据准备 | `main()` |
| `eval/eval_qa.py` | 评测脚本 | `main()`（`--agent` 开关） |

---

## 5. 核心数据流

### 5.1 问答流程（五层检索，逐层降级）

```
玩家提问
  ↓ SearchWorker（QThread，不阻塞 UI）
⓪ 关系表（推荐配置）  rag.search_relation()
     · 意图命中（专武 / 下位替代 / 推荐圣遗物 / 主词条 / 配队…）→ 直接答（免 LLM、毫秒级）
     · 「怎么养 / 怎么练」→ 聚合 专武 + 推荐圣遗物 + 主词条 + 配队 一起答
     · 关系表未收录 → 如实告知，不回退去答无关字段
① 档案匹配  rag.search_character(query, apply_default=True)
     · 字段词命中（元素 / 突破 / 特效…）→ 只回该字段
     · 无字段词 → 「默认回复」（见下表）→ 直接答，不走 LLM
② 关键词库        knowledge.find_answer()
③ 降级链          实体档案 → 具体成就名 → 全局 RAG
     · rag.search_achievement()：成就名是「字段名」不是文件名，必须单独反查
     · rag.search(top_k=2, MIN_SCORE=0.25)
④ Agent 兜底      agent.run_agent()（仅配置 API Key 时）
     · 把已检索的 context 注入 prompt，避免"有答案却答不存在"
  ↓ 全部失败 → NO_RESULT_MSG + 写日志
```

**默认回复策略**（`rag.DEFAULT_FIELDS` + 关系表字段 `DEFAULT_RELATION_FIELDS`，用户没指定字段时）：

| 类型 | 默认返回 |
|---|---|
| 角色 | 名字 + 图标 + 星级 + 元素 + 武器类型 + 突破加成 + 基础属性 + **专武** |
| 武器 / 圣遗物 / 材料 | 全部字段 |
| 成就 | 命中具体成就名 → 该条；只报辑名 → 前 3 条 + 「该辑共 N 条」提示 |

> 角色档案中位 **1817 字**（max 3351），整档返回既空泛又会撞 `MAX_REPLY_LEN`；默认模板只有 **111~143 字**。
> **17 个角色没有「突破加成」**（钟离、可莉、砂糖、提纳里…）、**32 个没有「专武」**（七七、夜兰、钟离、桑多涅…），缺失字段自动跳过。
> **专武不在角色档案里**（在 `data/relations.json`），由 `_default_text()` 通过 `get_relation(name, "专武")` 补进去 —— 见设计决策 #18。
> 所有默认回复统一由 `_default_text()` 生成 `字段：值` 格式，UI 才能识别出 `图标：URL` 并渲染成图片。

**问专武时附武器档案**：`_related_weapon_profile()` 只在 `kinds == ["专武"]`（用户明确就问专武）时，追加该武器的默认回复；「怎么养」这类聚合意图不展开（否则答案失去重点）。

```
Q: 胡桃的专武
【胡桃·专武】                      ← 图标渲染成武器图（护摩之杖图标）
胡桃的专武：护摩之杖

护摩之杖（武器）
星级：5星  武器类型：长柄武器  基础攻击力：608
副属性：暴击伤害 66.2%  特效：无羁的朱赤之蝶——…  简介：…  突破材料：…
```

**触发策略**：`field`（关系表 / 档案字段 / 默认回复 / 成就名）/ `keyword` 直接答（免费、毫秒级）；`rag` / 未命中 → 交给 Agent（配了 key 时）。

**Agent 兼容**：`search_character(query, apply_default=False, field_match=True)` 的两个开关**默认都是"Agent 语义"**（返回完整档案 + 保留字段词匹配），只有规则层传 `apply_default=True`；
`field_match=False` 仅用于"问专武时顺带取那把武器的默认回复"（查询串就是武器名本身，不该再走字段词匹配）。搞反会让 Agent 的 `search_entity` 只能拿到 6 项核心字段，能力退化。

**歧义兜底**：问「用什么武器」既可能问武器类型、也可能问专武 → 两者都答（`WEAPON_HINT_KEYWORDS`）。

**追问上下文**：未配置 AI 时**不做实体继承**（曾把上句实体拼到省略主语的追问前，易"串味"，已取消）；省略主语的追问需用户自己写清对象。配置 AI 时靠多轮历史理解省略。

### 5.2 Agent 工具（`agent.py:TOOLS`）

| 工具 | 用途 |
|---|---|
| `search_entity` | 按名查角色/武器/圣遗物/材料档案（可指定 field） |
| `get_talent_materials` | 天赋升级材料（支持 "6升7" / "1升10" 区间） |
| `get_material_schedule` | 材料周几刷 / 秘境 / 获取途径 |
| `search_relation` | 关系表：专武 / 下位替代武器 / 推荐圣遗物 / 主词条 / 配队 / 命座使用率（与规则层共用 `rag.search_relation()`；`玩法` 为英文原文，由 LLM 转述） |

### 5.3 补成就流程

```
Yae 导出 UIAF JSON →「导入成就」→ 存快照 data/uiaf_state.json（下次启动免重新导入）
   ↓
未导入 → 内容区显示提示页「请先导入成就」（顶部「导入成就」按钮仍可点）
   ↓
成就库 data/achievements.json（1845 条，含 id / 辑 / 名称 / 条件 / 原石）
   ↓
完成状态 = 本地手动标记（优先） or UIAF 官方状态
   ↓
默认「🃏 成就卡片」视图；可切「🗂 成就总览」（辑网格）→ 辑内列表
   · 总览 Tab 显示数量：全部(N) / 未完成(N) / 已完成(N)
   · 切回聊天：标题栏按钮变「‹ 返回问答」+ 面板内「‹ 返回问答」按钮
   ↓
「搜攻略」→ 系统浏览器打开 米游社搜索?keyword=成就名
```

**成就问答（与补成就 UI 无关的问答链路）**：

```
问「具体成就名」（如 妖鬼狂言百物语 / 「异色三连星」）
  → rag.search_achievement() 反查 73 辑 → 回「条件 + 奖励原石 + 所属辑」
问「成就辑名」（如 天地万象）
  → search_character 命中辑档案 → 预览前 3 条 +「该辑共 N 条成就，可提问具体成就名」
```

> 成就问答**不能走 `parse_fields()`**：它以成就名做 dict key，同名多档会互相覆盖。
> 已改用 `rag.parse_achievement_lines()` 逐行扫，**1845 条全部可查、同名逐行列出**（见坑 #31）。
> 抓取侧同理：`{param0}` 的数值来自原始 JSON 的 `detail.progress`，不能走 ambr 的模型（见坑 #32）。

---

## 6. 数据管道（怎么更新数据）

```bash
python fetch_ambr.py                 # 全量（约 25 分钟）
python fetch_ambr.py --character     # 仅角色（含天赋升级逐级表）
python fetch_ambr.py --weapon        # 仅武器（含精炼 1~5 合并特效）
python fetch_ambr.py --achievement   # 仅成就（{param0}→progress 还原 + 生成 data/achievements.json）
python fetch_ambr.py --talent-days   # 仅天赋书刷取时间（周几 + 秘境）
python fetch_guides.py               # 关系表（专武/圣遗物/配队/命座使用率）
python rag.py rebuild                # 重建字段级向量索引（数据变更后必做，约 1 分钟）
python rag.py count                  # 查看待入库字段块数（当前 7874）
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
5. **长文本列表用自适应高度容器**：QListWidget item 高度不自适应、长文本显示成省略号（见坑 #1）→ 补成就列表用 `setSizeHint`；聊天区已由 QTextBrowser 演进为气泡 Widget（见 #9）
6. **后台线程 + 启动预热**：检索在主线程会冻结 UI（首次加载向量模型约 7.5s）；`rag.warmup()` 后台预热 + 双重检查锁防并发重复加载
7. **关系表自动抓取而非人工维护**：Genshin Wizard 覆盖 121 角色（专武 89 / 圣遗物 107 / 配队 109），人工维护不现实
8. **日志贯穿决策链**：`logs/qa.jsonl` 记录命中层级 / 耗时 / `rag_ready` / Agent 工具参数，是排查"没找到"的唯一依据
9. **聊天气泡用 Widget 而非富文本**：Qt 富文本不支持圆角 / 背景，做不到"聊天软件"观感；改用 `QScrollArea` + `ChatBubble`（助手左深灰 / 用户右金色），顺带用「占位气泡直接更新」取代脆弱的文档文本定位（原坑 #5）
10. **长消息不打断阅读（S1 策略）**：仅当用户停在底部附近时新消息才自动跟随到底；已上滑看历史则保持位置不动，右下角浮出「↓ 新消息」按钮。用户主动发送时强制回到底部
11. **窗口位置持久化**：`data/window_state.json` 记坐标，启动校验是否落在任一屏幕可见区（拔副屏 / 换分辨率则回退默认），`moveEvent` 防抖 300ms 保存 + `closeEvent` 兜底
12. **取消离线实体继承**：曾把上句实体拼到省略主语的追问前（易"串味"），改为不做补全，追问需用户自己写清对象
13. **关系表进规则层**：专武 / 下位替代 / 推荐圣遗物 / 主词条 / 配队这些推荐类问题原先只走 Agent，**没配 Key 的用户直接答不出**。抽 `rag.search_relation()` 供规则层与 Agent 共用，离线即可精准回答；主词条源数据是英文占位符（`<sands> HP%`），统一中文化后再展示
14. **默认回复用「模板」而不是「向量排序」**：曾用字段级向量排序挑 top-3，但对泛化问题（"胡桃厉害吗"）排序结果机械且不可预期。改为**确定性的字段模板**：角色固定回核心 6 项、其他物品回全部字段、成就回整辑预览 —— 可预期、可测试、可解释。（`rank_fields()` 已删除；字段块索引仍保留给全局 RAG，检索质量仍优于原来的定长切块）
15. **评测双判定（must / must_not）**：原来"任一期望词命中即算对"，整档返回也能算通过，指标虚高（62 题 100%）。改为**必含 + 不得含**（如各字段题不得夹带「图标：」/「命座：」，否则说明又退化成整档返回），并统计过长回答比例，才能真正量化"空泛"
16. **成就名必须单独建索引**：成就是「整辑」级档案（`成就_天地万象.txt` 985 条），**成就名是字段名、不是文件名**，所以 `search_character()` 完全查不到具体成就 —— 以前只能靠向量检索"碰运气"（实测"妖鬼狂言百物语"偶尔命中、"「异色三连星」"完全查不到）。改为 `rag.search_achievement()` 建 `成就名 -> [(辑, 条件), ...]` 反查表（惰性缓存，最长匹配优先）。索引**必须用 `parse_achievement_lines()` 逐行扫**，不能用 `parse_fields()`（同名多档会覆盖，见坑 #31）
17. **图标异步渲染 + 缓存 + 失败静默**：`ChatBubble` 从正文摘出 `图标：URL` 单独用 `QLabel` 显示（正文不留 URL）。数据源在境外 → **必须**后台线程下载（同步会卡 UI 数秒）+ `data/icon_cache/<md5>.png` 落地缓存；拿不到图就隐藏图标、正文照常。没走富文本方案的原因见坑 #26
18. **默认回复里混入"关系表字段"**：角色的默认回复要带上**专武**，但专武不在角色档案里（在 `data/relations.json`）。所以 `_default_text()` 除了读档案字段，还会按 `DEFAULT_RELATION_FIELDS` 通过 `get_relation()` 补关系表字段，取不到就跳过该行（32 个角色没有专武数据）。另外「问专武时附武器档案」只在 `kinds == ["专武"]` 时展开——「怎么养」本身已聚合 4 项，再展开会让答案失去重点

---

## 8. 踩过的坑（AI 必读）

### Qt / UI
1. **QListWidget item 高度不自适应** → 长文本显示省略号。补成就列表用 `item.setSizeHint(widget.sizeHint())`；聊天区已改为气泡 Widget（不再依赖 QTextBrowser）
2. **自定义 QWidget 子类 QSS 背景不生效** → 必须 `setAttribute(Qt.WA_StyledBackground, True)`（`SetCard`/`AchRow`/`Toast` 已加）
3. **QSS `font-size` 不参与 fontMetrics 测量** → 测量高度要用 `widget.setFont()` 设字体，别只用 QSS
4. **`QUrl` 在 `PySide6.QtCore`，不在 QtGui**（`QDesktopServices` 在 QtGui）
5. ~~QTextBrowser 占位替换~~（**已被取代**）：现已改用「气泡 + 占位气泡直接更新文本」方案，不再依赖文档文本定位；此坑仅作历史记录

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

### UI / 渲染（补充）
19. **`QScrollArea` 白底**：viewport 与内容容器默认用调色板底色填充；只设 `QScrollArea{background:transparent}` 不够，需一并 `viewport().setAutoFillBackground(False)` + 内部容器透明（补成就总览曾出现白底）
20. **滚动条 `range` 异步更新**：插入控件后立即读 `maximum()` 可能仍是旧值，自动滚到底会失效 → 用 `rangeChanged` 联动 `_want_bottom` 标志补滚
21. **`QWidget.grab()` 会用调色板底色填充无背景区域**：离屏验证时看似"白底"，实为透明 → 判断背景问题要看**整窗 grab** 的 alpha（透明区 alpha=0），别被误导

### 检索 / 索引（补充）
22. **字段块 `source` 必须与实体来源一致**：索引 metadata 存的是**相对路径**（`gamedata/角色_胡桃.txt`），而 `search_character()` 原来返回**文件名** → 任何按 `source` 过滤的查询都会**静默失配**（不报错、只是"没效果"）。两处已统一为相对路径
23. **fastembed 默认批太大会 OOM**：7874 个字段块一次性 `embed()` 会在 FastGelu 节点报 `Failed to allocate memory`（约 555MB）。已改为分批（外层 512 / 内层 `batch_size=16`）；并且**先校验有数据再删旧索引**——否则"删了却建不起来"，索引直接丢失
24. **关系表数据质量**：`玩法` 是英文段落（不接入规则层，交给 Agent 转述）、`圣遗物主词条` 是英文占位符（`_localize_relation_text()` 中文化）、部分角色缺专武（如钟离）→ 缺失时如实告知"暂未收录"，**不要**回退去答无关字段
25. **推荐意图的关键词要收窄**：`培养`/`练什么` 这类裸词会把"胡桃培养材料"误判成推荐配置 → 只保留"怎么养 / 怎么练 / 怎么培养"等完整短语
26. **Qt 富文本的 `<img src="http...">` 不会联网下载**：QLabel / QTextDocument 不是浏览器，远程图会渲染成破图 → 图标只能自己下载后喂 `QPixmap`（见设计决策 #17）
27. **默认回复的文本格式必须统一**：角色走模板生成 `字段：值`，而武器/材料若直接回原始档案就是 `【图标】URL` 格式 → UI 的图标解析失配、观感也不一致。所有默认回复都要经 `_default_text()` 统一成 `字段：值`
28. **图标下载要留足超时**：首次访问境外图标域名有冷启动开销（DNS / 代理握手），`ICON_TIMEOUT=6s`；离屏测试时等待时间要**大于**它，否则会把"还没下完"误判成"下载失败"
29. **关系表英文词条写法极其不统一，别用字符串替换**：最初用 `_REL_TERM_MAP` 逐个 `str.replace`，只映射了 `Electro DMG Bonus%`，结果漏掉了 `Electro DMG Bonus`（无 %），而且源数据里还有 `Crit DMG` / `CRIT Damage` / `Cryo DMG%`（少 Bonus）/ `ATK %`（中间有空格）/ 裸 `HP`、`DEF`、`EM`。**已改为大小写不敏感 + 容忍空格的正则**（`_REL_SLOT_PATTERNS` / `_REL_TERM_PATTERNS`）；下次改这类清洗，先脚本扫一遍源数据的所有取值，不要逐个打补丁
30. **主词条里夹带源数据英文注释（4 个角色，未处理）**：夜兰 `__More than two HP% main stats are not recommended.__`、宵宫 `(vape teams only)`、绮良良 `(if on Favonius Sword)`、迪希雅 `If running her as a Solo Pyro, she will need around 300% Energy Recharge...`。这是数据源问题，不是术语翻译问题；要清掉需要决定"是否删源数据内容"，故保留现状
31. **`parse_fields()` 用 dict 存字段 → 同名条目静默覆盖**（**已修**）：成就档案里「动物园大亨」有 3 条（捕获 1 / 30 / 100 只），但 dict 的 key 是成就名 → 只留最后一条，**1845 条被压成 1551 条（丢 294 条，16%）**，最惨的 `成就_对决者·第一辑.txt` 30 条只剩 10 条。
    > 修法：**没有动 `parse_fields()`**（它被 `_extract_fields` / `get_talent_field` / `get_material_info` / `load_field_chunks` 四处调用，改返回类型会牵连建档索引），而是给成就单独加了 `parse_achievement_lines()` 逐行扫 `^【(名)】`，索引结构变成 `name -> [(辑, 条件), ...]`、同名逐行列出；`_extract_fields()` 的成就预览也换成了它。
32. **ambr 的 `AchievementDetail` 模型会丢掉 `progress` 字段**：成就描述里的 `{param0}`（163 处 / 21 个档案）数值就存在 `detail.progress`（如「融化{param0}个晶石」progress=15），但模型只声明了 `id/title/description/rewards`，Pydantic 默认忽略未声明字段 → 抓下来全是占位符。
    > 修法：`fetch_ambr.py` 的 `fetch_achievements()` 改走 `client._request("achievement")` 拿原始 JSON（同坑 #6 处理 `dailyDungeon` 的思路），再用 `fill_achievement_params()` 替换；`progress` 缺失时**保留占位符原样**（宁可露出便于排查，也不填错数值）。顺带：一次请求拿全量，去掉了逐分类 `sleep(1)`。
    > ⚠️ 回归防护：`eval/eval_qa.py` 会做「数据体检」，统计成就条件里残留的 `{param0}` 数量（应为 0）。
33. **打包后 `rfc3987_syntax` 的 `.lark` 语法文件丢失** → RAG 一加载就 `FileNotFoundError`（`_internal\rfc3987_syntax\syntax_rfc3987.lark`）。这是 chromadb 依赖链上的**纯数据包**，PyInstaller 不会自动收集。
    > 修法：`build.spec` 里把 `rfc3987_syntax` / `jsonschema_specifications` / `referencing` 一起 `collect_all`，并加进 `hiddenimports`。同类「纯数据包」漏收都会表现成"打包后某项功能静默不可用"，所以**打包后必须跑 `--selftest`**（见第 14 节），不要只看 exe 能不能启动。

34. **手写 `.vmx` 缺少 PCIe 桥接段 → "SCSI0 没有可用的 PCIe 插槽"**：日志特征是 `Device ... requested without secondary PCI slots available` 与 `[msg.pci.noslotavail] No PCIe slot available for SCSI0`。VMware 自己生成的 vmx 默认带 `pciBridge0` + `pciBridge4~7`（各 `functions = "8"`），手写时漏掉就只剩主总线上的少量插槽，轮到 SCSI 控制器时分配不到。
    > 修法：补 `pciBridge0/4/5/6/7`（`virtualDev = "pcieRootPort"`）与 `hpet0.present = "TRUE"`；并**删掉上一轮失败时被写进 vmx 的 `*.pciSlotNumber`**，强制下次开机重新分配。
35. **UEFI 下从 Windows 官方 ISO 引导不会自动进安装程序**：画面停在 `Time out.` / `> EFI Network...`。日志特征：`SECUREBOOT: Image APPROVED.` → `About to do EFI boot: EFI VMware Virtual SATA CDROM Drive (0.0)` → 约 3 秒后 `Status upon boot failure: Time out`。
    > 原因**不是"读不到盘"**（ISO 已被读出并通过 Secure Boot 校验），而是 Windows ISO 的引导程序在等 **「Press any key to boot from CD or DVD ...」**，不按键就超时退出、回退到 PXE。
    > 修法：先用鼠标点进虚拟机窗口（状态栏会提示「要将输入定向到该虚拟机，请在虚拟机内部单击或按 Ctrl+G」），开机后立刻连续按空格键。
    > 排查手法：`vmware.log` 里搜 `Guest:` / `CDROM:` / `SECUREBOOT`；要判断 ISO 是否真能 UEFI 引导，直接读字节即可 —— PVD 在扇区 16（`CD001`）、启动记录在扇区 17（`EL TORITO SPECIFICATION`）、启动目录条目里找 `platform = 0xEF` 的 UEFI 项（微软镜像的 EFI 启动镜像是 1.44MB FAT12，`MSDOS5.0` + `55AA`）。

---

## 9. 开发约定

- 中文查询/命令一律写临时 `.py` 脚本执行，避免 PowerShell 编码问题；**用完删除临时脚本**
- 修改 `docs/gamedata` 数据后必须 `python rag.py rebuild`
- 新增数据处理逻辑优先放数据层（`rag.py` / `achievement.py`），UI 只做展示
- 新增 UI 组件注意坑 #1 / #2
- 排查"没找到"的顺序：看 `logs/qa.jsonl` 的 `hit_type` / `rag_ready` / `rag_error`
- 数据文件不提交 git：`docs/` `index/` `models/` `logs/` `settings.json`、以及 `data/` 下的全部数据/缓存（`relations.json` `achievements.json` `uiaf_state.json` `window_state.json` `icon_cache/` `relations_readable.md`）

---

## 10. 测试与验证

**评测（首选，改检索逻辑后必跑）**：

```bash
python eval/eval_qa.py          # 规则层 87 题（免费、秒级）
python eval/eval_qa.py --agent  # 追加 Agent 1 题（需 API Key）
```

评测集 `eval/eval_set.jsonl` 共 88 题，覆盖 `field` / `keyword` / `rag` / `none` / `agent` 五类。
判定为 **must（必含；留空表示该题应无回答）+ must_not（不得含）双判定**，并统计"过长回答（>500 字）"比例用于量化空泛程度。
另外会做**数据体检**：统计成就条件里残留的 `{param0}` 占位符数量（应为 0，见坑 #32）。

**UI 离屏测试**（无显示器验证渲染与逻辑）：

```python
os.environ["QT_QPA_PLATFORM"] = "offscreen"
app = QApplication(sys.argv)
panel = achieve_ui.AchievePanel()
panel.grab().save("shot.png")     # 截图检查视觉
```

> 注意：`QWidget.grab()` 会给无背景区域填调色板底色（见坑 #21）；判断背景问题请 grab 整个窗口并检查 alpha（透明区 alpha=0），别被离屏截图误导。

**数据抽样验证**（临时脚本里跑，别在命令行直接传中文）：
- `rag.search_character("胡桃", apply_default=True)` → 看角色默认回复（含专武）
- `rag.search_character("胡桃突破材料")` → 只看字段词命中
- `rag.search_character("胡桃")` → Agent 语义（完整档案）
- `rag.search_achievement("动物园大亨")` → 成就名反查（注意坑 #31 的同名覆盖）
- `rag.search_relation("胡桃", "专武")` → 关系表取值

---

## 11. 当前进度与后续方向

**已完成**：
- 悬浮窗问答（五层检索 + Agent 多工具）
- 多轮对话、日志与统计面板、可观测闭环
- 补成就模式（总览 / 列表 / 卡片 / 撤销 / UIAF 导入 / 持久化）
- 数据管道（成就 id、天赋逐级表、武器精炼合并、天赋书刷取时间、关系表）
- 开源准备（README / LICENSE / .gitignore / setup_data）
- 界面主题统一（原神金色 + 深色，高对比）
- 体验优化：窗口位置记忆、聊天气泡（助手左 / 用户右）、长消息 S1 滚动策略（上滑不打断、「↓ 新消息」提示）
- 补成就 UI：默认成就卡片视图、Tab 数量、未导入提示页、返回问答引导
- 简化交互：取消未配置 AI 时的实体继承
- **检索精准化**：关系表（专武 / 下位替代 / 推荐圣遗物 / 主词条 / 配队）接入规则层；索引改为「字段块」；关系表英文数据中文化（正则）；评测改 must/must_not 双判定并扩到 88 题
  - 效果对比：正确率 76% → 100%（75 题口径），过长回答 14 → 0，整档返回命中 17 → 0
- **默认回复策略**：角色回核心 6 项 + 专武（111~143 字，替代 1817 字整档）、其他物品回全部字段、成就回整辑预览
- **问专武时附武器档案**：明确问专武 → 追加该武器全部字段 + 图标
- **角色图标渲染**：正文摘出 `图标：URL` → 后台下载 → 缓存 → `QLabel` 显示，失败静默降级
- **按具体成就名查询**：`rag.search_achievement()` 反查 73 辑（原先"「异色三连星」"完全查不到）
- **成就问答修复**：同名多档逐行全部列出（1845 条全可查，原先 1551 条且只留最后一条）；`{param0}` 由 `detail.progress` 还原（163 处）
- **关系表可读视图**：`data/relations_readable.md`，便于人工审阅数据质量
- **可运行 exe（PyInstaller onedir）**：`paths.py` 拆「只读资源 / 可写用户数据」；`build.spec` 收集 chromadb / onnxruntime / tokenizers 等原生依赖并裁剪未用 Qt；`build.py` 一键打包并把 `docs/index/models/data` 拷到 exe 同级；`--selftest` 可在冻结环境自检（见第 14 节）
- **VMware 虚拟机验证通过**：打包产物在干净 Win11 25H2（UEFI + vTPM，4C/6G/80GB）上**免装 Python 直接运行、问答正常**（虚拟机在 `D:\VM\GenshinGuide-Test\`）
- **开源合规收尾**：新增 `NOTICE`（数据来源/非官方/禁商用/侵权即删），`LICENSE` 声明「MIT 仅覆盖代码」，README 补非官方声明与「下载免安装包」入口，删掉 `fetch_*.py` / `setup_data.py` 里写死的 `D:/GenshinGuide` 绝对路径

**关系表已知数据问题**（详见 `data/relations_readable.md`，均未修）：
- `配队` 含占位符（如 `丝柯克 + 爱可菲 + 任意Water系 + 任意`）
- `玩法` 被 `fetch_guides.py` 抓取时**截断在 ~300 字**（英文段落中途断句）
- 32 / 121 角色缺专武、42 缺下位替代武器
- 4 个角色的主词条夹带英文注释（夜兰 / 宵宫 / 绮良良 / 迪希雅，见坑 #30）

**后续方向（按价值排序）**：
1. **RAG 优化**：加重排（rerank）/ MMR 去重；评测集继续扩充（错别字、省略主语追问、多意图）
2. **修复玩法截断**：`fetch_guides.py` 抓 `玩法` 时放宽长度限制（当前数据不可用，只交给 Agent）
3. **清理主词条英文注释**：4 个角色的源数据注释（需先定"是否删源数据内容"的口径）
4. **图标体验**：可加启动预热缓存，或换国内可达的图床；**无代理用户目前拿不到图**（不影响正文）
5. **关系表补全**：缺专武/下位替代的角色，目前如实回"暂未收录"，可考虑补数据源
6. **打包收尾**：图标 / Inno Setup 安装器 / 代码签名；装到 `Program Files` 时需把 `index` `models` 迁到用户目录（chroma、fastembed 要写锁文件）
7. **体力规划 agent**：加"刷取材料计算 / 秘境排期"工具（数据基础已具备）
8. **单元测试**：`_search_reply` / `parse_talent_upgrade` / `_execute_tool` / `search_achievement` 关键函数
9. **日志轮转**：`logs/qa.jsonl` 目前无限增长

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
| 索引 / 模型 | `index/`（7874 个字段块）`models/` |
| 关系表 | `data/relations.json`（`fetch_guides.py` 生成） |
| 关系表可读视图 | `data/relations_readable.md`（人工审阅用，非数据源） |
| 成就库 | `data/achievements.json`（`fetch_ambr.py --achievement`） |
| 手动标记 | `data/achievement_manual.json` |
| UIAF 快照 | `data/uiaf_state.json` |
| 窗口位置 | `data/window_state.json` |
| 图标缓存 | `data/icon_cache/<md5(URL)>.png` |
| 问答日志 | `logs/qa.jsonl` |
| 检索阈值 | `rag.MIN_SCORE=0.25`（全局 RAG）／`DEFAULT_ACHIEVEMENT_PREVIEW=3` |
| 回复上限 | `main.py:MAX_REPLY_LEN=1200`；`llm.py` `max_tokens=1600` / `temperature=0.3` |
| Agent | `max_rounds=4`；`search_character(apply_default=False)` 为默认语义 |
| 图标 | `ICON_SIZE=44px` / `ICON_TIMEOUT=6s`（境外源，失败静默） |
| 米游社搜索 | `https://www.miyoushe.com/ys/search?keyword={URL编码成就名}` |
| 窗口尺寸 | 380×560（问答 / 补成就同窗口切换） |

---

## 14. 打包（可运行 exe）

**构建环境**（装在 D 盘独立 venv，不污染系统 Python；用 `--system-site-packages` 复用已装的 PySide6/chromadb/fastembed，不重复下载）：

```bash
python -m venv --system-site-packages .buildenv
.buildenv\Scripts\python -m pip install pyinstaller
.buildenv\Scripts\python build.py      # 产物：dist/GenshinGuide/GenshinGuide.exe
```

**三个文件的职责**：

| 文件 | 作用 |
|---|---|
| `paths.py` | 统一路径：源码态=项目根；冻结态资源=exe 同级、用户数据=`%APPDATA%\GenshinGuide`（可用 `GENSHIN_GUIDE_HOME` 覆盖） |
| `build.spec` | 入口只打 `main.py`；`collect_all` 收集 chromadb / fastembed / onnxruntime / tokenizers / huggingface_hub / rfc3987_syntax 等；`excludes` 剔除抓取脚本与未用 Qt 模块 |
| `build.py` | 调 PyInstaller + 把 `docs/ index/ models/ data/{relations,achievements}.json` 拷到 exe 同级 |

**产物体积**（onedir，约 580MB）：`_internal/` 390MB（Python+Qt+依赖）、`models/` 91MB、`index/` 68MB、`docs/` 1.3MB。

**自检与排错**（打包后必做，别只看能不能启动）：

```bash
dist\GenshinGuide\GenshinGuide.exe --selftest   # 结果写 <user_dir>\selftest.txt
```
- `--selftest` 不走 UI，直接跑规则检索 + 成就反查 + RAG 检索，并打印资源路径是否都存在
- `--noconsole` 下看不到终端输出，**启动崩溃写 `<user_dir>\crash.log`**
- 冻结环境里"能启动"≠"能用"：某个依赖的数据文件漏收会让某层检索静默失效（见坑 #33）

**路径规则**（打包后与源码态不同，改代码时注意）：

| 类型 | 源码态 | 冻结态 |
|---|---|---|
| 只读资源 `docs/ index/ models/ data/relations.json data/achievements.json` | 项目根 | exe 同级（`build.py` 拷过去） |
| 可写数据 `settings.json logs/ data/window_state.json data/uiaf_state.json data/achievement_manual.json data/icon_cache/` | 项目根 | `%APPDATA%\GenshinGuide` |

> 数据放 exe 同级而非 `_internal/`，是因为 chromadb / fastembed 需要可写的资源目录。
> **未做**：安装器、图标、代码签名，以及「装到 `Program Files`（目录只读）时把 `index/models` 迁到用户目录」——当前 exe 只能在可写目录下跑。
> **重复打包**：`build.py` 会先把旧产物改名到 `dist/_prev_<时间戳>`（受限环境禁止批量删除，故不删只改名），可手动清理。

**虚拟机验证**：产物已在 VMware Workstation 17.5.1 + 干净 Windows 11 25H2（UEFI + Secure Boot + vTPM，4 vCPU / 6GB / 80GB 精简盘）上跑通，免装 Python、免装依赖，问答正常。建 VM 时有两个坑必须注意，见第 8 节 **#34**（手写 vmx 要带 PCIe 桥接段）和 **#35**（UEFI 下从 Windows ISO 引导要按键）。
