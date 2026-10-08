#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_ledger.py —— 运行台账（逐任务落盘，失败也记，带换算层）

为什么不是「一个大 JSON」
------------------------
流水线并发 = 5，也就是同时有 5 个任务在跑。若共写一份 JSON，
必然出现读-改-写竞争，轻则丢记录、重则文件损坏。
**所以：一个任务一个文件**，谁写谁的，零争用。

    <工作区>/ledger/runs/<batch_id>__<shot_id>.json

同一分镜重跑 = 往同一个文件的 `attempts` 数组里追加一次，不覆盖历史。
失败、跳过、成功都记 —— 否则「成本优化」只能看到成功的一半。

换算层（★ 用户要求：每个任务的执行时长与消耗都要有换算过程）
------------------------------------------------------------
原始数字（RH币、秒、字节）本身不能横向比较，必须换算成**单位经济指标**：

    rh_per_frame            每帧多少 RH币      → 判断时长档位是否划算
    cny_per_video_second    每秒成片多少钱     → 跨模型/跨路线比价
    wall_s_per_frame        每帧墙钟多少秒     → 判断是否被排队拖慢
    frames_per_min          每分钟产出多少帧   → 吞吐效率
    cny_per_mb              每 MB 产物多少钱   → 与清晰度档位对比

这些指标才是「后续优化成本与运行结构」的依据。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# RH币 -> 人民币。与 docs/runninghub-runs.json 的 rh_rate 保持一致。
DEFAULT_CNY_PER_RH_COIN = 0.0025


def frames_for(duration_s: Any) -> Optional[int]:
    """复刻 H3 工作流 node 250 的帧数表达式。

    max(5, round(a*24)) + (5 - (max(5, round(a*24)) % 17)) % 17
    """
    try:
        d = float(duration_s)
    except (TypeError, ValueError):
        return None
    n = max(5, round(d * 24))
    return n + (5 - (n % 17)) % 17


def classify_error(err: Any) -> str:
    """把错误归类，便于统计与自动决策（哪类错该重试、哪类该熔断）。"""
    s = str(err or "").lower()
    if not s:
        return ""
    if any(k in s for k in ("api key", "unauthorized", "401", "403", "鉴权", "api key不存在")):
        return "auth"
    if any(k in s for k in ("余额", "insufficient", "balance", "quota", "欠费")):
        return "balance"
    if any(k in s for k in ("审核", "content", "sensitive", "违规", "policy")):
        return "content"
    if any(k in s for k in ("timeout", "超时", "timed out")):
        return "timeout"
    if any(k in s for k in ("connection", "网络", "reset", "eof", "ssl", "proxy")):
        return "network"
    if any(k in s for k in ("参数", "invalid", "param", "不合法", "缺少", "not found", "找不到")):
        return "param"
    if any(k in s for k in ("熔断", "fuse")):
        return "fused"
    return "unknown"


def normalize(*, duration_s: Any, usage: Dict[str, Any] | None, wall_elapsed_s: Any,
              out_bytes: Any = None, cny_per_rh_coin: float = DEFAULT_CNY_PER_RH_COIN,
              frames: Optional[int] = None) -> Dict[str, Any]:
    """把原始消耗换算成可比指标。缺的量一律留 None，不臆造 0。"""
    u = usage or {}

    def _num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f

    rh = _num(u.get("consumeCoins"))
    cny = (rh * cny_per_rh_coin) if rh is not None else None
    video_s = _num(duration_s)
    wall = _num(wall_elapsed_s)
    fr = frames if frames is not None else frames_for(duration_s)
    mb = (_num(out_bytes) / 1024 / 1024) if out_bytes not in (None, "") else None

    def div(a, b):
        if a is None or b in (None, 0):
            return None
        return round(a / b, 6)

    return {
        "rh_coins": rh,
        "cny": round(cny, 6) if cny is not None else None,
        "frames": fr,
        "video_seconds": video_s,
        "wall_elapsed_s": wall,
        "out_mb": round(mb, 4) if mb is not None else None,
        # ---- 换算后的单位经济指标 ----
        "rh_per_frame": div(rh, fr),
        "cny_per_video_second": div(cny, video_s),
        "cny_per_mb": div(cny, mb),
        "wall_s_per_frame": div(wall, fr),
        "wall_s_per_video_second": div(wall, video_s),
        "frames_per_min": div((fr * 60) if fr is not None else None, wall),
        "ratio_wall_over_video": div(wall, video_s),
    }


class RunLedger:
    """逐任务台账。一个 (batch_id, shot_id) 一个文件，内部按 attempt 追加。"""

    def __init__(self, workspace: str | Path, cny_per_rh_coin: float = DEFAULT_CNY_PER_RH_COIN,
                 enabled: bool = True) -> None:
        self.root = Path(workspace) / "ledger" / "runs"
        self.rate = cny_per_rh_coin
        self.enabled = enabled

    # ---------- 路径 ----------
    def _path(self, batch_id: str, shot_id: str) -> Path:
        safe = f"{batch_id}__{shot_id}".replace("/", "_").replace("\\", "_")
        return self.root / f"{safe}.json"

    def _read(self, p: Path) -> Dict[str, Any]:
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {}

    def _write(self, p: Path, data: Dict[str, Any]) -> None:
        """原子写：先落临时文件再替换，避免并发/断电写出半个文件。"""
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)

    # ---------- 记录 ----------
    def record(self, *, batch_id: str, shot_id: str, stage: str, status: str,
               route: str = "", kind: str = "", target_id: str = "",
               task_id: Optional[str] = None, duration_s: Any = None,
               usage: Dict[str, Any] | None = None, wall_elapsed_s: Any = None,
               out_bytes: Any = None, artifact: Optional[str] = None,
               output_url: Optional[str] = None, error: Optional[str] = None,
               attempt_key: Optional[str] = None, extra: Optional[Dict[str, Any]] = None,
               episode: str = "") -> Optional[Dict[str, Any]]:
        """写一条 attempt。返回该 attempt 的字典（含换算结果）。

        attempt_key：同一次提交的生命周期标识（一般用 task_id）。
                     给了就用它定位已有 attempt 做**更新**，而不是新增 —— 
                     这样「提交时记 RUNNING → 结束时改为 SUCCESS」不会变成两条。
        """
        if not self.enabled:
            return None
        p = self._path(batch_id, shot_id)
        data = self._read(p)
        data.setdefault("batch_id", batch_id)
        data.setdefault("shot_id", shot_id)
        data.setdefault("episode", episode or (shot_id.split("_")[0] if "_" in shot_id else ""))
        data.setdefault("stage", stage)
        data.setdefault("route", route)
        data.setdefault("kind", kind)
        data.setdefault("target_id", target_id)
        data.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        attempts: List[Dict[str, Any]] = data.setdefault("attempts", [])

        norm = normalize(duration_s=duration_s, usage=usage, wall_elapsed_s=wall_elapsed_s,
                         out_bytes=out_bytes, cny_per_rh_coin=self.rate)

        # attempt_key 默认取 task_id：这样「提交时写 RUNNING」→「结束时写 SUCCESS/FAILED」
        # 会自动**更新同一条** attempt，调用方不必记得传，杜绝"一次任务两条记录"。
        # 没有 task_id（例如提交前就失败了）时保持 None → 每次新建 attempt，
        # 这样重试历史能累积下来，而不是被覆盖。
        key = attempt_key or task_id
        entry = None
        if key:
            entry = next((a for a in attempts if a.get("attempt_key") == key), None)
        if entry is None:
            entry = {
                "attempt": len(attempts) + 1,
                "attempt_key": key,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            attempts.append(entry)

        entry.update({
            "status": status,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "task_id": task_id or entry.get("task_id"),
            "duration_s": duration_s,
            "usage": usage or entry.get("usage") or {},
            "wall_elapsed_s": wall_elapsed_s,
            "artifact": artifact or entry.get("artifact"),
            "output_url": output_url or entry.get("output_url"),
            "error": error,
            "error_class": classify_error(error) if error else "",
            # 失败且没有 usage → 未计费。明确写出来，避免"失败=白花钱"的误判。
            "billed": bool(norm.get("rh_coins")),
            "normalized": norm,
        })
        if extra:
            entry.update(extra)
        if status in ("SUCCESS", "FAILED", "SKIPPED", "CANCELLED"):
            entry["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        self._write(p, data)
        return entry
