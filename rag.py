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

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(ROOT, "docs")
INDEX_DIR = os.path.join(ROOT, "index")
MODELS_DIR = os.path.join(ROOT, "models")

CHUNK_SIZE = 300
CHUNK_OVERLAP = 50
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
COLLECTION_NAME = "genshin_guide"
MIN_SCORE = 0.25  # 相似度低于此值的片段视为不相关

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


def build_index(force=False):
    import chromadb

    client = chromadb.PersistentClient(path=INDEX_DIR)
    if force:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass

    docs = load_documents()
    if not docs:
        print("docs/ 目录下没有文档，未构建索引。")
        return 0

    collection = client.get_or_create_collection(COLLECTION_NAME)
    model = _get_embedder()

    ids, texts, metas = [], [], []
    for i, (text, source) in enumerate(docs):
        ids.append(f"doc_{i}")
        texts.append(text)
        metas.append({"source": source})

    print(f"正在向量化 {len(texts)} 个片段（首次需下载模型，请耐心等待）...")
    embeddings = [e.tolist() for e in model.embed(texts)]
    collection.add(ids=ids, documents=texts, metadatas=metas, embeddings=embeddings)
    print(f"索引构建完成：{len(texts)} 个片段已入库。")
    return len(texts)


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


RELATIONS_FILE = os.path.join(ROOT, "data", "relations.json")
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


def get_talent_field(content):
    """从角色档案文本中提取【天赋升级】逐级表原文。返回 str 或 None。"""
    fields = {}
    current = None
    for line in content.splitlines():
        if line.startswith("【") and "】" in line:
            end = line.find("】")
            current = line[1:end]
            fields[current] = line[end + 1:].strip()
        elif current:
            fields[current] += "\n" + line.strip()
    return fields.get("天赋升级")


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
    fields = {}
    current = None
    for line in content.splitlines():
        if line.startswith("【") and "】" in line:
            end = line.find("】")
            current = line[1:end]
            fields[current] = line[end + 1:].strip()
        elif current:
            fields[current] += "\n" + line.strip()
    if field_names:
        return {k: v for k, v in fields.items() if k in field_names}
    return fields


def _extract_fields(query, content, kind):
    """根据 query 中的字段词，从带【字段】标记的内容中提取对应字段。

    命中字段词时返回该字段内容（可多个），未命中返回 None。
    """
    fields = {}
    current = None
    for line in content.splitlines():
        if line.startswith("【") and "】" in line:
            end = line.find("】")
            current = line[1:end]
            fields[current] = line[end + 1:].strip()
        elif current:
            fields[current] += "\n" + line.strip()
    # 天赋升级特殊处理：问"天赋材料/6升7"时优先返回逐级表解析结果
    # （避免"天赋"短词把【天赋】技能描述也匹配出来）
    if kind == "角色":
        tier_rng = _talent_tier_range(query)
        is_talent_q = any(k in query for k in TALENT_UPGRADE_HINT) or (
            ("天赋" in query or "技能" in query) and tier_rng is not None
        )
        if is_talent_q and fields.get("天赋升级"):
            parsed = parse_talent_upgrade(query, fields["天赋升级"])
            return parsed or f"天赋升级：{fields['天赋升级']}"
    # 成就特殊处理：query 含成就名则返回该成就；否则返回全文
    if kind == "成就":
        for key, val in fields.items():
            if key and key in query:
                return f"{key}：{val}"
        return None
    matched = []
    for field_name, keywords in FIELD_MAP.get(kind, {}).items():
        if any(kw in query for kw in keywords):
            if field_name in fields and fields[field_name]:
                matched.append(f"{field_name}：{fields[field_name]}")
    return "\n".join(matched) if matched else None


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


def search_character(query):
    """精确匹配：问题中提及角色/武器/圣遗物实体名时，返回对应数据。

    若问题包含字段词（元素/突破/特效等），只返回对应字段；否则返回全文。
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
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
    except OSError:
        return None
    # 字段级提取：命中字段词则只返回对应字段
    field_content = _extract_fields(query, content, kind)
    if field_content:
        return field_content, fn
    return content, fn


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
        print(f"待入库片段数：{len(load_documents())}")
    else:
        print("用法：python rag.py [build|rebuild|search|count]")
