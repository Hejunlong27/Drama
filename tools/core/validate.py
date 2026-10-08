"""校验层：G1 格式门，E001-E011 全自动。

设计原则：脚本只查「可穷举的形式错误」。
语义漂移（比如这集角色换了红衣）查不出来，靠锚点冻结 + 分镜阶段注入锚定行来防。
"""

from __future__ import annotations

import re

from .schema import (ABSTRACT_WORDS, CODE_TOKEN_WHITELIST, HEADER_FIELDS,
                     LEVEL_ERROR, LEVEL_WARN, MAX_EP_SEC, MAX_EP_SHOTS,
                     MAX_SHOT_SEC, MIN_EP_SEC, MIN_EP_SHOTS, MIN_SHOT_SEC,
                     ALL_SHOT_FIELDS, Issue, sha1)

FIELD_LINE_RE = re.compile(r"^([A-Z]+)\s*:", re.M)
CODE_TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")
QUOTED_RE = re.compile(r"[“\"]([^”\"]+)[”\"]")
SENT_SPLIT_RE = re.compile(r"[。！？!?…]+")


# ---------------------------------------------------------------- 剧本校验

def validate_script(conn, project: str, ep, raw_md: str) -> list:
    issues: list = []
    known_chars = {r["code"] for r in conn.execute(
        "SELECT code FROM characters WHERE project=?", (project,))}
    known_scenes = {r["code"] for r in conn.execute(
        "SELECT code FROM scenes WHERE project=?", (project,))}
    # 中文剧本的 ACTION 里写的是角色中文名，不是大写代号，一并纳入可识别集合
    known_names = {r["name"] for r in conn.execute(
        "SELECT name FROM characters WHERE project=?", (project,)) if r["name"]}
    recognizable = (known_chars | known_scenes | known_names) - {""}

    issues += _check_header(ep)
    issues += _check_char_anchor(conn, project, ep)
    issues += _check_codes(ep, known_chars, known_scenes)
    issues += _check_custom_fields(raw_md)
    issues += _check_sc_seq(ep)
    issues += _check_duration(ep)
    issues += _check_dialog(ep)
    issues += _check_vo(ep)
    issues += _check_action(ep, recognizable)
    return issues


def _check_header(ep) -> list:
    out = []
    order = ep.header.get("_order", [])
    present = [f for f in order if f in HEADER_FIELDS]
    for f in HEADER_FIELDS:
        if f not in ep.header:
            out.append(Issue("E001", LEVEL_ERROR, "HEADER", f"缺 HEADER 字段：{f}"))
    if present != list(HEADER_FIELDS) and len(present) == len(HEADER_FIELDS):
        out.append(Issue("E001", LEVEL_ERROR, "HEADER",
                         f"HEADER 顺序错误：当前 {present}"))
    return out


def _parse_char_header(text: str) -> dict:
    """'LIN_A: 锚点; LIN_B: 锚点' -> {'LIN_A': '锚点'}"""
    out = {}
    for part in (text or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _check_char_anchor(conn, project, ep) -> list:
    out = []
    declared = _parse_char_header(ep.header.get("CHAR", ""))
    for code, anchor in declared.items():
        row = conn.execute(
            "SELECT anchor_text, anchor_hash FROM characters WHERE project=? AND code=?",
            (project, code)).fetchone()
        if not row:
            continue  # E003 已报未登记
        if sha1(anchor) != row["anchor_hash"]:
            out.append(Issue("E002", LEVEL_ERROR, f"HEADER#CHAR:{code}",
                             f"锚点与库不一致\n  剧本：{anchor}\n  库内：{row['anchor_text']}"))
    # 集内出场但未在 HEADER 声明的
    for s in ep.shots:
        if s.char and s.char != "无" and s.char not in declared:
            out.append(Issue("E002", LEVEL_ERROR, s.sc_id,
                             f"角色 {s.char} 出场但 HEADER 未声明锚点"))
    return out


def _check_codes(ep, known_chars: set, known_scenes: set) -> list:
    out = []
    for s in ep.shots:
        if s.char and s.char != "无" and s.char not in known_chars:
            out.append(Issue("E003", LEVEL_ERROR, s.sc_id,
                             f"CHAR 引用未登记代号：{s.char}"))
        if s.location and s.location not in known_scenes:
            out.append(Issue("E003", LEVEL_ERROR, s.sc_id,
                             f"LOCATION 引用未登记代号：{s.location}"))
        for tok in set(CODE_TOKEN_RE.findall(s.action)):
            if tok in CODE_TOKEN_WHITELIST:
                continue
            if tok not in known_chars and tok not in known_scenes:
                out.append(Issue("E003", LEVEL_WARN, s.sc_id,
                                 f"ACTION 中的疑似未登记代号：{tok}"))
    return out


def _check_custom_fields(raw_md: str) -> list:
    out = []
    for i, line in enumerate(raw_md.splitlines(), 1):
        m = FIELD_LINE_RE.match(line.strip())
        if not m:
            continue
        if m.group(1) in ALL_SHOT_FIELDS:
            continue
        if line.strip().startswith("#"):
            continue
        out.append(Issue("E004", LEVEL_ERROR, f"L{i}",
                         f"非法字段行：{m.group(1)}:（只允许 {'/'.join(ALL_SHOT_FIELDS)}）"))
    return out


def _check_sc_seq(ep) -> list:
    out = []
    seqs = [s.seq for s in ep.shots]
    if seqs != list(range(1, len(seqs) + 1)):
        out.append(Issue("E005", LEVEL_ERROR, "全篇",
                         f"SC 编号不连续：{seqs}"))
    return out


def _check_duration(ep) -> list:
    out = []
    n = len(ep.shots)
    total = ep.total_sec
    if not (MIN_EP_SHOTS <= n <= MAX_EP_SHOTS):
        out.append(Issue("E006", LEVEL_ERROR, "全篇",
                         f"镜头数 {n} 超出 {MIN_EP_SHOTS}-{MAX_EP_SHOTS} 镜区间"))
    if not (MIN_EP_SEC <= total <= MAX_EP_SEC):
        out.append(Issue("E006", LEVEL_ERROR, "全篇",
                         f"总时长 {total} 秒超出 {MIN_EP_SEC}-{MAX_EP_SEC} 秒区间"))
    for s in ep.shots:
        if not (MIN_SHOT_SEC <= s.est_sec <= MAX_SHOT_SEC):
            out.append(Issue("E006", LEVEL_ERROR, s.sc_id,
                             f"单镜 {s.est_sec} 秒超出 {MIN_SHOT_SEC}-{MAX_SHOT_SEC} 秒"))
    return out


def _check_dialog(ep) -> list:
    out = []
    for s in ep.shots:
        if not s.dialog or s.dialog == "无":
            continue
        quoted = QUOTED_RE.findall(s.dialog)
        body = "".join(quoted) if quoted else s.dialog.strip('“”"')
        sents = [x for x in SENT_SPLIT_RE.split(body) if x.strip()]
        if len(sents) > 2:
            out.append(Issue("E007", LEVEL_ERROR, s.sc_id,
                             f"对白 {len(sents)} 句，超过 2 句上限"))
        for x in sents:
            if len(x.strip()) > 20:
                out.append(Issue("E007", LEVEL_ERROR, s.sc_id,
                                 f"单句 {len(x.strip())} 字超过 20 字：{x.strip()}"))
    return out


def _check_vo(ep) -> list:
    out = []
    for s in ep.shots:
        if not s.vo or s.vo == "无":
            continue
        if "NARRATOR:" not in s.vo and "(V.O.):" not in s.vo and "：" not in s.vo[:12]:
            out.append(Issue("E008", LEVEL_ERROR, s.sc_id,
                             f"VO 缺说话者前缀：{s.vo}"))
    return out


def _check_action(ep, known: set) -> list:
    out = []
    for s in ep.shots:
        if not s.action.strip():
            out.append(Issue("E009", LEVEL_ERROR, s.sc_id, "ACTION 为空"))
            continue
        if not any(c in s.action for c in known if c):
            out.append(Issue("E009", LEVEL_ERROR, s.sc_id,
                             "ACTION 未包含任何已登记代号或角色名（无法定位主体）"))
        for w in ABSTRACT_WORDS:
            if w in s.action:
                out.append(Issue("E009", LEVEL_ERROR, s.sc_id,
                                 f"ACTION 含抽象词「{w}」，改为具体动作/表情"))
    return out


# ------------------------------------------------------------ 分镜校验

def validate_storyboard(conn, project: str, ep, sb: dict) -> list:
    issues: list = []
    row = conn.execute("SELECT style_lock, style_lock_hash FROM projects WHERE slug=?",
                       (project,)).fetchone()
    if row:
        # 分镜的 STYLE LOCK 是展开后的中文段落，不能逐字比对；
        # 只要求它原样包含项目锁定的风格常量（防止 LLM 自由改写视觉基调）
        sl = sb.get("style_lock", "")
        fingerprint = (row["style_lock"] or "").strip()[:60]
        if fingerprint and fingerprint not in sl:
            issues.append(Issue("E011", LEVEL_ERROR, "STYLE LOCK",
                                "分镜 STYLE LOCK 未原样包含项目锁定的风格常量"))
    script_dialogs = []
    for s in ep.shots:
        if s.dialog and s.dialog != "无":
            script_dialogs += QUOTED_RE.findall(s.dialog)
    sb_dialogs = []
    for p in sb.get("shots", []):
        sb_dialogs += p.dialogs
    for d in script_dialogs:
        if d not in sb_dialogs:
            issues.append(Issue("E010", LEVEL_ERROR, "分镜", f"台词缺失或被改写：{d}"))
    total = sum(p.est_sec for p in sb.get("shots", []))
    if sb.get("shots") and not (MIN_EP_SEC <= total <= MAX_EP_SEC):
        issues.append(Issue("E006", LEVEL_ERROR, "分镜",
                            f"分镜总时长 {total} 秒超出 {MIN_EP_SEC}-{MAX_EP_SEC} 秒"))
    for p in sb.get("shots", []):
        if not (MIN_SHOT_SEC <= p.est_sec <= MAX_SHOT_SEC):
            issues.append(Issue("E006", LEVEL_ERROR, p.no,
                                f"分镜单镜 {p.est_sec} 秒超出 {MIN_SHOT_SEC}-{MAX_SHOT_SEC} 秒"))
    return issues


# ------------------------------------------------------------ 报告

def render_report(project: str, ep_no: int, issues: list, extra: str = "") -> str:
    errs = [i for i in issues if i.level == LEVEL_ERROR]
    warns = [i for i in issues if i.level == LEVEL_WARN]
    lines = [f"# 校验报告 · {project} · EP{ep_no:02d}", "",
             f"- ERROR：**{len(errs)}**", f"- WARN：**{len(warns)}**",
             f"- 结论：{'[PASS] 通过' if not errs else '[FAIL] 未通过，禁止进入下一环节'}", ""]
    if extra:
        lines += [extra, ""]
    if not issues:
        lines.append("无问题。")
        return "\n".join(lines)
    lines += ["| 码 | 级别 | 位置 | 说明 |", "|---|---|---|---|"]
    for i in issues:
        msg = i.message.replace("\n", "<br>").replace("|", "\\|")
        lines.append(f"| {i.code} | {i.level} | {i.where} | {msg} |")
    return "\n".join(lines) + "\n"
