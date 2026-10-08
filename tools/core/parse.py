"""解析层：Markdown <-> 结构化对象。

- parse_ffs(md)            解析 FFS 镜头级剧本
- parse_storyboard(md)     解析 seedance 四段式分镜提示词
- parse_outline(md)        解析 20 集要点表
"""

from __future__ import annotations

import re

from .schema import (DEFAULT_SHOT_SEC, HEADER_FIELDS, Shot, EpisodeScript,
                     ShotPrompt)

HEADER_RE = re.compile(r"^#\s+([A-Z ]+?)\s*:\s*(.*)$")
SC_RE = re.compile(r"^\[SC-(\d+)\]\s*$")
FIELD_RE = re.compile(r"^([A-Z]+)\s*:\s*(.*)$")
CN_NUM = "零一二三四五六七八九十"


def parse_ffs(md: str, ep_no: int = 0) -> EpisodeScript:
    header: dict = {}
    shots: list = []
    cur: dict | None = None
    order: list = []

    for raw in md.splitlines():
        line = raw.rstrip()
        m = HEADER_RE.match(line)
        if m and m.group(1) in HEADER_FIELDS and not shots:
            header[m.group(1)] = m.group(2).strip()
            order.append(m.group(1))
            continue

        m = SC_RE.match(line.strip())
        if m:
            if cur:
                shots.append(_mk_shot(cur))
            cur = {"sc_id": f"SC-{int(m.group(1)):02d}", "seq": int(m.group(1)),
                   "fields": {}, "extra": []}
            continue

        if cur is not None:
            fm = FIELD_RE.match(line.strip())
            if fm and fm.group(1) in ("SHOT", "LOCATION", "TIME", "CHAR", "ACTION",
                                      "DIALOG", "VO", "SFX", "DUR"):
                cur["fields"][fm.group(1)] = fm.group(2).strip()
            elif line.strip() and not line.strip().startswith("---"):
                # 续行：拼到上一个字段
                if cur["fields"]:
                    last = list(cur["fields"])[-1]
                    cur["fields"][last] += " " + line.strip()
            if line.strip() and line.strip() not in ("---",) and not FIELD_RE.match(line.strip()):
                pass
        # 记录非法字段行（校验用）
        if cur is not None:
            fm2 = FIELD_RE.match(line.strip())
            if fm2 and fm2.group(1) not in ("SHOT", "LOCATION", "TIME", "CHAR",
                                            "ACTION", "DIALOG", "VO", "SFX", "DUR"):
                cur.setdefault("bad_fields", []).append(fm2.group(1))

    if cur:
        shots.append(_mk_shot(cur))

    title = header.get("TITLE", "")
    if not ep_no and "EP" in header:
        m = re.match(r"(\d+)", header["EP"])
        ep_no = int(m.group(1)) if m else 0

    ep = EpisodeScript(ep_no=ep_no, title=title, header=header, shots=shots)
    ep.header["_order"] = order
    return ep


def _mk_shot(cur: dict) -> Shot:
    f = cur["fields"]
    dur = f.get("DUR", "").strip()
    try:
        est = int(re.sub(r"[^\d]", "", dur)) if dur else DEFAULT_SHOT_SEC
    except ValueError:
        est = DEFAULT_SHOT_SEC
    return Shot(
        sc_id=cur["sc_id"], seq=cur["seq"],
        shot=f.get("SHOT", ""), location=f.get("LOCATION", ""),
        time=f.get("TIME", ""), char=f.get("CHAR", ""),
        action=f.get("ACTION", ""), dialog=f.get("DIALOG", ""),
        vo=f.get("VO", ""), sfx=f.get("SFX", ""), est_sec=est,
    )


def parse_storyboard(md: str, ep_no: int = 0) -> dict:
    """解析 seedance 四段式输出。"""
    result = {"ep_no": ep_no, "style_lock": "", "anchor": "",
              "shots": [], "voices": ""}
    section = None
    cur: dict | None = None

    for raw in md.splitlines():
        line = raw.rstrip()
        st = line.strip()
        def flush():
            nonlocal cur
            if cur:
                result["shots"].append(_mk_prompt(ep_no, cur))
                cur = None

        if st.startswith("[STYLE LOCK]"):
            flush(); section = "style"; continue
        if st.startswith("[形象视觉"):
            flush(); section = "anchor"; continue
        if st.startswith("[画面描述]"):
            flush(); section = "desc"; continue
        if st.startswith("[音色锚定]"):
            flush(); section = "voice"; continue
        if st == "HARD CUT":
            flush(); continue

        if section == "style":
            if st and not st.startswith("#") and not st.startswith("---"):
                result["style_lock"] += st + "\n"
        elif section == "anchor":
            if st and not st.startswith("---"):
                result["anchor"] += st + "\n"
        elif section == "voice":
            if st and not st.startswith("---"):
                result["voices"] += st + "\n"
        elif section == "desc":
            m = re.match(r"^镜头([一二三四五六七八九十百]+)（约(\d+)秒）", st)
            if m:
                flush()
                cur = {"no": f"镜头{m.group(1)}", "sec": int(m.group(2)),
                       "lines": [], "sfx": ""}
                continue
            if st.startswith("音效设计："):
                if cur:
                    cur["sfx"] = st[len("音效设计："):]
                continue
            if cur is not None and st and st != "---":
                cur["lines"].append(st)

    if cur:
        result["shots"].append(_mk_prompt(ep_no, cur))
    return result


def _mk_prompt(ep_no: int, cur: dict) -> ShotPrompt:
    text = "\n".join(cur["lines"]).strip()
    dialogs = re.findall(r"[“\"]([^”\"]+)[”\"]", text)
    return ShotPrompt(ep_no=ep_no, no=cur["no"], est_sec=cur["sec"],
                      text=text, sfx=cur.get("sfx", ""), dialogs=dialogs)


def parse_outline(md: str) -> list:
    """解析 20 集要点表（Markdown 表格），返回 list[dict]。"""
    rows = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or set(cells[0]) <= set("-: "):
            continue
        if cells[0] in ("集", "集数", "EP") or cells[0].startswith("---"):
            continue
        if not cells[0].isdigit():
            continue
        rows.append({
            "ep": int(cells[0]),
            "title": cells[1] if len(cells) > 1 else "",
            "event": cells[2] if len(cells) > 2 else "",
            "result": cells[3] if len(cells) > 3 else "",
            "emotion": cells[4] if len(cells) > 4 else "",
            "hook": cells[5] if len(cells) > 5 else "",
        })
    return rows
