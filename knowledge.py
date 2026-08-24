# -*- coding: utf-8 -*-
"""
通用问答库（关键词匹配）

原角色攻略已移除：所有角色统一使用安柏计划拉取的客观数据（docs/gamedata/），
保证热门角色与非热门角色的回答一致（都由检索层提供）。

本模块只保留通用游戏知识问答（元素反应、树脂、深渊等）。

匹配逻辑：问题命中 GENERAL_QA 关键词则返回对应答案，否则返回 None，
由调用方降级到字段级精确匹配 / RAG 检索。
"""

GENERAL_QA = [
    {
        "keywords": ["元素反应", "反应有哪些", "反应大全"],
        "answer": "元素反应速查：\n蒸发：火+水（火打水1.5倍，水打火2倍）\n融化：火+冰（火打冰2倍，冰打火1.5倍）\n感电：水+雷（持续伤害）\n超载：火+雷（爆炸，火伤）\n冻结：水+冰（定身）\n超导：冰+雷（降物抗）\n扩散：风+水/火/雷/冰（传播元素）\n结晶：岩+水/火/雷/冰（生成护盾晶片）\n绽放：草+水（生成草原核）\n超绽放：草原核+雷（追踪弹）\n烈绽放：草原核+火（范围爆炸）\n激化：草+雷（超激化/蔓激化，雷伤/草伤强化）\n燃烧：草+火（持续燃烧）",
    },
    {"keywords": ["蒸发"], "answer": "蒸发：火+水。火打水伤害1.5倍，水打火伤害2倍。"},
    {"keywords": ["融化"], "answer": "融化：火+冰。火打冰伤害2倍，冰打火伤害1.5倍。"},
    {"keywords": ["感电"], "answer": "感电：水+雷。造成持续雷元素伤害。"},
    {"keywords": ["超载"], "answer": "超载：火+雷。引发爆炸，造成火元素范围伤害。"},
    {"keywords": ["冻结"], "answer": "冻结：水+冰。将敌人冻结定身。"},
    {"keywords": ["超导"], "answer": "超导：冰+雷。降低敌人物理抗性。"},
    {"keywords": ["扩散"], "answer": "扩散：风+水/火/雷/冰。传播对应元素，造成扩散伤害。"},
    {"keywords": ["结晶"], "answer": "结晶：岩+水/火/雷/冰。生成对应元素的护盾晶片。"},
    {"keywords": ["超绽放"], "answer": "超绽放：草原核+雷。生成追踪敌人的弹体，造成草元素伤害。"},
    {"keywords": ["烈绽放"], "answer": "烈绽放：草原核+火。引发范围爆炸，造成草元素伤害。"},
    {"keywords": ["绽放"], "answer": "绽放：草+水。生成草原核。"},
    {"keywords": ["超激化"], "answer": "超激化：草+雷。提升雷元素伤害。"},
    {"keywords": ["蔓激化"], "answer": "蔓激化：草+雷。提升草元素伤害。"},
    {"keywords": ["激化"], "answer": "激化：草+雷。包括超激化（提升雷伤）和蔓激化（提升草伤）。"},
    {"keywords": ["燃烧"], "answer": "燃烧：草+火。持续燃烧，造成火元素伤害。"},
    {
        "keywords": ["树脂", "体力", "原粹", "刷什么", "刷哪个", "先刷"],
        "answer": "树脂（体力）使用建议：\n1. 优先刷主C的圣遗物（45级后圣遗物本必出金）\n2. 天赋材料、武器突破材料\n3. 周本 Boss（首领）\n4. 地脉之花（金币/经验书）\n前期：先练一队，资源集中别分散。",
    },
    {
        "keywords": ["深渊", "深境螺旋", "螺旋", "满星"],
        "answer": "深境螺旋配队思路：\n常规体系：主C + 副C/增伤 + 辅助 + 生存（盾/奶）\n常用队：雷神国家队、万达国际、永冻队、胡桃队、草队。\n两队角色别冲突，注意元素破盾。",
    },
]


def find_answer(question, last_char=None):
    """通用问答匹配。返回 (answer, None, None)；未命中返回 (None, None, None)。

    last_char 参数保留以兼容调用方（上下文继承已交由检索层处理）。
    """
    q = question.strip()
    best = None
    best_score = 0
    best_kw_len = 0
    for item in GENERAL_QA:
        score = 0
        kw_len = 0
        for kw in item["keywords"]:
            if kw in q:
                score += 1
                kw_len = max(kw_len, len(kw))
        # 命中数优先，其次命中的关键词更长更精确（如"超绽放"优先于"绽放"）
        if score > best_score or (score == best_score and kw_len > best_kw_len):
            best_score = score
            best_kw_len = kw_len
            best = item
    if best is not None and best_score > 0:
        return best["answer"], None, None
    return None, None, None
