"""提示词生成层 —— 引擎无关。

所有图像/视频提示词必须收敛到本文件。将来接 ComfyUI / RunningHub / 可灵，
只改这里，上游剧本与校验层不动。
"""

from __future__ import annotations

from .schema import Character, Shot

# 镜头语言库 -> 英文提示词片段
SHOT_MAP = {
    "特写": "close-up shot",
    "大特写": "extreme close-up shot",
    "中景": "medium shot",
    "全景": "wide shot",
    "俯拍": "high angle shot",
    "仰拍": "low angle shot",
    "第一人称视角": "POV shot",
}

MOVE_MAP = {
    "推": "slow push in",
    "拉": "pull out",
    "摇": "pan",
    "跟随": "tracking follow",
    "固定": "static locked-off camera",
    "旋转": "rotating orbit shot",
}


def shot_to_english(shot: str) -> str:
    """'特写, 推' -> 'close-up shot, slow push in'"""
    parts = [p.strip() for p in shot.replace("，", ",").split(",") if p.strip()]
    out = []
    for p in parts:
        if p in SHOT_MAP:
            out.append(SHOT_MAP[p])
        elif p in MOVE_MAP:
            out.append(MOVE_MAP[p])
        else:
            out.append(p)
    return ", ".join(out)


def build_shot_prompt(shot: Shot, character: Character | None,
                      style_lock: str, scene_desc: str = "") -> str:
    """组装单镜提示词。STYLE LOCK 由库注入，不让 LLM 自由改写。"""
    seg = []
    seg.append(f"[STYLE LOCK]\n{style_lock.strip()}")
    anchor_lines = []
    if character:
        anchor_lines.append(
            f"{character.code}（{character.name}）：{character.anchor_text}")
    if scene_desc:
        anchor_lines.append(f"场景：{scene_desc}")
    if anchor_lines:
        seg.append("[视觉锚定]\n" + "\n".join(anchor_lines))
    seg.append(f"[画面]\n{shot_to_english(shot.shot)}｜{shot.action}")
    if shot.dialog and shot.dialog != "无":
        seg.append(f'[台词]\n{shot.dialog}')
    if shot.sfx and shot.sfx != "无":
        seg.append(f"[音效]\n{shot.sfx}")
    return "\n\n".join(seg)


def build_character_sheet(c: Character) -> str:
    """角色设定卡，供绘图/LoRA 训练参考。"""
    lines = [
        f"# {c.name}（{c.code}）", "",
        f"- 视觉锚点：{c.anchor_text}",
    ]
    if c.palette:
        lines.append(f"- 色卡：{c.palette}")
    if c.voice:
        lines.append(f"- 音色：{c.voice}")
    if c.trigger_word:
        lines.append(f"- LoRA 触发词：{c.trigger_word}")
    if c.lora_path:
        lines.append(f"- LoRA 路径：{c.lora_path}")
    lines.append(f"- 锚点 hash：{c.anchor_hash or '(未冻结)'}  v{c.anchor_version}")
    return "\n".join(lines) + "\n"
