# -*- coding: utf-8 -*-
"""
成就数据层：加载成就库、解析 UIAF（Yae 导出）完成状态、管理本地手动标记。

数据来源：
- 成就库：data/achievements.json（由 fetch_ambr.py 生成，含 id / 所属辑 / 名称 / 条件 / 原石）
- 官方状态：UIAF 导入的已完成 id（权威数据源，重新导入会覆盖修正）
- 手动标记：data/achievement_manual.json（用户在助手内打勾/取消，优先级高于官方状态）
"""

import io
import json
import os
import time
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
ACH_JSON = os.path.join(ROOT, "data", "achievements.json")
MANUAL_FILE = os.path.join(ROOT, "data", "achievement_manual.json")
STATE_FILE = os.path.join(ROOT, "data", "uiaf_state.json")

MYSH_URL = "https://www.miyoushe.com/ys/search?keyword="

_ach_cache = None
_manual_cache = None
_uiaf_done = None      # 官方已完成 id 集合
_uiaf_info = {}        # {id: {"current": n}}
_state_meta = {}       # {"source": 上次导入路径, "imported_at": 时间戳}
_state_loaded = False  # 本地快照是否已尝试加载


def load_achievements():
    """加载成就库（带缓存）。"""
    global _ach_cache
    if _ach_cache is None:
        try:
            with io.open(ACH_JSON, encoding="utf-8") as f:
                _ach_cache = json.load(f)
        except (OSError, ValueError):
            _ach_cache = []
    return _ach_cache


def load_manual():
    """加载本地手动标记（带缓存）。"""
    global _manual_cache
    if _manual_cache is None:
        try:
            with io.open(MANUAL_FILE, encoding="utf-8") as f:
                _manual_cache = json.load(f)
        except (OSError, ValueError):
            _manual_cache = {}
    return _manual_cache


def save_manual():
    """持久化手动标记。"""
    os.makedirs(os.path.dirname(MANUAL_FILE), exist_ok=True)
    with io.open(MANUAL_FILE, "w", encoding="utf-8") as f:
        json.dump(_manual_cache or {}, f, ensure_ascii=False, indent=2)


def save_state(source=None):
    """把当前官方完成状态持久化到本地（下次启动免重新导入）。"""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    data = {
        "source": source or _state_meta.get("source"),
        "imported_at": int(time.time()),
        "done": sorted(_uiaf_done or set()),
        "current": {
            str(k): v.get("current", 0)
            for k, v in (_uiaf_info or {}).items() if v.get("current")
        },
    }
    with io.open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def load_state():
    """加载本地持久化的官方状态快照。返回是否成功。"""
    global _uiaf_done, _uiaf_info, _state_meta
    try:
        with io.open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False
    _uiaf_done = set(d.get("done") or [])
    _uiaf_info = {
        int(k): {"current": v} for k, v in (d.get("current") or {}).items()
    }
    _state_meta = {
        "source": d.get("source"),
        "imported_at": d.get("imported_at"),
    }
    return True


def ensure_state():
    """懒加载：确保官方状态已就绪（内存没有时读本地快照）。"""
    global _state_loaded
    if _state_loaded or _uiaf_done is not None:
        return
    _state_loaded = True
    load_state()


def state_info():
    """当前状态信息（供 UI 显示）。"""
    ensure_state()
    if _uiaf_done is None:
        return {"imported": False}
    ts = _state_meta.get("imported_at")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "未知时间"
    return {
        "imported": True,
        "done": len(_uiaf_done),
        "when": when,
        "source": _state_meta.get("source") or "",
    }


def import_uiaf(path):
    """导入 UIAF JSON，更新官方完成状态并持久化。返回 (已完成数, 总条数)。"""
    global _uiaf_done, _uiaf_info, _state_loaded, _state_meta
    with io.open(path, encoding="utf-8") as f:
        data = json.load(f)
    lst = data.get("list") or []
    done = set()
    info = {}
    for x in lst:
        aid = x.get("id")
        if aid is None:
            continue
        if x.get("status") == 3:      # 3 = 已完成
            done.add(aid)
        info[aid] = {"current": x.get("current", 0)}
    _uiaf_done = done
    _uiaf_info = info
    _state_meta = {"source": path, "imported_at": int(time.time())}
    _state_loaded = True
    save_state(path)
    return len(done), len(lst)


def is_done(aid):
    """完成状态：手动标记优先，其次 UIAF 官方状态（首次调用自动加载本地快照）。"""
    m = load_manual().get(str(aid))
    if m is not None:
        return bool(m)
    ensure_state()
    return aid in (_uiaf_done or set())


def set_done(aid, done):
    """手动标记完成/取消（写入 data/achievement_manual.json）。"""
    load_manual()[str(aid)] = bool(done)
    save_manual()


def clear_manual():
    """清空手动标记（重新导入时可调用，让官方状态生效）。"""
    global _manual_cache
    _manual_cache = {}
    save_manual()


def has_uiaf():
    """是否已有官方状态（内存或本地快照）。"""
    ensure_state()
    return _uiaf_done is not None


def counts():
    """返回 (全部, 未完成, 已完成) 数量（手动标记优先 + UIAF 官方状态）。"""
    achs = load_achievements()
    total = len(achs)
    done = sum(1 for a in achs if is_done(a.get("id")))
    return total, total - done, done


def groups(tab="all"):
    """按辑分组，返回 [(辑名, 已完成数, 总数), ...]。

    tab: all（全部） / undone（未完成） / done（已完成）
    """
    g = {}
    for a in load_achievements():
        s = a.get("set") or "其他"
        item = g.setdefault(s, {"done": 0, "total": 0})
        item["total"] += 1
        if is_done(a.get("id")):
            item["done"] += 1
    out = [(s, v["done"], v["total"]) for s, v in g.items()]
    if tab == "undone":
        out = [x for x in out if x[1] < x[2]]
    elif tab == "done":
        out = [x for x in out if x[1] >= x[2]]
    return sorted(out, key=lambda x: x[0])


def achievements_of(s, tab="all"):
    """某个辑的成就列表（带 _done 标记），按 tab 排序：未完成/已完成前置。"""
    achs = [a for a in load_achievements() if (a.get("set") or "其他") == s]
    for a in achs:
        a["_done"] = is_done(a.get("id"))
    if tab == "undone":
        achs.sort(key=lambda a: (a["_done"], a.get("id") or 0))
    elif tab == "done":
        achs.sort(key=lambda a: (not a["_done"], a.get("id") or 0))
    return achs


def card_list(only_undone=True, set_filter=None):
    """卡片模式的成就列表（已排序，稳定）。"""
    achs = []
    for a in load_achievements():
        s = a.get("set") or "其他"
        if set_filter and s != set_filter:
            continue
        done = is_done(a.get("id"))
        if only_undone and done:
            continue
        a["_done"] = done
        achs.append(a)
    achs.sort(key=lambda a: a.get("id") or 0)
    return achs


def available_sets(only_undone):
    """卡片筛选下拉的辑列表：只看未完成时，只列还有未完成成就的辑。"""
    all_sets = sorted({a.get("set") or "其他" for a in load_achievements()})
    if not only_undone:
        return all_sets
    out = []
    for s in all_sets:
        if any(not is_done(a.get("id"))
               for a in load_achievements() if (a.get("set") or "其他") == s):
            out.append(s)
    return out


def search_url(name):
    """米游社搜索 URL（关键词 URL 编码）。"""
    return MYSH_URL + urllib.parse.quote(name or "")


def icon_of(s):
    """辑图标（emoji 占位，按名称关键词匹配）。"""
    if "对决者" in s or "挑战者" in s:
        return "⚔️"
    if "天地万象" in s:
        return "🌌"
    if "元素" in s:
        return "✨"
    if "神射手" in s:
        return "🎯"
    if "尘世巡游" in s or "异世相逢" in s:
        return "🗺️"
    if "七圣召唤" in s:
        return "🃏"
    return "🏆"
