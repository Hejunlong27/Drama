"""转换层：两个关键适配，不做就串不起来。

T1  world_bible_to_char_anchors()   parallel-world-writer 的 world-bible 零视觉字段
                                    -> 补出 FFS 需要的 code / anchor_text / palette / voice
T2  ffs_to_seedance_script()        seedance 解析规则只认「场1｜内景 X - 夜」和「代号："台词"」
                                    -> 不认 FFS 的 ACTION: 字段行，必须转译
"""

from __future__ import annotations

import re

from .schema import Character, Scene, EpisodeScript


def world_bible_to_char_anchors(wb: dict) -> list:
    """从 world-bible.json 抽取角色 + 视觉锚点。

    world-bible 期望结构（本项目扩展后）：
      {"characters": [{"name":..., "code":..., "visual_anchor":...,
                       "palette":..., "voice":...}],
       "scenes":     [{"code":..., "desc":..., "int_ext":..., "time":...}]}
    缺 code 时按姓名首字生成拼音占位，缺 visual_anchor 时报错（不能猜）。
    """
    chars = []
    for i, c in enumerate(wb.get("characters", []), 1):
        code = c.get("code") or ""
        if not code:
            code = f"CH{i:02d}"
        anchor = c.get("visual_anchor") or c.get("anchor_text") or ""
        if not anchor:
            raise ValueError(f"角色 {c.get('name')} 缺 visual_anchor，无法冻结锚点")
        chars.append(Character(
            code=code.upper(), name=c.get("name", code),
            anchor_text=anchor.strip(),
            palette=c.get("palette", ""), voice=c.get("voice", ""),
            trigger_word=c.get("trigger_word", ""),
            lora_path=c.get("lora_path", ""),
        ))
    return chars


def world_bible_to_scenes(wb: dict) -> list:
    scenes = []
    for i, s in enumerate(wb.get("scenes", []), 1):
        code = (s.get("code") or f"SC{i:02d}").upper()
        desc = s.get("desc", "").strip()
        if not desc:
            continue
        scenes.append(Scene(code=code, desc=desc,
                            int_ext=s.get("int_ext", "内景"),
                            time=s.get("time", "夜")))
    return scenes


def ffs_to_seedance_script(ep: EpisodeScript, scenes: dict) -> str:
    """FFS -> seedance 可读的普通剧本。

    输出形态：
        第1集：觉醒

        场1｜内景 公寓 - 夜
        LIN_A 站在窗前，手指无意识地摩挲着胸牌边缘。
        LIN_A："这不是我的记忆。"
    """
    out = [f"第{ep.ep_no}集：{ep.title}", ""]
    seq = 0
    last_loc = None
    for s in ep.shots:
        if s.location != last_loc:
            seq += 1
            last_loc = s.location
            sc = scenes.get(s.location)
            if sc:
                loc_name = _short(sc["desc"])
                head = f"场{seq}｜{sc['int_ext']} {loc_name} - {s.time or sc['time']}"
            else:
                head = f"场{seq}｜内景 {s.location} - {s.time}"
            out.append(head)
        action = s.action.strip()
        if action:
            out.append(action)
        if s.dialog and s.dialog != "无":
            speaker = s.char if s.char and s.char != "无" else "角色"
            out.append(f'{speaker}：{s.dialog}')
        if s.vo and s.vo != "无":
            out.append(_vo_line(s.vo))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _vo_line(vo: str) -> str:
    """VO 已带说话者前缀时原样输出，只把 ASCII 冒号换成中文冒号。"""
    v = vo.strip()
    if re.match(r"^(NARRATOR|[^：]{1,10}( \(V\.O\.\))?)\s*:", v):
        return re.sub(r"^([^：]{1,20}?)\s*:\s*", r"\1：", v)
    return f"NARRATOR：{v}"


def _short(desc: str, n: int = 12) -> str:
    d = re.split(r"[，,。;；]", desc.strip())[0]
    return d[:n] if d else desc[:n]
