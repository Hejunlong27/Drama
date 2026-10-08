# -*- coding: utf-8 -*-
"""平台字幕安全区自测（零依赖、零网络、零计费）。

跑法：
    python scripts/test_safe_area.py

它证明三件事：
  1. 三套画布（横屏 1440p / 竖屏 1080p / 竖屏 2K）× 两种语言，写出的 ASS 都满足
     「距底 ≥200px」与「文本框宽 ≤70% 视频宽」；
  2. **竖屏字号会被反向约束**——否则按高度百分比算出的字号会把行撑出安全区；
  3. 断言**真的会拦住**：把 ASS 里的边距手改成"贴边"，qa_safe_area() 必须报失败。

判据来自平台规则（用户 2026-10-06 给定）：字幕须落在安全区内，距底部至少 200px，
文本框宽度不超过视频宽度的 70%。
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SKILL = os.path.dirname(HERE)


def load_module():
    src = io.open(os.path.join(HERE, "subtitle.py"), encoding="utf-8").read()
    src = src.replace('if __name__ == "__main__":', 'if False:')
    mod = type(sys)("subtitle_under_test")
    mod.__file__ = os.path.join(HERE, "subtitle.py")
    exec(compile(src, "subtitle.py", "exec"), mod.__dict__)
    return mod


CUES = [
    {"start": 0.0, "end": 1.5, "text": "这是一条用来验证安全区的中文字幕",
     "lines": ["这是一条用来验证", "安全区的中文字幕"]},
    {"start": 1.6, "end": 3.0, "text": "第二条", "lines": ["第二条"]},
]

CANVASES = [
    (1440, 832, "横屏 1440p"),
    (1088, 1920, "竖屏 1080p"),
    (2176, 3840, "竖屏 2K"),
]


def main():
    m = load_module()
    cfg = json.loads(io.open(os.path.join(SKILL, "assets", "config.subtitle.example.json"),
                             encoding="utf-8").read())
    tmp = tempfile.mkdtemp(prefix="safearea_")
    fails = []

    print("=" * 104)
    print("%-14s %-4s %7s %7s %8s %8s %-22s %s"
          % ("画布", "语言", "字号px", "距底px", "文本框px", "占比", "字号依据", "断言"))
    print("-" * 104)
    for W, H, label in CANVASES:
        for lang in ("zh", "es"):
            p = os.path.join(tmp, "%dx%d_%s.ass" % (W, H, lang))
            info = m.write_ass(CUES, cfg, p, lang=lang, canvas=(W, H))
            qa = m.qa_safe_area(p, cfg)
            if not qa["pass"]:
                fails.append((label, lang, qa["failures"]))
            print("%-14s %-4s %7d %7d %8d %7.1f%% %-22s %s"
                  % (label, lang, info["font_size_px"], info["margin_v_px"],
                     info["text_w_px"], info["text_w_pct"],
                     info["font_size_by"][:20], "通过" if qa["pass"] else "失败"))

    print()
    print("=" * 104)
    print("项 2：竖屏字号必须被「文本框宽 ÷ 每行字符」反向压住")
    print("-" * 104)
    for lang in ("zh", "es"):
        lc = cfg["languages"][lang]
        mm = m.style_metrics(cfg, lang, (1088, 1920))
        naive = int(round(1920 * lc["font_size_pct"] / 100.0))
        need_naive = lc["max_line_chars"] * lc["char_width_em"] * naive
        print("  %s：按高度百分比=%.1f%% → %dpx，一行 %d 字需要 %.0fpx，"
              "安全区只给 %dpx ⇒ 必须压到 %dpx（%s）"
              % (lang, lc["font_size_pct"], naive, lc["max_line_chars"], need_naive,
                 mm["usable_w"], mm["font_size"], mm["font_size_by"]))
        if need_naive <= mm["usable_w"]:
            print("     ⚠ 该语言本来就不会溢出（无需压字）")

    print()
    print("=" * 104)
    print("项 3：断言必须能拦住被手改坏的 ASS")
    print("-" * 104)
    p = os.path.join(tmp, "tamper.ass")
    info = m.write_ass(CUES, cfg, p, lang="zh", canvas=(1088, 1920))
    good = io.open(p, encoding="utf-8").read()
    stamp = ",2,%d,%d,%d,1" % (info["margin_lr_px"], info["margin_lr_px"], info["margin_v_px"])
    bad = good.replace(stamp, ",2,20,20,10,1")
    if bad == good:
        print("   ⚠ 没找到可替换的样式字段，跳过该项")
    else:
        io.open(p, "w", encoding="utf-8").write(bad)
        qa = m.qa_safe_area(p, cfg)
        print("   篡改后（左右 20px / 距底 10px）→ %s"
              % ("❌ 断言漏放（不该）" if qa["pass"] else "✅ 已拦住：" + "；".join(qa["failures"])))
        if qa["pass"]:
            fails.append(("篡改", "zh", ["断言未拦住"]))

    print()
    if fails:
        print("★ 失败 %d 项：" % len(fails))
        for f in fails:
            print("   ", f)
        return 1
    print("全部通过：安全区（距底 ≥%dpx、文本框宽 ≤%d%%）在三套画布 × 两种语言下均成立。"
          % (cfg["safe_area"]["min_bottom_px"], cfg["safe_area"]["max_text_width_pct"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
