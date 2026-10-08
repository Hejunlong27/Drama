# -*- coding: utf-8 -*-
"""
run.py —— ai-video-pipeline 统一入口

用法（SKILL_DIR 用本仓库实际路径替换，即 <repo>/stages/video/workflow）：

    python run.py doctor [--json] [--workspace <路径>]     # G0 环境体检（不联网）
    python run.py probe  [probe_nodes 的参数...]           # G2 节点探测（联网，不扣费）
    python run.py check  [--json]                          # 缺哪些必填配置（不联网）
    python run.py --dry-run --strict                       # G1 预演（不联网）
    python run.py --limit 1                                # G4 小样（★真实计费）
    python run.py --resume 20250917_153000                 # G5 全量 / 续跑

环境变量：
    VIDEO_PIPELINE_ENGINE    覆盖引擎目录（默认 <skill>/scripts/engine，用于本地调试）
    VIDEO_PIPELINE_WORKSPACE 覆盖工作区
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from config_layer import (          # noqa: E402
    force_utf8_stdio, resolve_engine, resolve_workspace,
)

force_utf8_stdio()

USAGE = """用法：
  python run.py doctor  [--json] [--workspace <路径>]        环境体检（不联网）
  python run.py probe   --target banana|h3 [--emit FILE]     节点探测（联网，不扣费）
  python run.py probe   --kind workflow --id <ID>            探测任意工作流
  python run.py check   [--json]                             缺哪些必填配置
  python run.py --dry-run --strict                           预演（不联网）
  python run.py --limit 1                                    小样（★ 真实计费）
  python run.py --resume <batch_id>                          全量 / 续跑

工作区解析：--workspace > 环境变量 VIDEO_PIPELINE_WORKSPACE > <引擎>/.workspace 指针
          > ~/video-pipeline-workspace
"""


def _import_engine(name: str):
    engine = resolve_engine()
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    if str(engine / "tools") not in sys.path:
        sys.path.insert(0, str(engine / "tools"))
    return __import__(name)


def main(argv: list) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    cmd, rest = argv[0], argv[1:]

    if cmd == "doctor":
        sys.argv = [str(Path(__file__).with_name("doctor.py"))] + rest
        import doctor
        return doctor.main()

    if cmd == "probe":
        sys.argv = [str(resolve_engine() / "tools" / "probe_nodes.py")] + rest
        mod = _import_engine("probe_nodes")
        return mod.main()

    if cmd == "check":
        sys.argv = ["main.py", "--check"] + rest
        mod = _import_engine("main")
        return mod.main()

    if cmd.startswith("-"):
        sys.argv = ["main.py"] + argv
        mod = _import_engine("main")
        return mod.main()

    print(f"[FAIL] 未知命令: {cmd}\n")
    print(USAGE)
    return 2


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n已中断。重跑时加 --resume <batch_id> 即可续跑，已完成的阶段不会重复扣费。")
        sys.exit(130)
