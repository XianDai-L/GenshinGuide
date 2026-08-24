# -*- coding: utf-8 -*-
"""
用 ambr-py 从安柏计划（gi.yatta.moe）拉取全量数据：
- 角色（全字段：星级/元素/武器/地区/图标/生日/称号/所属/配音/突破加成/基础属性/突破材料/天赋/命座）
- 武器（全字段：星级/类型/白值/副属性/特效/突破材料/图标）
- 圣遗物（套装效果/单件/图标）
- 成就（分类/名称/描述/奖励）

生成中文客观数据文本，写入 docs/gamedata/。拉取频率 1 秒（礼貌访问）。
运行：python fetch_ambr.py
"""

import asyncio
import io
import os
import re
import shutil
import sys

import ambr

OUT_DIR = "D:/GenshinGuide/docs/gamedata"

ELEMENT = {"Ice": "冰", "Fire": "火", "Water": "水", "Electric": "雷",
           "Wind": "风", "Rock": "岩", "Grass": "草"}
WEAPON_TYPE = {"WEAPON_SWORD_ONE_HAND": "单手剑", "WEAPON_CLAYMORE": "双手剑",
               "WEAPON_POLE": "长柄武器", "WEAPON_BOW": "弓", "WEAPON_CATALYST": "法器"}
REGION = {"MONDSTADT": "蒙德", "LIYUE": "璃月", "INAZUMA": "稻妻", "SUMERU": "须弥",
          "FONTAINE": "枫丹", "NATLAN": "纳塔", "SNEZHNAYA": "至冬", "KHAENRIAH": "坎瑞亚"}
STAT_LABEL = {"FIGHT_PROP_BASE_HP": "生命值", "FIGHT_PROP_BASE_ATTACK": "攻击力",
              "FIGHT_PROP_BASE_DEFENSE": "防御力"}
SUB_STAT_LABEL = {
    "FIGHT_PROP_CRITICAL": "暴击率", "FIGHT_PROP_CRITICAL_HURT": "暴击伤害",
    "FIGHT_PROP_HP_PERCENT": "生命值", "FIGHT_PROP_ATTACK_PERCENT": "攻击力",
    "FIGHT_PROP_DEFENSE_PERCENT": "防御力", "FIGHT_PROP_ELEMENT_MASTERY": "元素精通",
    "FIGHT_PROP_CHARGE_EFFICIENCY": "元素充能效率", "FIGHT_PROP_PHYSICAL_ADD_HURT": "物理伤害加成",
    "FIGHT_PROP_ELEC_ADD_HURT": "雷元素伤害加成", "FIGHT_PROP_FIRE_ADD_HURT": "火元素伤害加成",
    "FIGHT_PROP_WATER_ADD_HURT": "水元素伤害加成", "FIGHT_PROP_ICE_ADD_HURT": "冰元素伤害加成",
    "FIGHT_PROP_WIND_ADD_HURT": "风元素伤害加成", "FIGHT_PROP_ROCK_ADD_HURT": "岩元素伤害加成",
    "FIGHT_PROP_GRASS_ADD_HURT": "草元素伤害加成", "FIGHT_PROP_HEAL_ADD": "治疗加成",
}
SPECIAL_STAT_LABEL = {
    "FIGHT_PROP_CRITICAL_HURT": "暴击伤害", "FIGHT_PROP_CRITICAL": "暴击率",
    "FIGHT_PROP_HP_PERCENT": "生命值", "FIGHT_PROP_ATTACK_PERCENT": "攻击力",
    "FIGHT_PROP_DEFENSE_PERCENT": "防御力", "FIGHT_PROP_ELEMENT_MASTERY": "元素精通",
    "FIGHT_PROP_CHARGE_EFFICIENCY": "元素充能效率", "FIGHT_PROP_HEAL_ADD": "治疗加成",
    "FIGHT_PROP_PHYSICAL_ADD_HURT": "物理伤害加成",
}
BASE_STAT_IDS = {"FIGHT_PROP_BASE_HP", "FIGHT_PROP_BASE_ATTACK", "FIGHT_PROP_BASE_DEFENSE"}


def safe_name(s):
    for ch in '\\/:*?"<>|':
        s = s.replace(ch, "")
    return s.strip()


def dedup(seq):
    seen = []
    for x in seq:
        if x and x not in seen:
            seen.append(x)
    return seen


def short(text):
    if not text:
        return ""
    return text.replace("\n", "").replace("\r", "")


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _tokenize_desc(text):
    """把描述中的数值替换为占位符，并收集数值列表。"""
    tokens = []

    def _rep(m):
        tokens.append(m.group(0))
        return "\x00"

    return _NUM_RE.sub(_rep, text), tokens


def format_affix(affix_name, ups):
    """把精炼 1~5 档特效合并成紧凑格式（如 16%/20%/24%/28%/32%）。

    - 所有档文本结构一致：纵向合并数值，相同值只显示单个，如"持续8秒"
    - 无数值或结构不一致：降级为精1~精5 逐行展示，保证不丢信息
    """
    descs = [u.get("description", "") or "" for u in (ups or [])]
    descs = [d for d in descs if d]
    if not descs:
        return ""
    parsed = [_tokenize_desc(d) for d in descs]
    templates = [p[0] for p in parsed]
    token_lists = [p[1] for p in parsed]
    n = len(token_lists[0])
    same_template = len(set(templates)) == 1 and all(len(t) == n for t in token_lists)
    if same_template and n == 0:
        # 无数值且各档相同：直接返回第一条
        return f"{affix_name}——{descs[0]}" if affix_name else descs[0]
    if same_template:
        # 纵向合并：第 j 个数值跨档拼接 a/b/c/d/e，全部相同则只显示 a
        cols = []
        for j in range(n):
            vals = [tl[j] for tl in token_lists]
            cols.append(vals[0] if len(set(vals)) == 1 else "/".join(vals))
        merged = templates[0]
        for col in cols:
            merged = merged.replace("\x00", col, 1)
        merged = merged.rstrip("。.；;，, ") or merged
        return f"{affix_name}——{merged}" if affix_name else merged
    # 降级：结构不一致，逐行展示精1~5
    lines = []
    if affix_name:
        lines.append(f"{affix_name}（精1~5）")
    for i, d in enumerate(descs, 1):
        lines.append(f"精{i}：{d}")
    return "\n".join(lines)


def format_stat(ptype, val):
    if ("PERCENT" in ptype or "CRITICAL" in ptype or "ADD_HURT" in ptype
            or "HEAL" in ptype or "CHARGE_EFFICIENCY" in ptype):
        return f"{val * 100:.1f}%"
    return str(round(val))


async def fetch_characters(client, mat_name):
    chars = await client.fetch_characters()
    count = 0
    for c in chars:
        try:
            detail = await client.fetch_character_detail(c.id)
            d = detail.model_dump()
        except Exception as e:
            print(f"角色 {c.name} 失败: {e}")
            await asyncio.sleep(1)
            continue
        name = d.get("name", "")
        info = d.get("info") or {}
        up = d.get("upgrade") or {}
        lines = [f"{name}（角色）"]
        if d.get("icon"):
            lines.append(f"【图标】{d['icon']}")
        if d.get("rarity"):
            lines.append(f"【星级】{d['rarity']}星")
        if d.get("element"):
            lines.append(f"【元素】{ELEMENT.get(d['element'], d['element'])}")
        if d.get("weapon_type"):
            lines.append(f"【武器类型】{WEAPON_TYPE.get(d['weapon_type'], d['weapon_type'])}")
        if d.get("region"):
            lines.append(f"【地区】{REGION.get(d['region'], d['region'])}")
        # 突破加成（满突破）
        special = ""
        for p in reversed(up.get("promotes") or []):
            for ad in (p.get("add_stats") or []):
                ad_id = ad.get("id", "")
                if ad_id not in BASE_STAT_IDS and ad_id in SPECIAL_STAT_LABEL:
                    special = f"{SPECIAL_STAT_LABEL[ad_id]} {format_stat(ad_id, ad.get('value', 0))}"
                    break
            if special:
                break
        if special:
            lines.append(f"【突破加成】{special}")
        bd = d.get("birthday")
        if bd and bd.get("month"):
            lines.append(f"【生日】{bd['month']}月{bd['day']}日")
        if info.get("title"):
            lines.append(f"【称号】{info['title']}")
        if info.get("constellation"):
            lines.append(f"【命座名】{info['constellation']}")
        if info.get("native"):
            lines.append(f"【所属】{info['native']}")
        for cv in (info.get("cv") or []):
            if cv.get("lang") == "CHS" and cv.get("va"):
                lines.append(f"【配音】{cv['va']}")
                break
        # 基础属性（1级）
        stats = []
        for bs in up.get("base_stats") or []:
            label = STAT_LABEL.get(bs.get("prop_type"), "")
            val = bs.get("init_value")
            if label and val is not None:
                stats.append(f"{label}{round(val)}")
        if stats:
            lines.append("【基础属性】" + "、".join(stats))
        if info.get("detail"):
            lines.append(f"【简介】{info['detail']}")
        # 突破材料（promotes.cost_items 聚合）
        mat_ids = set()
        for p in up.get("promotes") or []:
            for ci in (p.get("cost_items") or []):
                mid = ci.get("id")
                if mid in mat_name:
                    mat_ids.add(mid)
        mat_names = dedup([mat_name[mid] for mid in sorted(mat_ids)])
        if mat_names:
            lines.append("【突破材料】" + "、".join(mat_names))
        # 天赋
        t_list = []
        for t in (d.get("talents") or []):
            tname = t.get("name", "")
            tdesc = short(t.get("description", ""))
            if tname:
                t_list.append(f"{tname}：{tdesc}" if tdesc else tname)
        if t_list:
            lines.append("【天赋】")
            lines.extend(t_list)
        # 天赋升级材料（逐级表 2级~10级，同角色所有主动天赋消耗一致，取第一个）
        t_upgrade = []
        for t in d.get("talents") or []:
            ups = t.get("upgrades") or []
            if not ups:
                continue
            for up in ups:
                lv = up.get("level")
                if not lv or lv < 2 or lv > 10:
                    continue
                parts = []
                for ci in (up.get("cost_items") or []):
                    mid = ci.get("id")
                    cnt = ci.get("amount", 0)
                    if mid in mat_name and cnt:
                        parts.append(f"{mat_name[mid]}×{cnt}")
                if up.get("mora_cost"):
                    parts.append(f"摩拉{up['mora_cost']}")
                if parts:
                    t_upgrade.append((lv, "、".join(parts)))
            break  # 所有主动天赋消耗相同，只需一个
        if t_upgrade:
            t_upgrade.sort(key=lambda x: x[0])
            lines.append("【天赋升级】")
            for lv, detail in t_upgrade:
                lines.append(f"{lv}级：{detail}")
        # 命座
        c_list = []
        for i, cn in enumerate((d.get("constellations") or []), 1):
            cname = cn.get("name", "")
            cdesc = short(cn.get("description", ""))
            if cname:
                c_list.append(f"{i}命·{cname}：{cdesc}" if cdesc else f"{i}命·{cname}")
        if c_list:
            lines.append("【命座】")
            lines.extend(c_list)

        with io.open(os.path.join(OUT_DIR, f"角色_{safe_name(name)}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        count += 1
        await asyncio.sleep(1)
    return count


async def fetch_weapons(client, c90, mat_name):
    weapons = await client.fetch_weapons()
    count = 0
    for w in weapons:
        name = w.name
        wtype = WEAPON_TYPE.get(w.type, w.type)
        lines = [f"{name}（武器）"]
        try:
            wd = await client.fetch_weapon_detail(w.id)
            wdd = wd.model_dump()
            if wdd.get("icon"):
                lines.append(f"【图标】{wdd['icon']}")
            if w.rarity:
                lines.append(f"【星级】{w.rarity}星")
            if wtype:
                lines.append(f"【武器类型】{wtype}")
            up = wdd.get("upgrade") or {}
            max_add = 0
            for p in up.get("promotes", []):
                for ad in (p.get("add_stats") or []):
                    if ad.get("id") == "FIGHT_PROP_BASE_ATTACK":
                        max_add = max(max_add, ad.get("value", 0))
            for bs in up.get("base_stats") or []:
                ptype = bs.get("prop_type", "")
                ival = bs.get("init_value")
                gtype = bs.get("growth_type")
                if ival is None or gtype not in c90:
                    continue
                m90 = c90[gtype]
                if ptype == "FIGHT_PROP_BASE_ATTACK":
                    lines.append(f"【基础攻击力】{round(ival * m90 + max_add)}")
                elif ptype in SUB_STAT_LABEL:
                    lines.append(f"【副属性】{SUB_STAT_LABEL[ptype]} {format_stat(ptype, ival * m90)}")
            affix = wdd.get("affix") or {}
            affix_text = format_affix(affix.get("name", ""), affix.get("upgrades"))
            if affix_text:
                lines.append(f"【特效】{affix_text}")
            desc = wdd.get("description", "")
            if desc:
                lines.append(f"【简介】{desc}")
            # 突破材料
            mat_ids = set()
            for p in up.get("promotes", []):
                for ci in (p.get("cost_items") or []):
                    if ci.get("id") in mat_name:
                        mat_ids.add(ci.get("id"))
            mat_names = dedup([mat_name[mid] for mid in sorted(mat_ids)])
            if mat_names:
                lines.append("【突破材料】" + "、".join(mat_names))
        except Exception:
            pass
        with io.open(os.path.join(OUT_DIR, f"武器_{safe_name(name)}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        count += 1
        await asyncio.sleep(1)
    return count


async def fetch_artifacts(client):
    sets = await client.fetch_artifact_sets()
    count = 0
    for s in sets:
        sd = s.model_dump()
        name = sd.get("name", "")
        lines = [f"{name}（圣遗物套装）"]
        if sd.get("icon"):
            lines.append(f"【图标】{sd['icon']}")
        for i, af in enumerate(sd.get("affix_list") or []):
            effect = af.get("effect", "")
            if effect:
                need = "2" if i == 0 else ("4" if i == 1 else "?")
                lines.append(f"【{need}件套】{effect}")
        try:
            detail = await client.fetch_artifact_set_detail(s.id)
            dd = detail.model_dump()
            arts = dd.get("artifacts") or []
            piece_names = [a.get("name", "") for a in arts if a.get("name")]
            if piece_names:
                lines.append("【包含】" + "、".join(piece_names))
        except Exception:
            pass
        with io.open(os.path.join(OUT_DIR, f"圣遗物_{safe_name(name)}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        count += 1
        await asyncio.sleep(1)
    return count


async def fetch_materials(client):
    mats = await client.fetch_materials()
    count = 0
    for m in mats:
        try:
            md = (await client.fetch_material_detail(m.id)).model_dump()
        except Exception:
            await asyncio.sleep(1)
            continue
        name = md.get("name", "")
        if not name:
            continue
        lines = [f"{name}（材料）"]
        if md.get("icon"):
            lines.append(f"【图标】{md['icon']}")
        if md.get("type"):
            lines.append(f"【类型】{md['type']}")
        sources = [s.get("name", "") for s in (md.get("sources") or []) if s.get("name") and s.get("name") != "前往采集"]
        if sources:
            lines.append("【获取途径】" + "、".join(sources))
        if md.get("description"):
            lines.append(f"【简介】{md['description']}")
        with io.open(os.path.join(OUT_DIR, f"材料_{safe_name(name)}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        count += 1
        await asyncio.sleep(1)
    return count


async def fetch_achievements(client):
    cats = await client.fetch_achievement_categories()
    count = 0
    for cat in cats:
        cd = cat.model_dump()
        cat_name = cd.get("name", "")
        if not cat_name:
            continue
        lines = [f"{cat_name}（成就）"]
        total = 0
        for ach in cd.get("achievements") or []:
            for det in ach.get("details") or []:
                title = det.get("title", "")
                desc = short(det.get("description", ""))
                primos = sum(r.get("amount", 0) for r in (det.get("rewards") or []))
                if title:
                    lines.append(f"【{title}】{desc}" + (f"（奖励：{primos}原石）" if primos else ""))
                    total += 1
        with io.open(os.path.join(OUT_DIR, f"成就_{safe_name(cat_name)}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        count += total
        await asyncio.sleep(1)
    return count


async def fetch_talent_book_days(client, mat_name):
    """更新天赋书材料的【刷取时间】和【刷取秘境】。

    依赖 dailyDungeon API（ambr 库的 Domains 模型对新区 city=8 校验失败，
    这里直接用原始接口数据绕过）。返回更新的文件数。
    """
    raw = await client._request("dailyDungeon", use_cache=True)
    data = raw.get("data") or {}
    DAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    DAY_KEY = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    name_to_id = {m_name: m_id for m_id, m_name in mat_name.items()}
    mat_days = {}      # id -> set(周几)
    mat_domain = {}    # id -> 秘境名
    for idx, day in enumerate(DAY_KEY):
        day_map = data.get(day) or {}
        for dom in (day_map.values() if isinstance(day_map, dict) else day_map):
            for r in dom.get("reward") or []:
                mat_days.setdefault(r, set()).add(DAY_CN[idx])
                mat_domain.setdefault(r, dom.get("name", ""))
    count = 0
    for fn in os.listdir(OUT_DIR):
        if not (fn.startswith("材料_") and fn.endswith(".txt")):
            continue
        name = fn[3:-4]
        if not any(k in name for k in ("的教导", "的指引", "的哲学")):
            continue
        mid = name_to_id.get(name)
        if mid is None or mid not in mat_days:
            continue
        path = os.path.join(OUT_DIR, fn)
        text = io.open(path, encoding="utf-8").read()
        if "【刷取时间】" in text:
            continue
        days = "、".join(sorted(mat_days[mid], key=DAY_CN.index))
        new = text.rstrip() + "\n" + f"【刷取时间】{days}"
        if mat_domain.get(mid):
            new += f"\n【刷取秘境】{mat_domain[mid]}"
        new += "\n"
        io.open(path, "w", encoding="utf-8").write(new)
        count += 1
    return count


async def main():
    only_weapon = "--weapon" in sys.argv
    only_character = "--character" in sys.argv
    only_talent_days = "--talent-days" in sys.argv
    incremental = any((only_weapon, only_character, only_talent_days))
    if incremental:
        os.makedirs(OUT_DIR, exist_ok=True)
    else:
        if os.path.isdir(OUT_DIR):
            shutil.rmtree(OUT_DIR)
        os.makedirs(OUT_DIR)

    async with ambr.AmbrAPI(lang=ambr.Language.CHS) as client:
        mats = await client.fetch_materials()
        mat_name = {m.id: m.name for m in mats}
        curve = await client.fetch_weapon_curve()
        c90 = curve.get("90", {}).get("curveInfos", {})

        if only_weapon:
            n = await fetch_weapons(client, c90, mat_name)
            print(f"武器: {n}")
            print("完成（仅武器）")
            return
        if only_character:
            n = await fetch_characters(client, mat_name)
            print(f"角色: {n}")
            print("完成（仅角色）")
            return
        if only_talent_days:
            n = await fetch_talent_book_days(client, mat_name)
            print(f"天赋书刷取时间更新: {n} 个")
            print("完成（仅刷取时间）")
            return
        n = await fetch_characters(client, mat_name)
        print(f"角色: {n}")
        n = await fetch_weapons(client, c90, mat_name)
        print(f"武器: {n}")
        n = await fetch_artifacts(client)
        print(f"圣遗物: {n}")
        n = await fetch_materials(client)
        print(f"材料: {n}")
        n = await fetch_achievements(client)
        print(f"成就: {n} 条")
    print("全部完成")


if __name__ == "__main__":
    asyncio.run(main())
