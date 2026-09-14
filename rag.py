# -*- coding: utf-8 -*-
"""
里程碑 3：RAG 本地向量检索

流程：docs/ 目录下的 .txt/.md 攻略文档 -> 切块 -> BGE 向量化 -> Chroma 索引
检索：问题向量 -> 召回最相关片段

依赖：fastembed, chromadb
构建索引：python rag.py build
"""

import json
import os
import re
import sys
import threading

import paths

ROOT = paths.RESOURCE_DIR  # 兼容旧引用
DOCS_DIR = paths.DOCS_DIR
INDEX_DIR = paths.INDEX_DIR
MODELS_DIR = paths.MODELS_DIR

CHUNK_SIZE = 300
CHUNK_OVERLAP = 50
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
COLLECTION_NAME = "genshin_guide"
MIN_SCORE = 0.25  # 全局检索相似度低于此值的片段视为不相关
DOC_FIELD_MAX = 800  # 单个字段块入库正文上限（向量化时由模型自行截断）

# 默认回复字段：用户没有指定字段、也没命中字段词时返回这些（顺序即展示顺序）
# 角色档案中位 1817 字（max 3351），整档返回既空泛又会撞 MAX_REPLY_LEN，
# 所以只回核心 6 项；武器/圣遗物/材料不在表内 -> 默认返回全部字段（档案本身很短）
DEFAULT_FIELDS = {
    "角色": ["图标", "星级", "元素", "武器类型", "突破加成", "基础属性"],
}
# 默认回复里额外附加的「关系表字段」：这些字段不在档案里，要去 data/relations.json 取
# （如角色的专武；32 个角色没有专武数据，取不到就跳过该行）
DEFAULT_RELATION_FIELDS = {
    "角色": ["专武"],
}
# 成就是"整辑"级档案（最大 91KB / 971 条），默认只预览前 N 条
DEFAULT_ACHIEVEMENT_PREVIEW = 3

# 关系表字段（由 fetch_guides.py 生成，见 data/relations.json）
RELATION_KINDS = [
    "专武", "下位替代武器", "推荐圣遗物", "圣遗物备选",
    "圣遗物主词条", "玩法", "命座使用率", "配队",
]
# 规则层可直接使用的关系字段；"玩法"是英文原文段落，只交给 Agent 转述
RULE_RELATION_KINDS = [k for k in RELATION_KINDS if k != "玩法"]

# 关系表数据清洗：主词条等字段源数据是英文占位符，且写法**极不统一**
# （大小写混用 "Crit DMG"/"CRIT Damage"、有无 %、"Cryo DMG%" 少了 Bonus、
#   "ATK %" 中间有空格、甚至裸 "HP"/"DEF"/"EM"），
# 所以用「大小写不敏感 + 容忍空格」的正则统一处理，而不是逐个字符串替换。
_REL_SLOT_PATTERNS = [
    (r"<\s*sands\s*>", "时之沙："),
    (r"<\s*goblet\s*>", "空之杯："),
    (r"<\s*circlet\s*>", "理之冠："),
]
# 顺序重要：长模式必须在前（如 "atk\s*%" 先于裸 "atk"）
_REL_TERM_PATTERNS = [
    (r"elemental\s+mastery", "元素精通"),
    (r"energy\s+recharge", "元素充能效率"),
    (r"healing\s+bonus", "治疗加成"),
    (r"crit(?:ical)?\s+damage", "暴击伤害"),
    (r"crit\s+dmg", "暴击伤害"),
    (r"crit\s+rate", "暴击率"),
    (r"elemental\s+dmg(?:\s+bonus)?%?", "元素伤害加成"),
    (r"pyro\s+dmg(?:\s+bonus)?%?", "火元素伤害加成"),
    (r"hydro\s+dmg(?:\s+bonus)?%?", "水元素伤害加成"),
    (r"cryo\s+dmg(?:\s+bonus)?%?", "冰元素伤害加成"),
    (r"electro\s+dmg(?:\s+bonus)?%?", "雷元素伤害加成"),
    (r"anemo\s+dmg(?:\s+bonus)?%?", "风元素伤害加成"),
    (r"geo\s+dmg(?:\s+bonus)?%?", "岩元素伤害加成"),
    (r"dendro\s+dmg(?:\s+bonus)?%?", "草元素伤害加成"),
    (r"physical\s+dmg(?:\s+bonus)?%?", "物理伤害加成"),
    (r"atk\s*%", "攻击力%"),
    (r"hp\s*%", "生命值%"),
    (r"def\s*%", "防御力%"),
    (r"(?<![a-z])atk(?![a-z])", "攻击力"),
    (r"(?<![a-z])hp(?![a-z])", "生命值"),
    (r"(?<![a-z])def(?![a-z])", "防御力"),
    (r"(?<![a-z])em(?![a-z])", "元素精通"),
    # 源数据里的残词（"CRIT Rate or DMG"/"... or Rate"），兜底成可读中文
    (r"(?<![a-z])dmg(?![a-z])", "伤害加成"),
    (r"(?<![a-z])rate(?![a-z])", "暴击率"),
]

# 字段级匹配：实体类型 -> 字段名 -> 触发关键词
FIELD_MAP = {
    "角色": {
        "元素": ["元素", "什么系"],
        "武器类型": ["武器类型", "拿什么武器", "用什么武器", "什么武器"],
        "星级": ["星级", "几星", "稀有度"],
        "地区": ["地区", "哪个国家", "哪个国"],
        "突破加成": ["突破加成", "突破属性"],
        "生日": ["生日", "哪天生日"],
        "称号": ["称号"],
        "命座名": ["命座名", "命之座"],
        "所属": ["所属", "隶属", "组织", "阵营"],
        "配音": ["配音", "CV", "声优"],
        "基础属性": ["基础属性", "白值", "属性", "生命值"],
        "突破材料": ["突破", "升级材料", "材料"],
        "简介": ["简介", "介绍", "是谁", "背景"],
        "天赋升级": ["天赋升级", "天赋材料", "技能升级", "天赋等级"],
        "天赋": ["天赋", "技能", "招式"],
        "命座": ["命座", "几命"],
    },
    "武器": {
        "基础攻击力": ["基础攻击力", "白值", "攻击力"],
        "副属性": ["副属性", "副词条"],
        "特效": ["特效", "被动", "效果"],
        "星级": ["星级", "几星"],
        "武器类型": ["武器类型", "什么类型", "类型"],
        "简介": ["简介", "介绍"],
        "突破材料": ["突破", "升级材料", "材料"],
    },
    "圣遗物": {
        "2件套": ["2件", "两件"],
        "4件套": ["4件", "四件"],
        "包含": ["包含", "单件", "有哪些"],
    },
    "材料": {
        "刷取时间": ["刷取时间", "什么时候刷", "周几刷", "哪天刷", "周几", "哪天", "什么时候打"],
        "刷取秘境": ["刷取秘境", "在哪刷", "哪个秘境", "哪里刷", "在哪个副本"],
        "获取途径": ["获取途径", "去哪", "怎么获得", "怎么获取", "采集"],
        "类型": ["类型", "是什么材料"],
        "简介": ["简介", "介绍", "用途", "是什么"],
    },
}

# 客观字段词：命中这些词的问题优先走字段级精确匹配（跳过关键词库）
OBJECTIVE_KEYWORDS = [
    "元素", "武器类型", "星级", "地区", "突破加成", "生日", "称号",
    "命座名", "所属", "配音", "基础属性", "突破材料", "天赋", "命座",
    "特效", "副属性", "基础攻击力", "获取途径", "刷取时间", "件套",
]

_embedder_cache = None
_collection_cache = None
_embedder_lock = threading.Lock()
_collection_lock = threading.Lock()


def warmup():
    """后台预热向量模型与索引，避免首次问答卡 UI。失败静默。"""
    try:
        _get_embedder()
        _get_collection()
    except Exception:
        pass


def _chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    text = text.strip()
    if not text:
        return []
    chunks = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + size, n)
        chunks.append(text[start:end])
        if end >= n:
            break
        start = end - overlap
    return chunks


def parse_fields(content):
    """把带【字段】标记的档案文本解析成 {字段名: 内容}（保持出现顺序）。

    档案格式（docs/gamedata/*.txt）：
        胡桃（角色）
        【元素】火
        【天赋升级】
        2级：...
    """
    fields = {}
    current = None
    for line in content.splitlines():
        if line.startswith("【") and "】" in line:
            end = line.find("】")
            current = line[1:end]
            fields[current] = line[end + 1:].strip()
        elif current is not None:
            fields[current] += "\n" + line.strip()
    return fields


# 文件名前缀 -> 实体类型
_PREFIX_KIND = {
    "角色_": "角色",
    "武器_": "武器",
    "圣遗物_": "圣遗物",
    "材料_": "材料",
    "成就_": "成就",
}


def _entity_of(fn):
    """从档案文件名解析 (实体类型, 实体名)。"""
    base = os.path.basename(fn)
    for prefix, kind in _PREFIX_KIND.items():
        if base.startswith(prefix) and base.endswith(".txt"):
            return kind, base[len(prefix):-4]
    return "", os.path.splitext(base)[0]


def entity_of_source(source):
    """从 source（相对路径，如 gamedata/角色_胡桃.txt）解析 (实体类型, 实体名)。"""
    return _entity_of(source)


def load_documents(docs_dir=DOCS_DIR):
    """递归读取 docs 目录下所有 .txt/.md，切块返回 [(文本, 来源文件名)]。"""
    docs = []
    if not os.path.isdir(docs_dir):
        return docs
    for root, _dirs, files in os.walk(docs_dir):
        for fn in sorted(files):
            if not fn.lower().endswith((".txt", ".md")):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, docs_dir)
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            for chunk in _chunk_text(content):
                docs.append((chunk, rel))
    return docs


def _get_embedder():
    global _embedder_cache
    if _embedder_cache is None:
        with _embedder_lock:
            if _embedder_cache is None:  # double-check：避免并发重复加载模型
                from fastembed import TextEmbedding
                _embedder_cache = TextEmbedding(model_name=MODEL_NAME, cache_dir=MODELS_DIR)
    return _embedder_cache


def _get_collection():
    global _collection_cache
    if _collection_cache is None:
        with _collection_lock:
            if _collection_cache is None:  # double-check：避免并发重复连接
                import chromadb
                client = chromadb.PersistentClient(path=INDEX_DIR)
                _collection_cache = client.get_collection(COLLECTION_NAME)
    return _collection_cache


def load_field_chunks(docs_dir=DOCS_DIR):
    """把 docs 下的档案切成「字段块」——检索的最小单元。

    优先按【字段】切（保结构、不跨字段截断），无字段结构的文档回退定长切。
    返回 [(入库文本, source, meta)]，meta 含 entity / kind / field / source。
    """
    chunks = []
    if not os.path.isdir(docs_dir):
        return chunks
    for root, _dirs, files in os.walk(docs_dir):
        for fn in sorted(files):
            if not fn.lower().endswith((".txt", ".md")):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, docs_dir)
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            kind, entity = _entity_of(fn)
            fields = parse_fields(content)
            if fields:
                for field, value in fields.items():
                    value = value.strip()
                    # 【图标】只是图片链接，对问答无价值且污染回答
                    if not value or field == "图标":
                        continue
                    text = f"{entity}（{kind}）【{field}】{value[:DOC_FIELD_MAX]}"
                    chunks.append((text, rel, {
                        "entity": entity, "kind": kind, "field": field, "source": rel,
                    }))
            else:
                for piece in _chunk_text(content):
                    chunks.append((piece, rel, {
                        "entity": entity, "kind": kind, "field": "", "source": rel,
                    }))
    return chunks


def build_index(force=False, batch_size=512, embed_batch=16):
    """重建字段级向量索引。

    batch_size：外层批次（同时限制内存占用与 chroma 单次 add 上限）
    embed_batch：向量化内部批大小；默认批太大会在低内存机器上 OOM，故显式调小
    """
    import chromadb

    chunks = load_field_chunks()
    if not chunks:
        print("docs/ 目录下没有可索引内容，未构建索引。")
        return 0

    client = chromadb.PersistentClient(path=INDEX_DIR)
    if force:
        # 先确认有数据再删旧索引，避免"删了却建不起来"
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass

    collection = client.get_or_create_collection(COLLECTION_NAME)
    model = _get_embedder()

    texts = [c[0] for c in chunks]
    total = len(texts)
    print(f"正在向量化 {total} 个字段块（首次需下载模型，请耐心等待）...")
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        embeddings = [
            e.tolist() for e in model.embed(texts[start:end], batch_size=embed_batch)
        ]
        collection.add(
            ids=[f"c_{i}" for i in range(start, end)],
            documents=texts[start:end],
            metadatas=[c[2] for c in chunks[start:end]],
            embeddings=embeddings,
        )
        print(f"  已入库 {end}/{total}")
    print(f"索引构建完成：{total} 个字段块已入库。")
    return total


# 天赋升级问法提示词（区别于【天赋】技能描述）
TALENT_UPGRADE_HINT = ["天赋升级", "天赋材料", "技能升级", "天赋等级", "升天赋"]


def _talent_tier_range(query):
    """从问句中解析天赋等级区间。

    - '6升7'/'6到7'/'6→7' → (7, 7)（升到7级所需）
    - '1升10' → (2, 10)（从1级升到10级，消耗2~10级）
    - '升到8' → (8, 8)
    - 无具体等级返回 None
    """
    m = re.search(r"(\d+)\s*(?:升|到|→|->|=>|~)\s*(\d+)", query)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        lo, hi = min(a, b), max(a, b)
        return (lo + 1, hi)
    m = re.search(r"升到\s*(\d+)", query)
    if m:
        lv = int(m.group(1))
        return (lv, lv) if lv >= 2 else None
    return None


def parse_talent_upgrade(query, raw_text):
    """解析【天赋升级】逐级表，按问法返回答案文本。

    raw_text 形如："2级：xxx、yyy\n3级：..."
    无具体等级 -> 全升满总量；单级/区间 -> 对应消耗；解析失败返回 None。
    """
    tiers = {}
    for ln in raw_text.splitlines():
        ln = ln.strip()
        m = re.match(r"(\d+)\s*级[:：]\s*(.*)", ln)
        if m:
            tiers[int(m.group(1))] = m.group(2)
    if not tiers:
        return None

    def _sum_levels(lo, hi):
        total = {}
        for lv in range(lo, hi + 1):
            if lv not in tiers:
                continue
            for part in tiers[lv].split("、"):
                part = part.strip()
                if part.startswith("摩拉"):
                    total["摩拉"] = total.get("摩拉", 0) + int(part[2:])
                else:
                    name, _, cnt = part.rpartition("×")
                    if cnt:
                        total[name] = total.get(name, 0) + int(cnt)
        return total

    def _fmt(total):
        parts = [f"{k}×{v}" for k, v in total.items() if k != "摩拉"]
        mora = total.get("摩拉")
        if mora:
            parts.append(f"摩拉{mora:,}")
        return "、".join(parts)

    rng = _talent_tier_range(query)
    if rng is None:
        total = _sum_levels(2, 10)
        return "天赋全升满（2~10级）所需：\n" + _fmt(total)
    lo, hi = rng
    if lo == hi:
        return f"天赋升到{lo}级所需：\n{tiers.get(lo, '')}"
    detail = "\n".join(f"{lv}级：{tiers[lv]}" for lv in range(lo, hi + 1) if lv in tiers)
    total = _sum_levels(lo, hi)
    return f"天赋从{lo - 1}级升到{hi}级所需：\n{detail}\n合计：{_fmt(total)}"


RELATIONS_FILE = paths.RELATIONS_FILE
_relations_cache = None


def load_relations():
    """加载关系映射表（角色 -> 专武/推荐圣遗物等）。失败返回空表。"""
    global _relations_cache
    if _relations_cache is None:
        try:
            with open(RELATIONS_FILE, encoding="utf-8") as f:
                _relations_cache = json.load(f)
        except (OSError, ValueError):
            _relations_cache = {}
    return _relations_cache


def _localize_relation_text(text):
    """把关系表里的英文占位符文本转成中文可读形式。

    源数据形如 "<sands> HP% or Elemental Mastery |<goblet> Pyro DMG Bonus% |<circlet> CRIT DMG"
    -> "时之沙：生命值% / 元素精通；空之杯：火元素伤害加成；理之冠：暴击伤害"

    注意：源数据里还夹带英文注释（如 "__More than two HP% main stats are not
    recommended.__"），这类内容不做处理（属于数据源问题，见 HANDOFF）。
    """
    for pattern, zh in _REL_SLOT_PATTERNS:
        text = re.sub(pattern, zh, text, flags=re.IGNORECASE)
    text = text.replace("|", "；")
    text = re.sub(r"\s+or\s+", " / ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+of\s+", " / ", text, flags=re.IGNORECASE)  # 源数据笔误："ATK% of Elemental Mastery"
    for pattern, zh in _REL_TERM_PATTERNS:
        text = re.sub(pattern, zh, text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    return text.replace("： ", "：").replace(" ；", "；")


def get_relation(character, kind):
    """取关系表字段的值（已格式化 + 中文化），不带"XX的YY："前缀。

    角色或字段不存在返回 None。默认回复里要嵌入字段值（如「专武：护摩之杖」）时用它。
    """
    character = (character or "").replace("「", "").replace("」", "").strip()
    kind = (kind or "").strip()
    if not character or not kind:
        return None
    rel = load_relations().get(character)
    if not rel:
        return None
    val = rel.get(kind)
    if not val:
        return None
    if isinstance(val, list):
        if val and isinstance(val[0], list):
            val = "\n".join(" + ".join(str(x) for x in team) for team in val)
        else:
            val = "、".join(str(x) for x in val)
    elif isinstance(val, dict):
        if kind == "命座使用率":
            val = "、".join(f"{k}命{round(float(v) * 100)}%" for k, v in val.items())
        else:
            val = "、".join(f"{k}：{v}" for k, v in val.items())
    else:
        val = str(val)
    return _localize_relation_text(val)


def search_relation(character, kind):
    """查关系表（专武 / 下位替代武器 / 推荐圣遗物 / 主词条 / 配队 等）。

    返回可直接展示的中文文本；角色或字段不存在返回 None。
    Agent 与规则层共用本函数，保证两条链路答案一致。
    """
    val = get_relation(character, kind)
    if val is None:
        return None
    name = (character or "").replace("「", "").replace("」", "").strip()
    return f"{name}的{kind}：{val}"


def get_talent_field(content):
    """从角色档案文本中提取【天赋升级】逐级表原文。返回 str 或 None。"""
    return parse_fields(content).get("天赋升级")


def get_material_info(name, field_names=None):
    """查材料文件并提取指定字段。返回 {字段: 值} dict；找不到返回 {}。

    field_names 为 None 时返回全部字段。
    """
    gd = os.path.join(DOCS_DIR, "gamedata")
    if not os.path.isdir(gd):
        return {}
    clean = name.replace("「", "").replace("」", "")
    target = None
    for fn in os.listdir(gd):
        if fn.startswith("材料_") and fn.endswith(".txt"):
            if name in fn:
                target = fn
                break
    if not target:
        for fn in os.listdir(gd):
            if fn.startswith("材料_") and fn.endswith(".txt") and clean in fn:
                target = fn
                break
    if not target:
        return {}
    try:
        with open(os.path.join(gd, target), encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return {}
    fields = parse_fields(content)
    if field_names:
        return {k: v for k, v in fields.items() if k in field_names}
    return fields


def _default_text(fields, kind, name):
    """生成「默认回复」文本。

    - 表内有（角色）-> 只回核心字段 + 关系表字段（专武）
    - 表内没有（武器 / 圣遗物 / 材料）-> 全部字段
    统一输出 "字段：值" 格式，UI 才能识别出「图标：URL」并渲染成图片。
    缺失字段自动跳过（17 个角色没有突破加成、32 个角色没有专武）。
    """
    keys = DEFAULT_FIELDS.get(kind)
    if keys is None:
        lines = [f"{k}：{v}" for k, v in fields.items() if k]
    else:
        lines = [f"{k}：{fields[k]}" for k in keys if fields.get(k)]
    # 关系表字段（不在档案里，如角色的专武）
    for rel_key in DEFAULT_RELATION_FIELDS.get(kind, []):
        if any(line.startswith(rel_key + "：") for line in lines):
            continue
        val = get_relation(name, rel_key)
        if val:
            lines.append(f"{rel_key}：{val}")
    return "\n".join(lines) if lines else None


def _extract_fields(query, content, kind, apply_default=False, name=""):
    """按问法决定返回哪些字段。

    ① 特殊问法（天赋等级区间）
    ② 字段词命中 -> 只返回该字段（"用户特指字段"）
    ③ 成就：命中具体成就名 -> 只回该条；否则回整辑预览
    apply_default=True 时追加 ④「默认回复」：角色只回核心 6 项 + 专武；
    其余情况返回 None，表示"交给调用方返回完整档案"（= 全部字段）。
    name 为实体名，用于取关系表字段（如专武）。
    """
    fields = parse_fields(content)
    # ① 天赋升级特殊处理：问"天赋材料/6升7"时优先返回逐级表解析结果
    # （避免"天赋"短词把【天赋】技能描述也匹配出来）
    if kind == "角色":
        tier_rng = _talent_tier_range(query)
        is_talent_q = any(k in query for k in TALENT_UPGRADE_HINT) or (
            ("天赋" in query or "技能" in query) and tier_rng is not None
        )
        if is_talent_q and fields.get("天赋升级"):
            parsed = parse_talent_upgrade(query, fields["天赋升级"])
            return parsed or f"天赋升级：{fields['天赋升级']}"
    # ③ 成就：档案是整辑级（最大 985 条），必须限制长度；
    #    且必须用 parse_achievement_lines 而非 parse_fields —— 后者按成就名做 dict key，
    #    同名多档（如「动物园大亨」1/30/100 只）会互相覆盖（见坑 #31）
    if kind == "成就":
        items = parse_achievement_lines(content)
        for name, cond in items:
            if name in query:
                return f"{name}：{cond}"
        if not items:
            return None
        head = "\n".join(f"{k}：{v}" for k, v in items[:DEFAULT_ACHIEVEMENT_PREVIEW])
        return f"{head}\n（该辑共 {len(items)} 条成就，可提问具体成就名）"
    # ② 字段词命中
    matched = []
    for field_name, keywords in FIELD_MAP.get(kind, {}).items():
        if any(kw in query for kw in keywords):
            if field_name in fields and fields[field_name]:
                matched.append(f"{field_name}：{fields[field_name]}")
    if matched:
        return "\n".join(matched)
    if not apply_default:
        return None
    return _default_text(fields, kind, name)


def match_entity(query):
    """检测 query 是否提到实体名（角色/武器/圣遗物/材料/成就）。

    返回匹配的实体名（最长优先，避免子串误匹配）；未命中返回 None。
    """
    gamedata_dir = os.path.join(DOCS_DIR, "gamedata")
    if not os.path.isdir(gamedata_dir):
        return None
    candidates = []
    for fn in os.listdir(gamedata_dir):
        if not fn.endswith(".txt"):
            continue
        if fn.startswith("角色_"):
            name = fn[3:-4]
        elif fn.startswith("武器_"):
            name = fn[3:-4]
        elif fn.startswith("圣遗物_"):
            name = fn[4:-4]
        elif fn.startswith("材料_"):
            name = fn[3:-4]
        elif fn.startswith("成就_"):
            name = fn[3:-4]
        else:
            continue
        name_clean = name.replace("「", "").replace("」", "")
        if (name and name in query) or (name_clean and name_clean in query):
            candidates.append((len(name_clean), name))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def get_character_names():
    """返回 gamedata 中所有角色名（用于上下文继承的实体检测）。"""
    gamedata_dir = os.path.join(DOCS_DIR, "gamedata")
    if not os.path.isdir(gamedata_dir):
        return []
    names = []
    for fn in os.listdir(gamedata_dir):
        if fn.startswith("角色_") and fn.endswith(".txt"):
            names.append(fn[3:-4])
    return names


_ACH_LINE_RE = re.compile(r"^【(.+?)】(.*)$")


def parse_achievement_lines(content):
    """把成就辑档案解析成 [(成就名, 条件文本), ...]（保持文件顺序）。

    **不能用 `parse_fields()`**：它以字段名做 dict key，而成就是"同名多档"
    （如「动物园大亨」捕获 1 / 30 / 100 只各一条），dict 会互相覆盖，
    1845 条会被压成 1551 条。成就名本来就允许重复，所以这里逐行扫。
    """
    items = []
    for line in content.splitlines():
        m = _ACH_LINE_RE.match(line.strip())
        if m:
            name = m.group(1).strip()
            if name:
                items.append((name, m.group(2).strip()))
    return items


_achievement_index = None


def _get_achievement_index():
    """构建 成就名 -> [(所属辑, 条件), ...] 索引（惰性、进程内缓存）。

    成就是"整辑"级档案（成就_天地万象.txt 有 985 条），成就名不是文件名，
    所以按实体名匹配的 search_character() 找不到它们，必须单独建索引。
    同名多档**全部保留**（见 parse_achievement_lines）。
    """
    global _achievement_index
    if _achievement_index is None:
        index = {}
        gamedata_dir = os.path.join(DOCS_DIR, "gamedata")
        if os.path.isdir(gamedata_dir):
            for fn in sorted(os.listdir(gamedata_dir)):
                if not (fn.startswith("成就_") and fn.endswith(".txt")):
                    continue
                album = fn[3:-4]
                try:
                    with open(os.path.join(gamedata_dir, fn), encoding="utf-8") as f:
                        content = f.read()
                except OSError:
                    continue
                for name, cond in parse_achievement_lines(content):
                    index.setdefault(name, []).append((album, cond))
        _achievement_index = index
    return _achievement_index


def search_achievement(query):
    """按具体成就名查询（最长匹配优先）。返回 (content, source) 或 None。

    成就名可能互为子串，取最长命中；过短的名字（<3 字）不参与匹配，避免误命中。
    同名多档逐行全部列出（原文照录，不合并加工）。
    """
    index = _get_achievement_index()
    if not index or not query:
        return None
    best, best_len = None, 0
    for name in index:
        clean = name.replace("「", "").replace("」", "")
        if len(clean) < 3:
            continue
        if (name in query) or (clean in query):
            if len(clean) > best_len:
                best, best_len = name, len(clean)
    if best is None:
        return None
    entries = index[best]
    body = "\n".join(f"{best}：{cond}" for _album, cond in entries)
    albums = []
    for album, _cond in entries:
        if album not in albums:
            albums.append(album)
    return (
        f"{body}\n（所属成就辑：{'、'.join(albums)}）",
        os.path.join("gamedata", f"成就_{entries[0][0]}.txt"),
    )


def search_character(query, apply_default=False, field_match=True):
    """精确匹配：问题中提及角色/武器/圣遗物实体名时，返回对应数据。

    若问题包含字段词（元素/突破/特效等），只返回对应字段。
    apply_default=True（规则层用）：无字段词时按「默认回复」策略返回
    （角色 = 核心 6 项 + 专武、成就 = 整辑预览、武器/圣遗物/材料 = 全部字段）。
    apply_default=False（Agent 等调用方用）：无字段词时返回完整档案。
    field_match=False：跳过字段词匹配，直接要「默认回复」
    （用于"问专武时顺带给出武器档案"——查询串就是武器名本身）。

    返回 (content, source) 或 None。
    """
    gamedata_dir = os.path.join(DOCS_DIR, "gamedata")
    if not os.path.isdir(gamedata_dir):
        return None
    candidates = []
    for fn in os.listdir(gamedata_dir):
        if not fn.endswith(".txt"):
            continue
        if fn.startswith("角色_"):
            kind, name = "角色", fn[3:-4]
        elif fn.startswith("武器_"):
            kind, name = "武器", fn[3:-4]
        elif fn.startswith("圣遗物_"):
            kind, name = "圣遗物", fn[4:-4]
        elif fn.startswith("材料_"):
            kind, name = "材料", fn[3:-4]
        elif fn.startswith("成就_"):
            kind, name = "成就", fn[3:-4]
        else:
            continue
        # 去掉书名号再匹配（如「渔获」-> 渔获）
        name_clean = name.replace("「", "").replace("」", "")
        if (name and name in query) or (name_clean and name_clean in query):
            candidates.append((len(name_clean), kind, name, fn))
    if not candidates:
        return None
    # 选实体名最长的（避免"糖"误匹配"砂糖"这种子串问题）
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, kind, name, fn = candidates[0]
    path = os.path.join(gamedata_dir, fn)
    # source 用与向量索引一致的相对路径（gamedata/xxx.txt）
    source = os.path.join("gamedata", fn)
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
    except OSError:
        return None
    fields = parse_fields(content)
    if not fields:
        # 空壳档案（如 武器_圣缚.txt 只有一行名字），如实告知而不是只回个名字
        return f"资料库里暂无「{name}」的详细数据。", source
    if field_match:
        # 字段级提取：命中字段词则只返回对应字段；apply_default 决定无命中时的默认回复
        field_content = _extract_fields(
            query, content, kind, apply_default=apply_default, name=name
        )
    else:
        field_content = _default_text(fields, kind, name)
    if field_content:
        return field_content, source
    return content, source


def is_ready():
    """RAG 是否已就绪（向量模型与索引已加载），供观测日志标记。"""
    return _embedder_cache is not None and _collection_cache is not None


def search(query, top_k=3):
    """检索最相关片段。

    返回 ([(text, source, score), ...], error)。error 为 None 表示成功；
    未建索引或加载失败时返回原因（供日志观测，不再静默吞异常）。
    """
    if not os.path.isdir(INDEX_DIR):
        return [], None
    try:
        collection = _get_collection()
        model = _get_embedder()
        q_emb = [list(model.embed([query]))[0]]
        res = collection.query(query_embeddings=q_emb, n_results=top_k)
    except Exception as e:
        return [], str(e)

    results = []
    docs = res.get("documents") or [[]]
    metas = res.get("metadatas") or [[]]
    dists = res.get("distances") or [[]]
    for i, d in enumerate(docs[0]):
        src = metas[0][i].get("source", "") if i < len(metas[0]) else ""
        dist = dists[0][i] if i < len(dists[0]) else None
        score = round(1 - float(dist), 4) if dist is not None else 0.0
        results.append((d, src, score))
    return results, None


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "build"
    if arg in ("build", "rebuild"):
        build_index(force=(arg == "rebuild"))
    elif arg == "search":
        q = sys.argv[2] if len(sys.argv) > 2 else input("输入问题：")
        for text, src, score in search(q)[0]:
            print(f"[{src}] (相似度 {score})\n{text}\n")
    elif arg == "count":
        print(f"字段块数（实际入库单元）：{len(load_field_chunks())}")
        print(f"定长切块数（旧口径，仅参考）：{len(load_documents())}")
    else:
        print("用法：python rag.py [build|rebuild|search|count]")
