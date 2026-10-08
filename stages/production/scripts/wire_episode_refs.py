# -*- coding: utf-8 -*-
"""wire_episode_refs.py —— 把 `character_refs` 重排成提示词 `<Picture N>` 要求的顺序

为什么必须重排（2026-09-19 归纳）：
  `subject_definitions` 的 `<Picture N>` 对**全部 Subject 顺序编号**（角色/场景/道具都占号），
  而引擎是把 `character_refs[i]` 顺序喂给第 i 个槽位（node 51/49/50/43/19/23）。
  ⇒ 缺一个角色的图、或场景图没挂进去，**后面的图全部错位**。
  ⇒ 补资产时不能「把场景图追加到末尾」，必须按 Picture 号逐个落位，且不能留空号。

用法：
    python scripts\\wire_episode_refs.py 4 5 6            # 干跑，只打印将要写入的内容
    python scripts\\wire_episode_refs.py 4 5 6 --apply    # 真写 shots.json（自动备份）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import episode_preflight as PF  # noqa: E402

WS = PF.WS  # ★ 工作区解析沿用 episode_preflight（env DRAMA_WORKSPACE > 位置推断 > 报错）
DATA = WS / "video-pipeline" / "data"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes", nargs="+")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    shots = json.loads((DATA / "shots.json").read_text(encoding="utf-8"))
    prompts = json.loads((DATA / "h3_prompts.json").read_text(encoding="utf-8"))
    chars = json.loads((DATA / "characters.json").read_text(encoding="utf-8"))
    char_sections, _ = PF.parse_asset_md()

    by_id = {s["shot_id"]: s for s in shots}
    changes, blocked = [], []
    for e in args.episodes:
        ep = "ep%02d" % int(e)
        r = PF.analyze(ep, shots, prompts, chars, char_sections)
        for row in r["rows"]:
            if row["target"] is None:
                blocked.append((row["shot"], "无提示词"))
                continue
            miss = [k for k in row["target"] if k.startswith("❌")]
            if miss:
                blocked.append((row["shot"], "缺资产: " + ", ".join(miss)))
                continue
            cur = list(by_id[row["shot"]].get("character_refs") or [])
            if cur != row["target"]:
                changes.append((row["shot"], cur, row["target"]))

    print("=" * 88)
    print("character_refs 重排（%s）" % ("真实写入" if args.apply else "干跑，不写盘"))
    print("=" * 88)
    for sid, cur, tgt in changes:
        print("  %-14s %s" % (sid, ",".join(cur) or "—"))
        print("  %-14s → %s" % ("", ",".join(tgt)))
    print("\n  将变更 %d 段" % len(changes))
    if blocked:
        print("  ★ 仍被阻塞 %d 段（缺资产，先补图再重排）：" % len(blocked))
        for sid, why in blocked:
            print("      %-14s %s" % (sid, why))

    if not args.apply:
        print("\n  加 --apply 才写盘。")
        return 0

    bak = DATA / ("shots.json.bak_%s" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(DATA / "shots.json", bak)
    for sid, _, tgt in changes:
        by_id[sid]["character_refs"] = tgt
    (DATA / "shots.json").write_text(json.dumps(shots, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print("\n  [OK] 已写入 %d 段；备份：%s" % (len(changes), bak.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
