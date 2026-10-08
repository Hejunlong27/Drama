# -*- coding: utf-8 -*-
"""wire_new_assets.py —— 把已生成的资产「瘦身 + 接进 characters.json」

解决三个具体问题：
  1. 新生成的人物图是 6000×3376（超 H3 上限 5760）⇒ 必须瘦身才能进参考槽位。
  2. 生成器产出的是 `char_10_大伯母.png` 这种**带名字**的文件，而既有约定是
     `refs_h3/<key>.jpg`（**按 key 命名**）⇒ 这里统一按 key 落盘，保持全库一致。
  3. 生成 ≠ 可用：产物躺在 output/assets/ 里，`characters.json` 里没有这个 key，
     引擎就会报「找不到 key」。这一步把 key 接进去（带备份）。

为什么不直接用 prepare_refs.py：
  它从 characters.json **现有的值**里取源文件，而这些新资产在 characters.json 里**还没有 key**；
  且在 --apply 时对「源文件已在 refs_h3」的旧条目会 src==dst 自我覆写。所以另走一条明确路径。

用法：
    python scripts\\wire_new_assets.py                    # 干跑
    python scripts\\wire_new_assets.py --apply            # 真写（瘦身 + 改 characters.json）
    python scripts\\wire_new_assets.py --only char_17,scene_04 --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))  # noqa: E402
from _ws import WS  # noqa: E402  ★ 工作区解析（env DRAMA_WORKSPACE > 位置推断 > 报错），见 scripts/_ws.py
VP = WS / "video-pipeline"
DATA = VP / "data"
OUT = VP / "output" / "assets"
REFS_H3 = DATA / "refs_h3"
CFG = DATA / "characters.json"

MAX_EDGE = 2048          # 与 prepare_refs.py 的 DEFAULT_MAX_EDGE 一致
QUALITY = 92             # 与 prepare_refs.py 的 DEFAULT_QUALITY 一致


def collect() -> list[tuple[str, Path]]:
    """从两个 manifest 汇总 (key, 资产图路径)。files[0] 或 role=='asset' 的那张。"""
    out: list[tuple[str, Path]] = []
    for name in ("char_assets_manifest.json", "scene_assets_manifest.json"):
        f = DATA / name
        if not f.is_file():
            continue
        man = json.loads(f.read_text(encoding="utf-8"))
        for it in man.get("items", []):
            key = it.get("key")
            files = it.get("files") or []
            if not key or not files:
                continue
            pick = next((x for x in files if isinstance(x, dict) and x.get("role") == "asset"), None)
            if pick is None:
                pick = files[0]
            src = Path(pick["file"] if isinstance(pick, dict) else pick)
            if src.is_file():
                out.append((key, src))
    return out


def thin(src: Path, dst: Path) -> tuple[int, int]:
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        longest = max(w, h)
        if longest > MAX_EDGE:
            k = MAX_EDGE / longest
            nw = max(2, int(round(w * k)) // 2 * 2)
            nh = max(2, int(round(h * k)) // 2 * 2)
            im = im.resize((nw, nh), Image.LANCZOS)
        else:
            nw, nh = w, h
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, "JPEG", quality=QUALITY, optimize=True, progressive=True)
        return nw, nh


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default=None, help="只处理这些 key（逗号分隔）")
    ap.add_argument("--force", action="store_true", help="已是 refs_h3 的也重做")
    args = ap.parse_args()

    assets = collect()
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    only = {x.strip() for x in args.only.split(",")} if args.only else None

    todo = []
    for key, src in assets:
        if only and key not in only:
            continue
        cur = cfg.get(key)
        if cur and not args.force:
            print("  %-10s [SKIP] characters.json 已有该 key -> %s" % (key, cur))
            continue
        todo.append((key, src))

    print("=" * 90)
    print("新资产 瘦身 + 接入（%s）" % ("真实写入" if args.apply else "干跑，不写盘"))
    print("=" * 90)
    print("  %-10s %-46s %-16s %s" % ("key", "源文件", "原尺寸/体积", "→ 新尺寸/体积"))
    rows = []
    for key, src in todo:
        with Image.open(src) as im:
            w, h = im.size
        mb = src.stat().st_size / 1048576
        dst = REFS_H3 / ("%s.jpg" % key)
        nw = nh = 0
        new_mb = 0.0
        if args.apply:
            nw, nh = thin(src, dst)
            new_mb = dst.stat().st_size / 1048576
        rows.append((key, dst))
        print("  %-10s %-46s %-16s %s"
              % (key, src.name, "%dx%d %.1fMB" % (w, h, mb),
                 ("%dx%d %.2fMB" % (nw, nh, new_mb)) if args.apply else "（待算）"))

    if not todo:
        print("  没有需要处理的资产。")
        return 0

    if not args.apply:
        print("\n  加 --apply 才真的瘦身并写 characters.json。")
        return 0

    bak = DATA / ("characters.json.bak_%s" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(CFG, bak)
    for key, dst in rows:
        cfg[key] = "./refs_h3/%s.jpg" % key
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n  [OK] 已接入 %d 个 key；characters.json 备份：%s" % (len(rows), bak.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
