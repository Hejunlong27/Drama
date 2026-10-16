#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""compliance_precheck.py —— 发布前平台合规预检（缺口 G4，2026-10-16 新增）

为什么有它
----------
用户保留的 `drama-platform-compliance-audit` 技能是「被限流之后的诊断与申诉」；
但**发布前**缺一道机器可跑的自查，导致「受限集数超阈值影响整体发布」这类问题反复出现。
本脚本把该技能里**可确定性判定**的部分固化成门禁，并在阶段目录 `08-合规/` 留痕。

预检三问（TikTok 口径，逐集必做）
----------------------------------
    Q1 方法是否出现？（是否呈现任何可被模仿的操作信息）
    Q2 主体是否出现？（含背景、屏幕画面、倒影、监控屏）
    Q3 身份是否歧义？（校服/学生装/童装风格 → 会被读作未成年）
**三项全「否」才可发布**；任一为「是」须走「镜头外化」整改（死亡情节一律
「镜头外发生 → 事后发现 → 以调查与对白推进」）。

另两项硬要求
------------
* **AIGC 标注**：AI 生成内容用于真实还原人物/场景时**必须明确标注**（平台要求，未标注 = 降流）。
* **受限集数占比 ≤ 10%**：`受限占比 = 受限集数 / 总集数`，超过会「影响整体发布」。

用法
----
    python compliance_precheck.py --workspace "D:/剧/新剧" \
        --ep ep01 --q1 no --q2 no --q3 no --aigc-label yes
    python compliance_precheck.py --workspace "D:/剧/新剧" --all \
        --restricted 3 --total 30 --aigc-label yes

退出码：0 = 可发布；2 = 不可发布（有整改项）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _ws as W  # noqa: E402
import _layout as L  # noqa: E402

LIMIT_RATIO = 0.10        # ★ 受限集数占比警戒线
TIKTOK_THEMES = [
    ("安全与文明", "Violent and Criminal Behavior", "斗殴、寻仇、犯罪手法演示、复仇爽剧里的具体作案步骤"),
    ("安全与文明", "Youth Sexual and Physical Abuse", "校服/学生装角色涉性涉险；被读作未成年的角色出现在暴力场景"),
    ("安全与文明", "Human Trafficking and Smuggling", "拐卖、偷渡情节若带操作性细节"),
    ("心理与行为健康", "Suicide and Self-Harm", "★最高频踩线：上吊/割腕/跳楼/服毒的画面呈现或方法描写"),
    ("心理与行为健康", "Disorder Eating / Body Image", "极端减肥、催吐、身材羞辱"),
    ("心理与行为健康", "Dangerous Activity and Challenges", "危险驾驶、极限行为模仿"),
    ("敏感与成熟主题", "Body Exposure and Sexualized Behaviors", "半裸、床戏、贴身挑逗、以暴露为卖点"),
    ("敏感与成熟主题", "Shocking and Graphic Content", "尸体、大量出血、伤口特写、酷刑"),
    ("敏感与成熟主题", "Animal Abuse", "虐宠、斗兽"),
    ("诚信与真实", "Edited Media and AIGC", "★AI 生成真人化内容未标注 → 降流"),
    ("诚信与真实", "Unoriginal Content and IP Rights", "搬运、未授权 BGM、残留第三方水印/台标"),
    ("受管制商品", "Regulated Goods / Commercial Disclosure", "烟草、酒精、赌博、药品的展示与营销"),
]


def _yn(v, default=None):
    if v is None:
        return default
    return str(v).strip().lower() in ("y", "yes", "true", "1", "是", "有")


def main() -> int:
    ap = argparse.ArgumentParser(description="发布前平台合规预检（TikTok 口径）")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--ep", default=None, help="单集预检（发布前三问必填）")
    ap.add_argument("--all", action="store_true", help="整剧预检（受限集数占比 + AIGC 标注）")
    ap.add_argument("--q1", default=None, help="方法是否出现？（yes/no）")
    ap.add_argument("--q2", default=None, help="主体是否出现（含背景/倒影/屏幕）？（yes/no）")
    ap.add_argument("--q3", default=None, help="身份是否歧义（校服/童装风）？（yes/no）")
    ap.add_argument("--aigc-label", dest="aigc", default=None, help="全剧是否已统一标注 AIGC？（yes/no）")
    ap.add_argument("--restricted", type=int, default=0, help="已受限集数")
    ap.add_argument("--total", type=int, default=0, help="总集数")
    ap.add_argument("--note", default="", help="补充说明")
    a = ap.parse_args()

    ws = pathlib.Path(a.workspace).expanduser().resolve() if a.workspace else W.WS
    blocks, warns = [], []

    # ---- 单集：发布前三问 ----
    if a.ep:
        ans = {"Q1 方法是否出现": _yn(a.q1), "Q2 主体是否出现": _yn(a.q2), "Q3 身份是否歧义": _yn(a.q3)}
        missing = [k for k, v in ans.items() if v is None]
        if missing:
            print("[BLOCK] 单集预检必须回答发布前三问：%s" % "；".join(missing))
            print("        用法：--q1 no --q2 no --q3 no")
            return 2
        hit = [k for k, v in ans.items() if v]
        print("=" * 74)
        print("发布前合规预检 · %s（工作区 %s）" % (a.ep, ws))
        print("=" * 74)
        for k, v in ans.items():
            print("  %-16s %s" % (k, "是 ← 需整改" if v else "否"))
        if hit:
            blocks.append("发布前三问有 %d 项为「是」：%s ⇒ 走「镜头外化」整改后再发" % (len(hit), "；".join(hit)))

    # ---- 整剧：AIGC 标注 + 受限集数占比 ----
    if a.all or a.aigc is not None or a.total:
        aigc = _yn(a.aigc)
        if a.aigc is not None and aigc is False:
            blocks.append("全剧未标注 AIGC ⇒ 平台要求「AI 用于真实还原人物或场景时须明确标注」，未标注会降流")
        elif a.aigc is None:
            warns.append("未提供 --aigc-label，无法判定 AIGC 标注是否合规（平台硬要求）")
        if a.total > 0:
            ratio = a.restricted / a.total
            allow = int(LIMIT_RATIO * a.total + 0.999999)   # ceil
            remain = allow - a.restricted
            print("\n  受限集数    : %d / %d = %.1f%%（警戒线 %.0f%%，上限 %d 集）"
                  % (a.restricted, a.total, ratio * 100, LIMIT_RATIO * 100, allow))
            print("  剩余余量    : %d 集" % remain)
            if ratio > LIMIT_RATIO:
                blocks.append("受限占比 %.1f%% 超过警戒线 %.0f%%（超 %d 集）⇒ 会影响整体发布，先整改再上新"
                              % (ratio * 100, LIMIT_RATIO * 100, a.restricted - allow))
            elif remain <= 1:
                warns.append("受限余量仅剩 %d 集，后续发布需谨慎" % remain)

    if not a.ep and not a.all and a.aigc is None and not a.total:
        print("提示：单集用 --ep ep01 --q1/--q2/--q3；整剧用 --all --aigc-label yes --restricted N --total M")
        print("      下面的条款表可用于逐集人工核对。")

    print("\n  ── TikTok 六主题条款映射（节选，完整清单见 docs/compliance/发布前合规预检清单.md）──")
    for theme, clause, forms in TIKTOK_THEMES:
        print("  [%s] %-42s %s" % (theme, clause, forms))
    for w in warns:
        print("  [WARN] %s" % w)
    for b in blocks:
        print("  [FAIL] %s" % b)

    # ---- 落痕到 08-合规/ ----
    try:
        outdir = L.stage_dir(ws, "compliance")
    except Exception:                                                          # noqa: BLE001
        outdir = ws / "08-合规"
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rec = {"at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
           "ep": a.ep, "all": a.all, "answers": {"q1": a.q1, "q2": a.q2, "q3": a.q3},
           "aigc_label": a.aigc, "restricted": a.restricted, "total": a.total,
           "note": a.note, "blocks": blocks, "warns": warns,
           "verdict": "不可发布" if blocks else "可发布（本项）"}
    p = outdir / ("合规预检_%s.json" % stamp)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n[OK] 预检记录：%s" % p)

    if blocks:
        print("\n[BLOCK] 不可发布 —— 共 %d 项整改要求。" % len(blocks))
        return 2
    print("\n[OK] 本项目本项预检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
