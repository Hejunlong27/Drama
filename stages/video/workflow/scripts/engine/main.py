# -*- coding: utf-8 -*-
"""
main.py —— 批量 AI 视频生产流水线主流程

用法（一般通过 skill 的 scripts/run.py 调用，它会自动注入工作区）：
    # 0) 先看配置缺什么（不联网）
    python main.py --check

    # 1) 打印工作流的真实 nodeId / fieldName，回填 <工作区>/config.local.json
    python main.py --probe

    # 2) 预演：不联网、不提交，只校验数据 + 组装 nodeInfoList
    python main.py --dry-run

    # 3) 只跑第 1 条打通链路
    python main.py --limit 1

    # 4) 全量跑
    python main.py

    # 5) 断点续跑（沿用原批次目录，已完成的阶段自动跳过）
    python main.py --resume 20250917_153000

工作区：data/ 与 output/ 的基准目录。
解析顺序：--workspace > 环境变量 VIDEO_PIPELINE_WORKSPACE > <引擎>/.workspace 指针
         > ~/video-pipeline-workspace。相对路径的 --data-dir / --out-dir 相对工作区解析。
配置分层：config.py（默认） < <工作区>/config.local.json（私有覆盖） < 环境变量（机密）。

主流程：读输入 JSON -> 分批（每批 20 个 shot）并发处理 -> 批末检查熔断 -> 写 summary.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import config as C
from runninghub_client import RunningHubClient, RunningHubError, truncate
from pipeline import (
    ImageRequest, Logger, ShotContext, UploadCache,
    ST_FAILED, ST_PENDING, ST_SKIPPED, ST_SUCCESS,
    get_image_provider, process_shot,
)
from run_ledger import RunLedger

BASE_DIR = Path(__file__).resolve().parent          # 引擎目录（skill 包内）
WORKSPACE_POINTER = BASE_DIR / ".workspace"          # configure.py init 写入的一行指针
WORKSPACE_FALLBACK = Path.home() / "video-pipeline-workspace"


def _resolve_workspace(explicit: Optional[str]) -> Path:
    """工作区解析（高 -> 低）：
    ① --workspace ② 环境变量 VIDEO_PIPELINE_WORKSPACE ③ <engine>/.workspace 指针
    ④ ~/video-pipeline-workspace。绝不默认落到 skill 包内（cwd 不可靠）。
    """
    for cand in (explicit, os.getenv("VIDEO_PIPELINE_WORKSPACE")):
        if cand and str(cand).strip():
            return Path(str(cand)).expanduser().resolve()
    if WORKSPACE_POINTER.is_file():
        line = WORKSPACE_POINTER.read_text(encoding="utf-8-sig").strip()
        if line:
            return Path(line).expanduser().resolve()
    return WORKSPACE_FALLBACK.resolve()


def _resolve_against(base: Path, value: str) -> Path:
    """绝对路径原样返回；相对路径相对 base（工作区）解析。"""
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p)


# ===========================================================================
# 全局状态：state.json（供断点续跑）
# ===========================================================================
class StateStore:
    """state.json 读写。

    - 所有协程共用 asyncio.Lock 串行化写入（并发写会写坏 JSON）
    - 采用「写临时文件 + os.replace」原子替换，避免进程中断留下半截文件
    - get() 返回 state 内部的**活引用**，update() 就地修改后落盘
    """

    def __init__(self, path: Path, batch_id: str) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self.data: Dict[str, Any] = {
            "batch_id": batch_id,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": None,
            "shots": {},
        }

    # ---------- 载入 ----------
    def load(self) -> bool:
        """从已有 state.json 恢复。返回是否成功恢复。"""
        if not self.path.is_file():
            return False
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"! state.json 无法解析，将作为新批次处理: {self.path}")
            return False
        if isinstance(loaded.get("shots"), dict):
            self.data = loaded
            return True
        return False

    # ---------- 读 ----------
    def get(self, shot_id: str) -> Dict[str, Any]:
        shots = self.data.setdefault("shots", {})
        if shot_id not in shots:
            shots[shot_id] = {"image_status": ST_PENDING, "crop_status": ST_PENDING,
                              "video_status": ST_PENDING, "error": None}
        return shots[shot_id]

    # ---------- 写 ----------
    async def update(self, shot_id: str, **fields: Any) -> None:
        async with self._lock:
            shot = self.get(shot_id)
            shot.update(fields)
            shot["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.data["updated_at"] = shot["updated_at"]
            self._write_sync()

    async def flush(self) -> None:
        async with self._lock:
            self.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._write_sync()

    def _write_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)     # 原子替换


# ===========================================================================
# 熔断器（整批级）
# ===========================================================================
class Fusebox:
    """连续失败 >= N 或失败率 > 阈值 时熔断，停止提交新任务。

    FUSE_MIN_SAMPLES 保证前几个任务全失败时不会立刻熔断（样本太少）。
    """

    def __init__(self, consec_limit: int, rate_limit: float, min_samples: int) -> None:
        self.consec_limit = consec_limit
        self.rate_limit = rate_limit
        self.min_samples = min_samples
        self.consecutive = 0
        self.total = 0
        self.failed = 0
        self._blown = False
        self.reason = ""

    def blown(self) -> bool:
        return self._blown

    def _blow(self, reason: str) -> None:
        if not self._blown:
            self._blown = True
            self.reason = reason

    def record(self, ok: bool) -> None:
        self.total += 1
        if ok:
            self.consecutive = 0
            return
        self.failed += 1
        self.consecutive += 1
        if self.consecutive >= self.consec_limit:
            self._blow(f"连续失败 {self.consecutive} 个（阈值 {self.consec_limit}）")
            return
        if self.total >= self.min_samples and (self.failed / self.total) > self.rate_limit:
            self._blow(f"失败率 {self.failed}/{self.total} = {self.failed / self.total:.0%} "
                       f"超过阈值 {self.rate_limit:.0%}")

    def status(self) -> Dict[str, Any]:
        return {"blown": self._blown, "reason": self.reason, "consecutive_fails": self.consecutive,
                "total_finished": self.total, "failed": self.failed,
                "fail_rate": round(self.failed / self.total, 4) if self.total else 0.0}


# ===========================================================================
# 输入读取与校验
# ===========================================================================
def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"! {path.name} 不是合法 JSON：{e}") from e


class Inputs:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        raw_shots = _read_json(data_dir / "shots.json", [])
        if isinstance(raw_shots, dict):
            raw_shots = raw_shots.get("shots") or []
        self.shots: List[Dict[str, Any]] = [s for s in raw_shots if isinstance(s, dict)]
        self.characters: Dict[str, str] = _read_json(data_dir / "characters.json", {}) or {}
        self.scenes: Dict[str, str] = _read_json(data_dir / "scenes.json", {}) or {}
        self.h3_prompts: Dict[str, Any] = _read_json(data_dir / "h3_prompts.json", {}) or {}

    def validate(self) -> List[str]:
        """静态校验输入，返回问题列表（不联网）。"""
        problems: List[str] = []
        if not self.shots:
            problems.append(f"{self.data_dir / 'shots.json'} 为空或不存在")
        seen = set()
        for i, s in enumerate(self.shots):
            sid = str(s.get("shot_id") or "").strip()
            if not sid:
                problems.append(f"shots[{i}] 缺少 shot_id")
                continue
            if sid in seen:
                problems.append(f"shot_id 重复: {sid}")
            seen.add(sid)
            if not (s.get("banana_prompt") or s.get("prompt")):
                problems.append(f"{sid} 缺少 banana_prompt")
            if sid not in self.h3_prompts:
                problems.append(f"{sid} 在 h3_prompts.json 里没有对应提示词")
            for key, table, name in ((s.get("character_refs") or [], self.characters, "characters"),
                                     (s.get("scene_refs") or [], self.scenes, "scenes")):
                for ref in key:
                    if ref not in table:
                        problems.append(f"{sid} 引用了 {name}.json 里不存在的 key: {ref}")
                    else:
                        v = table[ref]
                        if not str(v).startswith(("http://", "https://", "data:")):
                            p = Path(v)
                            if not p.is_absolute():
                                p = self.data_dir / p
                            if not p.is_file():
                                problems.append(f"{sid} 的参考图 {name}[{ref}] 本地文件不存在: {p}")
        return problems


def _chunks(seq: Sequence[Any], size: int) -> List[List[Any]]:
    size = max(1, int(size))
    return [list(seq[i:i + size]) for i in range(0, len(seq), size)]


# ===========================================================================
# probe：打印真实 nodeId / fieldName
# ===========================================================================
async def run_probe() -> int:
    if not C.RUNNINGHUB_API_KEY:
        print("! 未设置 RUNNINGHUB_API_KEY，无法探测。请先：set RUNNINGHUB_API_KEY=你的key")
        return 2
    prov = C.get_active_provider()
    targets = [(f"图片模型【{C.ACTIVE_IMAGE_PROVIDER}】{prov.get('label','')}",
                prov.get("kind", "workflow"), prov.get("id"))]
    targets.append(("H3 视频工作流", C.H3.get("kind", "workflow"), C.H3.get("id")))

    async with RunningHubClient(C.RUNNINGHUB_API_KEY, C.RH_BASE_URL, C.HTTP_TIMEOUT,
                                channel=C.POLL_CHANNEL) as client:
        for label, kind, target_id in targets:
            print("\n" + "=" * 78)
            print(f"{label}   kind={kind}  id={target_id or '（未填写）'}")
            print("=" * 78)
            if not target_id:
                print("  ! id 未填写，跳过。请先在 config.py 里填 ID。")
                continue
            try:
                nodes = await client.get_nodes(kind, target_id)
            except RunningHubError as e:
                print(f"  ! 探测失败：{e}")
                continue
            if not nodes:
                print("  ! 没有拿到可编辑输入节点。可能：工作流未暴露 API 输入，或 ID 不正确。")
                continue
            print(f"  共 {len(nodes)} 个可填节点 —— 把 nodeId / fieldName 抄进 config.py：\n")
            print(f"  {'nodeId':<12}{'fieldName':<24}{'示例值 / classType'}")
            print("  " + "-" * 74)
            for n in nodes:
                nid = str(n.get("nodeId", ""))
                fn = str(n.get("fieldName", ""))
                extra = n.get("classType") or n.get("description") or ""
                val = n.get("fieldValue")
                val_s = "" if val in (None, "") else str(val)
                if len(val_s) > 48:
                    val_s = val_s[:45] + "..."
                print(f"  {nid:<12}{fn:<24}{extra} {val_s}")
    print("\n提示：nodeId 与 fieldName 回填到 <工作区>/config.local.json 的 nodes 里"
          "（或用 skill 的 configure.py apply-nodes 自动写入）。")
    return 0


# ===========================================================================
# 主流程
# ===========================================================================
async def run_pipeline(args: argparse.Namespace) -> int:
    workspace = args._workspace
    data_dir = _resolve_against(workspace, args.data_dir)
    out_root = _resolve_against(workspace, args.out_dir)

    inputs = Inputs(data_dir)
    problems = inputs.validate()
    if problems:
        print("! 输入数据存在问题：")
        for p in problems:
            print(f"  - {p}")
        if not args.dry_run:
            print("\n（可用 --dry-run 只看问题不提交；修好后重跑）")
        if args.strict:
            return 2

    # 批次目录
    batch_id = args.resume or args.batch_id or time.strftime("%Y%m%d_%H%M%S")
    batch_dir = out_root / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    logger = Logger()
    state = StateStore(batch_dir / "state.json", batch_id)
    resumed = False
    if args.resume:
        resumed = state.load()
        await logger.log(f"断点续跑：已载入 {'成功' if resumed else '失败（作为新批次）'}"
                         f" -> {state.path}")

    # 筛选要处理的 shot
    shots = inputs.shots
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        shots = [s for s in shots if str(s.get("shot_id")) in want]
    if args.limit:
        shots = shots[: args.limit]

    fuse = Fusebox(C.FUSE_CONSECUTIVE_FAILS, C.FUSE_FAIL_RATE, C.FUSE_MIN_SAMPLES)
    provider = get_image_provider()

    await logger.log("=" * 78)
    await logger.log(f"工作区 {args._workspace}")
    await logger.log(f"批次 {batch_id} | 图片模型={C.ACTIVE_IMAGE_PROVIDER}"
                     f"({provider.__class__.__name__}) | H3 工作流={C.H3.get('id') or '未填写'}")
    await logger.log(f"待处理 {len(shots)} 个分镜 | 并发={C.CONCURRENCY} | 批大小={C.BATCH_SIZE}"
                     f" | 轮询通道={C.POLL_CHANNEL} | {'DRY-RUN 预演' if args.dry_run else '真实执行'}")
    if args.dry_run:
        await logger.log("DRY-RUN：不会发起任何 RunningHub 请求，只校验数据并组装 nodeInfoList")
    await logger.log("=" * 78)

    started = time.time()
    client_cm = (RunningHubClient(C.RUNNINGHUB_API_KEY, C.RH_BASE_URL, C.HTTP_TIMEOUT,
                                 channel=C.POLL_CHANNEL)
                 if not args.dry_run else _NullClient())
    async with client_cm as client:
        ctx = ShotContext(
            client=client,
            sem=asyncio.Semaphore(max(1, int(args.concurrency))),
            uploads=UploadCache(C.UPLOAD_CACHE_TTL_HOURS),
            logger=logger,
            state=state,
            fuse=fuse,
            data_dir=data_dir,
            output_root=batch_dir,
            characters=inputs.characters,
            scenes=inputs.scenes,
            dry_run=args.dry_run,
            video_only=bool(getattr(args, "video_only", False)),
            # 运行台账：逐任务落盘（含失败与换算指标）。预演不写，避免污染真实账。
            ledger=RunLedger(args._workspace, cny_per_rh_coin=C.RH_CNY_PER_COIN,
                             enabled=not args.dry_run),
            batch_id=batch_id,
        )

        for bi, batch in enumerate(_chunks(shots, args.batch_size), start=1):
            if fuse.blown():
                await logger.log(f"⚠ 熔断已触发，剩余 {len(shots) - (bi - 1) * args.batch_size} 个分镜不再提交")
                for s in shots[(bi - 1) * args.batch_size:]:
                    sid = str(s.get("shot_id") or "?")
                    await state.update(sid, image_status=ST_SKIPPED, video_status=ST_SKIPPED)
                break

            await logger.log(f"--- 第 {bi} 批：{len(batch)} 个分镜 ---")
            results = await asyncio.gather(
                *[process_shot(ctx, s, inputs.h3_prompts, provider) for s in batch],
                return_exceptions=True,
            )
            for s, r in zip(batch, results):
                sid = str(s.get("shot_id") or "?")
                if isinstance(r, BaseException):
                    await logger.log(f"[{sid}] ✗ 未捕获异常（已隔离，不影响其他分镜）: {truncate(r, 120)}")
                    await state.update(sid, error=truncate(r, C.ERROR_TEXT_LIMIT),
                                       image_status=ST_FAILED)
                    fuse.record(False)
                else:
                    # 只有真正 FAILED 才计入熔断；SKIPPED / PENDING（含 dry-run）不算失败
                    fuse.record(r != ST_FAILED)
            await logger.log(f"--- 第 {bi} 批完成 | 已完成 {fuse.total} | 失败 {fuse.failed} "
                             f"| 熔断={'是' if fuse.blown() else '否'} ---")

    elapsed = time.time() - started
    await state.flush()
    summary = _write_summary(batch_dir, batch_id, state, fuse, elapsed, args,
                             inputs, statuses=_collect_statuses(state))
    logger.dump(batch_dir / "run.log")

    print("\n" + "=" * 78)
    print(f"批次 {batch_id} 结束 | 耗时 {elapsed:.1f}s")
    print(f"  成功 {summary['totals']['success']} | 失败 {summary['totals']['failed']} "
          f"| 跳过 {summary['totals']['skipped']} | 未完成 {summary['totals']['pending']}")
    if fuse.blown():
        print(f"  ⚠ 熔断：{fuse.reason}")
    usage = summary["usage"]
    if usage.get("consumeMoney") or usage.get("consumeCoins"):
        print(f"  花费：{usage}")
    print(f"  输出目录：{batch_dir}")
    print(f"  汇总文件：{batch_dir / 'summary.json'}")
    print("=" * 78)
    return 0


class _NullClient:
    """DRY-RUN 用的假客户端：任何网络调用都直接报错，确保预演绝不触网。"""

    async def __aenter__(self) -> "_NullClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def _blocked(self, *a, **kw):
        raise RunningHubError("DRY-RUN 模式禁止网络调用", retriable=False)

    upload_file = download = create_task = poll_until_done = _blocked


def _collect_statuses(state: StateStore) -> Dict[str, Dict[str, Any]]:
    return state.data.get("shots") or {}


def _write_summary(batch_dir: Path, batch_id: str, state: StateStore, fuse: Fusebox,
                   elapsed: float, args: argparse.Namespace, inputs: Inputs,
                   statuses: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    totals = {"total": len(inputs.shots), "success": 0, "failed": 0, "skipped": 0, "pending": 0}
    money = coins = 0.0
    shot_rows: List[Dict[str, Any]] = []

    for shot in inputs.shots:
        sid = str(shot.get("shot_id") or "?")
        st = statuses.get(sid) or {}
        v = st.get("video_status", ST_PENDING)
        final_ok = v == ST_SUCCESS and (batch_dir / sid / "final.mp4").is_file()
        if final_ok:
            totals["success"] += 1
        elif v == ST_SKIPPED:
            totals["skipped"] += 1
        elif st.get("error") or ST_FAILED in (st.get("image_status"), st.get("crop_status"), v):
            totals["failed"] += 1
        else:
            totals["pending"] += 1

        for key in ("image_usage", "video_usage"):
            u = st.get(key) or {}
            try:
                money += float(u.get("consumeMoney") or 0)
                coins += float(u.get("consumeCoins") or 0)
            except (TypeError, ValueError):
                pass

        shot_rows.append({
            "shot_id": sid,
            "scene": shot.get("scene"),
            "image_status": st.get("image_status"),
            "crop_status": st.get("crop_status"),
            "video_status": st.get("video_status"),
            "image_task_id": st.get("image_task_id"),
            "video_task_id": st.get("video_task_id"),
            "cropped_count": len(st.get("cropped") or []),
            "raw": st.get("banana_raw"),
            "video_url": st.get("video_url"),
            "final_mp4": str(batch_dir / sid / "final.mp4") if final_ok else None,
            "error": st.get("error"),
        })

    summary = {
        "batch_id": batch_id,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_seconds": round(elapsed, 1),
        "dry_run": bool(args.dry_run),
        "video_only": bool(getattr(args, "video_only", False)),
        "resumed": bool(args.resume),
        "config": {
            "image_provider": C.ACTIVE_IMAGE_PROVIDER,
            "image_workflow_id": C.IMAGE_PROVIDERS.get(C.ACTIVE_IMAGE_PROVIDER, {}).get("id"),
            "h3_workflow_id": C.H3.get("id"),
            "concurrency": args.concurrency,
            "batch_size": args.batch_size,
            "poll_channel": C.POLL_CHANNEL,
        },
        "totals": totals,
        "fuse": fuse.status(),
        "usage": {"consumeMoney": round(money, 4), "consumeCoins": round(coins, 2)},
        "shots": shot_rows,
    }
    (batch_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


# ===========================================================================
# CLI
# ===========================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="批量 AI 视频生产流水线（RunningHub 九宫格分镜 -> MiniMax H3 视频）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workspace", default=None,
                   help="工作区根目录（data/ 与 output/ 的基准）。默认按 "
                        "--workspace > 环境变量 VIDEO_PIPELINE_WORKSPACE > "
                        "<引擎>/.workspace 指针 > ~/video-pipeline-workspace 解析")
    p.add_argument("--data-dir", default=C.DATA_DIR,
                   help=f"输入 JSON 目录（相对工作区，默认 {C.DATA_DIR}）")
    p.add_argument("--out-dir", default=C.OUTPUT_DIR,
                   help=f"输出根目录（相对工作区，默认 {C.OUTPUT_DIR}）")
    p.add_argument("--batch-id", default=None, help="指定批次 ID（默认用时间戳）")
    p.add_argument("--resume", default=None, metavar="BATCH_ID",
                   help="断点续跑：载入该批次目录下的 state.json，已完成的阶段直接跳过")
    p.add_argument("--dry-run", action="store_true",
                   help="预演：不联网、不提交，只校验数据并打印将要提交的 nodeInfoList")
    p.add_argument("--video-only", action="store_true",
                   help="跳过 ①分镜图生成 ②裁剪（不扣图费），H3 走多参模式："
                        "提示词 + 有序参考图，不传首帧图/九宫格节点")
    p.add_argument("--probe", action="store_true", help="探测工作流的真实 nodeId / fieldName")
    p.add_argument("--check", action="store_true", help="只检查哪些关键配置还没填")
    p.add_argument("--provider", default=None, choices=sorted(C.IMAGE_PROVIDERS.keys()),
                   help="临时切换图片模型（覆盖 active_image_provider，不写配置文件）")
    p.add_argument("--limit", type=int, default=None, help="只处理前 N 个分镜")
    p.add_argument("--only", default=None, help="只处理指定 shot_id，逗号分隔，如 shot_001,shot_003")
    p.add_argument("--concurrency", type=int, default=C.CONCURRENCY,
                   help=f"并发槽位数（★ 上限 {C.CONCURRENCY_MAX}，默认 {C.CONCURRENCY}）")
    p.add_argument("--batch-size", type=int, default=C.BATCH_SIZE, help=f"每批分镜数（默认 {C.BATCH_SIZE}）")
    p.add_argument("--strict", action="store_true", help="输入数据有问题时直接退出（非 0）")
    p.add_argument("--json", action="store_true",
                   help="以机器可读 JSON 输出（当前用于 --check；供 Agent 解析）")
    return p


def _bootstrap(args: argparse.Namespace) -> None:
    """解析工作区 -> 应用 config.local.json 覆盖层 -> 应用 --provider 覆盖。"""
    args._workspace = _resolve_workspace(args.workspace)
    for w in C.apply_overlay(args._workspace / C.OVERLAY_FILENAME):
        print(f"[WARN] {w}")
    if args.provider:
        C.ACTIVE_IMAGE_PROVIDER = args.provider


def _print_check(args: argparse.Namespace) -> int:
    # --video-only 时不该索要图片工作流配置（那种模式下图片 Provider 永不被构造）
    miss = C.missing_configs(video_only=bool(getattr(args, "video_only", False)))
    if args.json:
        print(json.dumps({
            "ok": not miss, "workspace": str(args._workspace),
            "active_provider": C.ACTIVE_IMAGE_PROVIDER,
            "api_key": C.mask_key(C.RUNNINGHUB_API_KEY),
            "missing": miss,
            "overlay_file": str(args._workspace / C.OVERLAY_FILENAME),
            "overlay_warnings": list(C.OVERLAY_WARNINGS),
        }, ensure_ascii=False, indent=2))
        return 0 if not miss else 1
    if not miss:
        print("✓ 必填项都已就位，可以跑 --dry-run 预演了。")
        return 0
    print(f"工作区：{args._workspace}")
    print("以下配置还没填，请补齐后再跑（用 --probe 拿到 nodeId/fieldName）：\n")
    for m in miss:
        print(f"  - {m}")
    print("\n提示：这些值写到 <工作区>/config.local.json（或设置环境变量 RUNNINGHUB_API_KEY），"
          "不要改代码。可用 skill 的 configure.py 代填。")
    return 1


def main() -> int:
    args = build_parser().parse_args()
    _bootstrap(args)

    if args.check:
        return _print_check(args)

    if args.probe:
        return asyncio.run(run_probe())

    if args.concurrency > C.CONCURRENCY_MAX and not args.dry_run:
        print(f"! RunningHub 同时最多执行 {C.CONCURRENCY_MAX} 个任务，"
              f"--concurrency 已自动收敛为 {C.CONCURRENCY_MAX}。")
        args.concurrency = C.CONCURRENCY_MAX

    if not args.dry_run:
        # 与 --dry-run 用同一套校验口径（video_only 时豁免图片工作流），
        # 避免「预演绿、真跑才被闸门挡下」的假绿。
        miss = C.missing_configs(video_only=bool(getattr(args, "video_only", False)))
        if miss:
            print("! 配置不完整，无法执行（先用 --dry-run 或 --check 查看）：")
            for m in miss:
                print(f"  - {m}")
            return 1

    return asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断。重跑时加 --resume <batch_id> 即可续跑，已完成的阶段不会重复扣费。")
        sys.exit(130)
