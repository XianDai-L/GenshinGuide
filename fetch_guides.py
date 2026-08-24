# -*- coding: utf-8 -*-
"""
用安柏计划的角色指南（Genshin Wizard + aza.gg）批量生成关系映射表。

每个角色抓取以下数据块：
- 专武（Best Weapon）
- 下位替代武器（Replacement Weapon，最多 4 把）
- 推荐圣遗物（Best Artifact Set）+ 圣遗物备选（Second/Third）
- 圣遗物主词条（Main Stats Priority，原文保留，Agent 翻译）
- 玩法（Playstyle，英文原文，Agent 翻译）
- 命座使用率（aza constellationsUsage）
- 配队（Synergy teams，解析为中文角色名）

写入 data/relations.json（覆盖）。版本更新后重跑即可。

运行：python fetch_guides.py
"""

import asyncio
import io
import json
import os
import re

import ambr

OUT_FILE = "D:/GenshinGuide/data/relations.json"
SLEEP = 0.5  # 礼貌间隔

_W_NAME = re.compile(r"__([^_]+)__")


def clean_text(s):
    """清理数据源里常见的软连字符等不可见字符。"""
    return (s or "").replace("\xad", "")


def parse_weapon_names(text, en_to_id, weapons_cn):
    """从 __X__ 标记的文本提取武器中文名列表（去重保序）。"""
    names = []
    for m in _W_NAME.finditer(clean_text(text)):
        for part in m.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            wid = en_to_id.get(part)
            if wid is not None:
                nm = weapons_cn.get(wid)
                if nm and nm not in names:
                    names.append(nm)
    return names


def artifact_names(reliquary_list, reliquary_cn):
    """套装引用列表（含 custom 字符串）-> 中文套装名列表。"""
    out = []
    for x in reliquary_list or []:
        if isinstance(x, dict) and isinstance(x.get("id"), int):
            nm = reliquary_cn.get(x["id"])
            if nm and nm not in out:
                out.append(nm)
    return out


async def main():
    relations = {}
    async with ambr.AmbrAPI(lang=ambr.Language.CHS) as client_cn:
        async with ambr.AmbrAPI(lang=ambr.Language.EN) as client_en:
            chars = await client_cn.fetch_characters()
            avatar_cn = {str(c.id): c.name for c in chars}
            weapons_cn = {w.id: w.name for w in await client_cn.fetch_weapons()}
            weapons_en = {w.id: w.name for w in await client_en.fetch_weapons()}
            en_to_id = {name: wid for wid, name in weapons_en.items()}
            reliquary_cn = {s.id: s.name for s in await client_cn.fetch_artifact_sets()}

            for c in chars:
                name = c.name
                if not name:
                    continue
                try:
                    raw = await client_cn._request(
                        f"advanced/avatarGuides/{c.id}", static=True, use_cache=True
                    )
                except Exception as e:
                    print(f"{name} guide 失败: {e}")
                    await asyncio.sleep(SLEEP)
                    continue
                data = raw.get("data") or {}
                gw = data.get("gwData") or {}
                aza = data.get("azaData") or {}
                entry = {}
                builds = gw.get("builds") or []
                if builds:
                    for info in builds[0].get("info") or []:
                        iname = info.get("name", "") or ""
                        value = info.get("value")
                        rl = info.get("reliquaryList")
                        if iname.startswith("Best Weapon"):
                            best = parse_weapon_names(value, en_to_id, weapons_cn)
                            if best:
                                entry["专武"] = best[0]
                        elif iname.startswith("Replacement Weapon"):
                            repl = parse_weapon_names(value, en_to_id, weapons_cn)
                            if repl:
                                entry["下位替代武器"] = repl[:4]
                        elif iname.startswith("Best Artifact"):
                            arts = artifact_names(rl, reliquary_cn)
                            if arts:
                                entry["推荐圣遗物"] = arts
                        elif iname.startswith("Second Best Artifact"):
                            arts = artifact_names(rl, reliquary_cn)
                            if arts:
                                entry.setdefault("圣遗物备选", []).append("+".join(arts))
                        elif iname.startswith("Third Best Artifact"):
                            arts = artifact_names(rl, reliquary_cn)
                            if arts:
                                entry.setdefault("圣遗物备选", []).append("+".join(arts))
                        elif iname.startswith("Main Stats Priority"):
                            if value:
                                entry["圣遗物主词条"] = clean_text(str(value)).strip()
                    play = gw.get("playstyle")
                    if play and play.get("description"):
                        entry["玩法"] = clean_text(play["description"])[:300]
                    syn = gw.get("synergies") or {}
                    teams = syn.get("synergiestList") or syn.get("teams") or []
                    if teams:
                        team_strs = []
                        for team in teams:
                            slots = []
                            for slot in team:
                                stype = slot.get("type")
                                if stype == "normal":
                                    slots.append(avatar_cn.get(str(slot.get("id")), f"角色{slot.get('id')}"))
                                elif stype == "element":
                                    slots.append(f"任意{slot.get('element')}系")
                                elif stype == "flexible":
                                    slots.append("任意")
                            if slots:
                                team_strs.append(" + ".join(slots))
                        if team_strs:
                            entry["配队"] = team_strs
                cusage = aza.get("constellationsUsage") or {}
                if cusage:
                    entry["命座使用率"] = {k: v for k, v in sorted(cusage.items())}
                if entry:
                    relations[name] = entry
                await asyncio.sleep(SLEEP)

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with io.open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(relations, f, ensure_ascii=False, indent=2)
    stats = {}
    for v in relations.values():
        for k in v:
            stats[k] = stats.get(k, 0) + 1
    print(f"关系表生成完成：{len(relations)} 个角色")
    for k, n in stats.items():
        print(f"  {k}: {n}")


if __name__ == "__main__":
    asyncio.run(main())
