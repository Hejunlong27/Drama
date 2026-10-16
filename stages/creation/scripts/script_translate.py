#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""script_translate.py —— 剧本/小说**转译**（多语种）：转译工作单 + 语言质检

定位
----
用户给一部小说或剧本 + 指定目标语种（西语/英语/日语/韩语/葡语/泰语/印尼语/越南语/中文），
本环节**先转译、再质检**，通过后才交给下游 ④剧本改写（s3）。**转译只换语言与本地化表达，
不增删情节、不改人设、不改钩子**。

分工（与全仓一致：脚本管确定性，LLM 管创作）
--------------------------------------------
    脚本：语种注册表读取、源语言判定、分段、字数/语速预算、术语表、质检（确定性）
    LLM ：真正的转译与本地化改写（按 translation-brief.md 的指令）

命令
----
    python script_translate.py langs                                  # 支持的语种
    python script_translate.py detect "text"                          # 源语言判定
    python script_translate.py plan --to es [--from auto] [--write]    # 转译工作单 + brief
    python script_translate.py check --to es                          # 质检（残留/翻译腔/术语/预算）
    python script_translate.py glossary init | add --src X --target Y | list
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _common as C  # noqa: E402

REF_DIR = pathlib.Path(__file__).resolve().parents[1] / "references"
REGISTRY = REF_DIR / "translation-languages.json"

EXIT_FAIL = 1
EXIT_GATE = 4          # 关卡未过（转译稿质检不通过 → 不许进下游）

SEG_TARGET_CHARS = 2000        # 无场景标记时的分段粒度
LATIN_CHARS_PER_WORD = 5.2     # 拉丁语系平均词长（兜底；registry 有 chars_per_word 时以它为准）


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
def registry() -> dict:
    obj = C.read_json(REGISTRY)
    if not isinstance(obj, dict) or not obj.get("languages"):
        raise SystemExit("[FAIL] 语种注册表不可用：%s" % REGISTRY)
    return obj


def resolve_lang(code_or_alias: str) -> tuple:
    """别名/短码 → (完整 code, 语种 dict)。"""
    q = str(code_or_alias or "").strip().lower()
    reg = registry()
    if q in reg["languages"]:
        return q, reg["languages"][q]
    for code, d in reg["languages"].items():
        if q == code.split("-")[0].lower():
            return code, d
        if q == str(d.get("label", "")).lower() or q == str(d.get("native_name", "")).lower():
            return code, d
        for al in d.get("aliases") or []:
            if q == str(al).lower():
                return code, d
    raise SystemExit("[FAIL] 不支持的语种：%s（支持：%s）"
                     % (code_or_alias, "、".join(reg["languages"])))


def short_code(code: str) -> str:
    return code.split("-")[0].lower()


# ---------------------------------------------------------------------------
# 源语言判定
# ---------------------------------------------------------------------------
def detect_language(text: str) -> tuple:
    """返回 (code, 置信度 0-1, 依据)。不确定时置信度低，调用方须问用户。"""
    t = str(text or "")
    if not t.strip():
        return None, 0.0, "空文本"
    reg = registry()
    counts = {}
    for code, d in reg["languages"].items():
        pat = d.get("script_markers")
        n = len(re.findall(pat, t)) if pat else 0
        counts[code] = n
    total = len(re.findall(r"\S", t)) or 1

    # 1) CJK 内部先分开：假名 → ja；谚文 → ko；纯汉字 → zh
    kana = len(re.findall(r"[\u3040-\u30ff]", t))
    hangul = len(re.findall(r"[\uac00-\ud7af]", t))
    han = len(re.findall(r"[\u4e00-\u9fff]", t))
    if hangul > 0 and hangul / total > 0.15:
        return "ko-KR", min(0.95, hangul / total * 3), "谚文占比 %d/%d" % (hangul, total)
    if kana > 0 and kana / total > 0.05:
        return "ja-JP", min(0.95, kana / total * 4), "假名占比 %d/%d" % (kana, total)
    if han > 0 and han / total > 0.30:
        return "zh-CN", min(0.95, han / total * 2), "汉字占比 %d/%d" % (han, total)

    # 2) 拉丁/泰文：用特征字符 + 停用词打分
    best, best_score, best_why = None, 0.0, ""
    low = t.lower()
    for code, d in reg["languages"].items():
        if d.get("script") in ("cjk", "hangul"):
            continue
        pat = d.get("script_markers")
        marker = len(re.findall(pat, t)) if pat else 0
        m_ratio = marker / total
        sw = [w for w in (d.get("detect_stopwords") or []) if re.search(r"\b%s\b" % re.escape(w), low)]
        sw_score = min(1.0, len(sw) / 6.0)
        score = m_ratio * 0.45 + sw_score * 0.55
        if score > best_score:
            best, best_score, best_why = code, score, ("特征字符 %d、停用词命中 %d（%s）"
                                                       % (marker, len(sw), "、".join(sw[:5])))
    if best and best_score >= 0.35:
        return best, round(best_score, 2), best_why
    return None, round(best_score, 2), "信号不足：" + (best_why or "无")


# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------
def rate_mid(d: dict) -> float:
    t = (d.get("speech_rate") or {}).get("target") or [3.0]
    return sum(t) / len(t)


def units_of(text: str, d: dict, code: str) -> tuple:
    """把一段文本折算成该语种的「单位数」（char=字符数 / word=词数）与其字符数。"""
    chars = len(re.findall(r"\S", text))
    unit = (d.get("speech_rate") or {}).get("unit", "char")
    if unit == "char":
        return chars, chars
    cpw = float(d.get("chars_per_word") or LATIN_CHARS_PER_WORD)
    return round(chars / cpw), chars


def budget(src_text: str, src_code: str, src_d: dict, dst_d: dict) -> dict:
    su, _ = units_of(src_text, src_d, src_code)
    secs = su / rate_mid(src_d)
    tgt_units_max = round(secs * float((dst_d.get("speech_rate") or {}).get("max") or rate_mid(dst_d) * 1.25))
    tgt_units_ok = round(secs * rate_mid(dst_d))
    return {
        "src_units": su,
        "src_unit_label": (src_d.get("speech_rate") or {}).get("unit_label", "字/秒"),
        "src_rate_mid": round(rate_mid(src_d), 2),
        "est_seconds": round(secs, 1),
        "target_units_target": tgt_units_ok,
        "target_units_max": tgt_units_max,
        "target_unit_label": (dst_d.get("speech_rate") or {}).get("unit_label", "单位/秒"),
        "target_unit_cn": "词" if (dst_d.get("speech_rate") or {}).get("unit") == "word" else "字",
        "note": "等效时长估算，供转译时把握长短；精确时长在 ④剧本改写阶段按 DUR 控制。",
    }


# ---------------------------------------------------------------------------
# 分段
# ---------------------------------------------------------------------------
SCENE_MARK = re.compile(r"^\s*(?:第\s*[0-9一二三四五六七八九十百]+\s*[章幕节场集]|"
                        r"CHAPTER\s+\d+|Chapter\s+\d+|INT\.|EXT\.|内景|外景|场\s*\d+|\#{1,3}\s)")


def split_segments(text: str) -> list:
    lines = str(text).splitlines()
    segs, cur = [], []
    for ln in lines:
        if SCENE_MARK.match(ln) and cur:
            segs.append(cur)
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        segs.append(cur)
    out = []
    for i, block in enumerate(segs, 1):
        body = "\n".join(block).strip()
        out.append({"seq": i, "chars": len(re.findall(r"\S", body)),
                    "first_line": (body.splitlines() or [""])[0][:60], "text": body})
    # 没有场景标记 → 按字数切（先按空行分段累加；连空行都没有的长文则**硬切**）
    if len(out) <= 1:
        parts = [p for p in str(text).split("\n\n") if p.strip()]
        if len(parts) <= 1:
            blob = str(text)
            parts = [blob[i:i + SEG_TARGET_CHARS] for i in range(0, len(blob), SEG_TARGET_CHARS)]
        chunks, buf, n = [], [], 0
        for para in parts:
            c = len(re.findall(r"\S", para))
            if n and n + c > SEG_TARGET_CHARS:
                chunks.append(buf)
                buf, n = [], 0
            buf.append(para)
            n += c
        if buf:
            chunks.append(buf)
        out = [{"seq": i, "chars": len(re.findall(r"\S", "\n\n".join(c))),
                "first_line": (c[0] if c else "").strip()[:60], "text": "\n\n".join(c)}
               for i, c in enumerate(chunks, 1)]
    return out


# ---------------------------------------------------------------------------
# 命令：langs / detect
# ---------------------------------------------------------------------------
def cmd_langs(a) -> int:
    reg = registry()
    print(C.hr())
    print("转译语种注册表（%d 种）　%s" % (len(reg["languages"]), REGISTRY))
    print(C.hr())
    for code, d in reg["languages"].items():
        sr = d.get("speech_rate") or {}
        print("● %-7s %s" % (code, d.get("label")))
        print("   别名：%s" % "、".join(d.get("aliases") or []))
        print("   语速：%s（目标 %s）；单位 %s" % (sr.get("unit_label"), sr.get("target"), sr.get("unit")))
        print("   口语红线：%s" % "；".join((d.get("oral_rules") or [])[:2]))
    print()
    print("用法：plan --to <code|别名> → LLM 转译 → check --to <code>")
    return 0


def cmd_detect(a) -> int:
    text = a.text
    if a.file:
        text = C.read_text(pathlib.Path(a.file).expanduser())
        if not text:
            print("[FAIL] 读不到文件：%s" % a.file)
            return 2
    code, conf, why = detect_language(text)
    if a.json:
        print(json.dumps({"code": code, "confidence": conf, "why": why}, ensure_ascii=False))
        return 0 if code else 1
    if not code:
        print("[?] 判定不确定（置信度 %.2f）：%s" % (conf, why))
        print("    请显式指定源语种：plan --to <目标语> --from <源语>（或直接把语种告诉用户确认）")
        return 1
    print("[OK] 源语言判定：%s（%s）  置信度 %.2f" % (code, registry()["languages"][code]["label"], conf))
    print("     依据：%s" % why)
    return 0


# ---------------------------------------------------------------------------
# 命令：plan —— 转译工作单 + brief
# ---------------------------------------------------------------------------
def cmd_plan(a) -> int:
    ws = C.resolve_ws(a.workspace)
    dst_code, dst_d = resolve_lang(a.to)
    src_path = pathlib.Path(a.src).expanduser() if a.src else C.pdir(ws, C.SOURCE_SCRIPT)
    src_text = C.read_text(src_path)
    if not src_text:
        print("[FAIL] 读不到源稿：%s" % src_path)
        print("       请把小说/剧本放到该路径，或用 --src 指定文件。")
        return 2

    if a.frm and a.frm != "auto":
        src_code, src_d = resolve_lang(a.frm)
        conf, why = 1.0, "用户显式指定"
    else:
        src_code, conf, why = detect_language(src_text)
        if not src_code:
            print("[FAIL] 源语言判定不确定（置信度 %.2f）：%s" % (conf, why))
            print("       请显式指定：--from zh / --from en …")
            return EXIT_GATE
        src_d = registry()["languages"][src_code]

    if src_code == dst_code:
        print("[WARN] 源语言与目标语种相同（%s）：无需转译，可直接进 ④剧本改写。" % src_code)
        return 0

    segs = split_segments(src_text)
    bud = budget(src_text, src_code, src_d, dst_d)
    out_path = C.pdir(ws, C.SOURCE_SCRIPT_TRANSLATED % short_code(dst_code))

    plan_obj = {
        "generated_at": C.now_iso(), "module": "S0b-script-translate", "schema_version": 1,
        "from": src_code, "from_label": src_d.get("label"), "from_confidence": conf,
        "from_detect_why": why,
        "to": dst_code, "to_label": dst_d.get("label"),
        "src_file": str(src_path), "out_file": str(out_path),
        "src_chars": len(re.findall(r"\S", src_text)),
        "src_lines": src_text.count("\n") + 1,
        "segments": [{"seq": s["seq"], "chars": s["chars"], "first_line": s["first_line"]} for s in segs],
        "budget": bud,
        "dst_rules": {
            "speech_rate": dst_d.get("speech_rate"), "oral_rules": dst_d.get("oral_rules"),
            "forbidden_phrases": dst_d.get("forbidden_phrases"), "punctuation": dst_d.get("punctuation"),
            "localization": dst_d.get("localization"), "name_policy": dst_d.get("name_policy"),
        },
        "qc": dst_d.get("qc"),
        "redlines": registry().get("hard_redlines"),
        "note": "转译工作单：LLM 按 translation-brief.md 分段转译 → check 通过 → 进 ④剧本改写。",
    }
    p = C.write_json(C.pdir(ws, C.TRANSLATE_PLAN), plan_obj)

    print(C.hr())
    print("剧本转译 · 工作单")
    print(C.hr())
    print("  源稿      : %s（%s，%d 字 / %d 行）"
          % (src_path, src_d.get("label"), plan_obj["src_chars"], plan_obj["src_lines"]))
    print("  源语判定  : %s（置信度 %.2f，%s）" % (src_code, conf, why))
    print("  目标语种  : %s（%s）" % (dst_code, dst_d.get("label")))
    print("  分段      : %d 段（场景/章节标记切分，无标记则按 %d 字切）" % (len(segs), SEG_TARGET_CHARS))
    print("  预算      : 等效时长约 %s 秒 → 目标语 %s~%s %s"
          % (bud["est_seconds"], bud["target_units_target"], bud["target_units_max"],
             bud.get("target_unit_cn", "")))
    print("  落点      : %s" % p)
    if a.write:
        bp = C.write_text(C.pdir(ws, C.TRANSLATION_BRIEF),
                          render_brief(plan_obj, segs, dst_d))
        print("  brief     : %s" % bp)
    print()
    print("  下一步：LLM 按 brief 分段转译 → 写回 %s" % out_path)
    print("          python script_translate.py --workspace <ws> check --to %s" % short_code(dst_code))
    return 0


def render_brief(plan_obj: dict, segs: list, dst_d: dict) -> str:
    L, A = [], None
    A = L.append
    reg = registry()
    A("# 转译工作单（%s → %s）" % (plan_obj["from_label"], plan_obj["to_label"]))
    A("")
    A("> 本文件由 `script_translate.py plan --write` 生成；指令来源：`references/translation-languages.json`。")
    A("")
    A("## 一、任务")
    A("")
    A("把 `%s`（%s）转译为 **%s**，写入 `%s`。"
      % (plan_obj["src_file"], plan_obj["from_label"], plan_obj["to_label"], plan_obj["out_file"]))
    A("")
    A("**只换语言与本地化表达**：不增删情节、不改人设、不改钩子、不改镜序。")
    A("")
    A("## 二、语种规范（%s）" % plan_obj["to"])
    A("")
    sr = dst_d.get("speech_rate") or {}
    A("- **语速口径**：%s～%s %s（目标中值 %s）"
      % (sr.get("min"), sr.get("max"), sr.get("unit_label"), round(rate_mid(dst_d), 2)))
    A("- **标点**：%s" % json.dumps(dst_d.get("punctuation") or {}, ensure_ascii=False))
    A("- **人名策略**：%s" % dst_d.get("name_policy"))
    A("")
    A("### 必须遵守的口语规范")
    A("")
    for x in dst_d.get("oral_rules") or []:
        A("- %s" % x)
    A("")
    A("### 翻译腔黑名单（出现即返工）")
    A("")
    for x in dst_d.get("forbidden_phrases") or []:
        A("- %s" % x)
    A("")
    A("### 本地化适配")
    A("")
    for x in dst_d.get("localization") or []:
        A("- %s" % x)
    A("")
    A("## 三、预算")
    A("")
    b = plan_obj["budget"]
    A("- 源稿等效时长约 **%s 秒**（按源语速 %s %s 折算）" % (b["est_seconds"], b["src_rate_mid"], b["src_unit_label"]))
    A("- 目标语篇幅：**%s～%s %s**（超出即改写，禁止为塞字而加速语速）"
      % (b["target_units_target"], b["target_units_max"], b.get("target_unit_cn", "")))
    A("")
    A("## 四、术语表（先固化再转译）")
    A("")
    A("先用 `script_translate.py glossary init` 生成 `glossary.json`，把人名/地名/机构/专有名词的**唯一译法**定下来；"
      "全稿只能出现一种写法。")
    A("")
    A("## 五、分段（逐段转译，保留段号便于对照）")
    A("")
    A("| 段 | 源字数 | 首行预览 |")
    A("|---|---|---|")
    for s in plan_obj["segments"]:
        A("| %d | %d | %s |" % (s["seq"], s["chars"], s["first_line"].replace("|", "／")))
    A("")
    A("## 六、红线")
    A("")
    for x in reg.get("hard_redlines") or []:
        A("- %s" % x)
    A("")
    A("## 七、交付与自检")
    A("")
    A("```bash")
    A("python stages/creation/scripts/script_translate.py --workspace <ws> check --to %s" % short_code(plan_obj["to"]))
    A("```")
    A("")
    A("质检维度：① 目标语特征字符占比 ② 残留源语言字符 ③ 翻译腔黑名单 ④ 术语一致性。全绿才允许进 ④剧本改写。")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# 命令：check —— 转译质检（确定性）
# ---------------------------------------------------------------------------
def _blacklist_pattern(phrase: str) -> str | None:
    """把黑名单条目编译成**容忍词形变化**的正则。

    条目格式（见 translation-languages.json 的注释约定）：
        "hac|hiz|hace una decisión（说明）"  → 可自动检测；`|` 为同词位变体
        "~ 说明文字"                         → 仅人工参考，返回 None（不参与自动匹配）

    长词按「词干前缀 + \\w*」编译，从而抓住变位/变格（hizo / haciendo / decisiones…）。
    """
    body = re.split(r"[（(]", str(phrase), 1)[0].strip()
    if not body or body.startswith("~"):
        return None
    toks = [t for t in re.split(r"\s+", body) if re.search(r"[^\W\d_]", t, re.UNICODE)]
    if not toks:
        return None

    def stem(a: str) -> str:
        a = re.sub(r"[^\w]", "", a, flags=re.UNICODE)
        if not a:
            return ""
        return re.escape(a[:len(a) - 2]) if len(a) >= 5 else re.escape(a)

    parts = []
    for t in toks:
        alts = [a for a in t.split("|") if a.strip()]
        s = "|".join(x for x in (stem(a) for a in alts) if x)
        if s:
            parts.append("(?:%s)\\w*" % s)
    if not parts:
        return None
    return r"\s+".join(parts)


def cmd_check(a) -> int:
    ws = C.resolve_ws(a.workspace)
    dst_code, dst_d = resolve_lang(a.to)
    path = (pathlib.Path(a.file).expanduser() if a.file
            else C.pdir(ws, C.SOURCE_SCRIPT_TRANSLATED % short_code(dst_code)))
    text = C.read_text(path)
    if not text:
        print("[FAIL] 读不到转译稿：%s" % path)
        return 2

    qc = dst_d.get("qc") or {}
    total = len(re.findall(r"\S", text)) or 1
    oks, warns, fails = [], [], []

    # 1) 目标语特征字符占比
    mk = len(re.findall(dst_d.get("script_markers") or r"(?!)", text))
    ratio = mk / total
    lo = float(qc.get("min_marker_ratio") or 0)
    (oks if ratio >= lo else fails).append(
        ("目标语特征字符占比 ≥%.0f%%" % (lo * 100), "实测 %.1f%%（%d/%d）" % (ratio * 100, mk, total)))

    # 2) 残留源语言整句
    fp = len(re.findall(dst_d.get("foreign_script_pattern") or r"(?!)", text))
    f_ratio = fp / total
    hi = float(qc.get("foreign_ratio_max") or 0.02)
    (oks if f_ratio <= hi else fails).append(
        ("残留他语字符占比 ≤%.0f%%" % (hi * 100), "实测 %.2f%%（%d/%d）" % (f_ratio * 100, fp, total)))

    # 3) 翻译腔黑名单（容忍词形变化；`~` 开头的条目仅作人工参考）
    phrases = dst_d.get("forbidden_phrases") or []
    hits, auto_n = [], 0
    for ph in phrases:
        pat = _blacklist_pattern(ph)
        if not pat:
            continue
        auto_n += 1
        if re.search(pat, text, re.IGNORECASE | re.UNICODE):
            hits.append(ph)
    (oks if not hits else fails).append(
        ("翻译腔黑名单（自动检测 %d/%d 条）" % (auto_n, len(phrases)),
         "命中：%s" % "；".join(hits) if hits else "无命中"))

    # 4) 术语一致性
    gl = C.read_json(C.pdir(ws, C.GLOSSARY)) or {}
    terms = gl.get("terms") or []
    missing, bad_variant = [], []
    for t in terms:
        tgt = str(t.get("target") or "").strip()
        if tgt and tgt not in text:
            missing.append("%s→%s" % (t.get("src"), tgt))
        for v in t.get("forbidden") or []:
            if v and v in text:
                bad_variant.append("%s 误用「%s」" % (t.get("src"), v))
    if terms:
        (oks if not bad_variant else fails).append(
            ("术语表禁用写法（%d 条术语）" % len(terms),
             "；".join(bad_variant) if bad_variant else "无违规"))
        (warns if missing else oks).append(
            ("术语表译法已出现", "未出现：%s" % "；".join(missing[:6]) if missing else "全部出现"))
    else:
        warns.append(("术语表", "未建立（建议 glossary init 后固化人名/专名译法）"))

    # 5) 篇幅预算（若工作单存在）
    tp = C.read_json(C.pdir(ws, C.TRANSLATE_PLAN)) or {}
    bud = tp.get("budget") or {}
    if bud:
        u, _ = units_of(text, dst_d, dst_code)
        ok_max = float(bud.get("target_units_max") or 0)
        if ok_max:
            (oks if u <= ok_max else warns).append(
                ("篇幅 ≤%d %s" % (ok_max, bud.get("target_unit_cn", "")),
                 "实测 %s（目标 %s）" % (u, bud.get("target_units_target"))))

    print(C.hr())
    print("转译质检 · %s → %s" % (tp.get("from", "?"), dst_d.get("label")))
    print(C.hr())
    print("  稿件：%s（%d 字）" % (path, total))
    for k, v in oks:
        print("  [OK  ] %-30s %s" % (k, v))
    for k, v in warns:
        print("  [WARN] %-30s %s" % (k, v))
    for k, v in fails:
        print("  [FAIL] %-30s %s" % (k, v))
    print()
    if fails:
        print("[FAIL] 转译稿未过质检（%d 项）→ **禁止进入下游**，先修再检。" % len(fails))
        return EXIT_GATE
    print("[OK] 转译稿通过质检%s → 可进 ④剧本改写（s3）。"
          % ("（含 %d 项警告）" % len(warns) if warns else ""))
    return 0


# ---------------------------------------------------------------------------
# 命令：glossary
# ---------------------------------------------------------------------------
GLOSSARY_TPL = {
    "_comment": "转译术语表：同一人/物/机构全剧只能一种写法；forbidden 填禁止出现的错译。",
    "terms": [],
}


def cmd_glossary(a) -> int:
    ws = C.resolve_ws(a.workspace)
    p = C.pdir(ws, C.GLOSSARY)
    if a.gcmd == "init":
        obj = C.read_json(p)
        if isinstance(obj, dict) and obj.get("terms"):
            print("[SKIP] 术语表已存在（%d 条）：%s" % (len(obj["terms"]), p))
            return 0
        print("[OK] 已写出术语表模板：%s" % C.write_json(p, GLOSSARY_TPL))
        print("     下一步：glossary add --src \"苏念\" --target \"Su Nian\" --forbidden \"Su Niann\"")
        return 0
    if a.gcmd == "list":
        obj = C.read_json(p) or {}
        for t in obj.get("terms") or []:
            print("● %-14s → %-22s %s" % (t.get("src"), t.get("target"), t.get("note") or ""))
        if not (obj.get("terms") or []):
            print("（空）先跑 glossary init")
        return 0
    if a.gcmd == "add":
        obj = C.read_json(p) or dict(GLOSSARY_TPL)
        terms = obj.setdefault("terms", [])
        item = {"src": a.src, "target": a.target, "note": a.note,
                "forbidden": [x for x in (a.forbidden or []) if x]}
        for i, t in enumerate(terms):
            if t.get("src") == a.src:
                terms[i] = item
                break
        else:
            terms.append(item)
        print("[OK] 已登记 %d 条术语：%s" % (len(terms), C.write_json(p, obj)))
        return 0
    print("[FAIL] 未知 glossary 子命令")
    return 2


# ---------------------------------------------------------------------------
def main() -> int:
    C.harden_stdio()
    ap = argparse.ArgumentParser(description="剧本/小说转译：工作单 + 语言质检")
    ap.add_argument("--workspace", default=None)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("langs", help="列出支持语种")
    p.set_defaults(fn=cmd_langs)

    p = sub.add_parser("detect", help="判定源语言")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--file", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_detect)

    p = sub.add_parser("plan", help="生成转译工作单（--write 同时写 brief）")
    p.add_argument("--to", required=True, help="目标语种（es/en/ja/ko/pt/th/id/vi/zh 或别名）")
    p.add_argument("--from", dest="frm", default="auto")
    p.add_argument("--src", default=None)
    p.add_argument("--write", action="store_true")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("check", help="转译稿质检")
    p.add_argument("--to", required=True)
    p.add_argument("--file", default=None)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("glossary", help="术语表")
    gs = p.add_subparsers(dest="gcmd")
    q = gs.add_parser("init")
    q.set_defaults(fn=cmd_glossary)
    q = gs.add_parser("list")
    q.set_defaults(fn=cmd_glossary)
    q = gs.add_parser("add")
    q.add_argument("--src", required=True)
    q.add_argument("--target", required=True)
    q.add_argument("--note", default=None)
    q.add_argument("--forbidden", nargs="*", default=None)
    q.set_defaults(fn=cmd_glossary)
    p.set_defaults(fn=cmd_glossary, gcmd="list")

    a = ap.parse_args()
    if not getattr(a, "fn", None):
        return cmd_langs(a)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
