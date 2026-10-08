# -*- coding: utf-8 -*-
"""
pipeline.py —— 编排层：重试、裁剪、Provider 接口、单分镜五阶段流水线

一个 shot 的完整流程：
  ① 图生图出九宫格   （分镜提示词 + 角色参考图 + 场景参考图 -> banana_raw.png）
  ② 九宫格裁剪编号   （3×3 -> shot_001_01.png … shot_001_09.png）
  ③ 取 H3 提示词     （从 h3_prompts.json 按 shot_id 取，缺失即失败，不猜）
  ④ 匹配参考图       （characters.json / scenes.json 按 refs 解析）
  ⑤ 生成 H3 视频     （文本 + 首帧图 + 参考图 -> final.mp4）

并发：全局 asyncio.Semaphore(5) 包住「提交任务 + 轮询直到终态」整段生命周期。
     下载放在信号量之外（下载不占用 RunningHub 任务槽位）。
"""
from __future__ import annotations

import asyncio
import json
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from PIL import Image

import config as C
from runninghub_client import (
    RunningHubClient, RunningHubError, extract_usage, pick_output_url, truncate,
)

# 阶段状态
ST_PENDING = "PENDING"
ST_RUNNING = "RUNNING"
ST_SUCCESS = "SUCCESS"
ST_FAILED = "FAILED"
ST_SKIPPED = "SKIPPED"


# ===========================================================================
# 日志
# ===========================================================================
class Logger:
    """带时间戳的进度日志，同时缓存到内存供 run.log 落盘。"""

    def __init__(self) -> None:
        self.lines: List[str] = []
        self._lock = asyncio.Lock()

    def _stamp(self) -> str:
        return time.strftime("%H:%M:%S")

    async def log(self, msg: str) -> None:
        line = f"[{self._stamp()}] {msg}"
        async with self._lock:
            print(line, flush=True)
            self.lines.append(line)

    def log_sync(self, msg: str) -> None:
        line = f"[{self._stamp()}] {msg}"
        print(line, flush=True)
        self.lines.append(line)

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines), encoding="utf-8")


# ===========================================================================
# 通用重试
# ===========================================================================
async def retry_async(fn: Callable[[], Any], *, max_retry: int = 3,
                      backoff: Sequence[float] = (5, 15, 45),
                      label: str = "", logger: Optional[Logger] = None) -> Any:
    """通用重试：最多 max_retry 次重试（退避 backoff），只重试标记为瞬时错误的异常。

    - RunningHubError.retriable 决定是否值得重试（401/403/参数错等确定性错误不重试）
    - create 失败 -> 重试 create；create 成功但 poll 失败 -> 只重试 poll（见 run_task）
    """
    attempt = 0
    while True:
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - 需要按 retriable 分流
            attempt += 1
            retriable = bool(getattr(e, "retriable", False))
            if attempt > max_retry or not retriable:
                raise
            wait = backoff[min(attempt - 1, len(backoff) - 1)]
            if logger:
                await logger.log(f"    ↻ {label} 第 {attempt}/{max_retry} 次重试，{wait}s 后继续：{truncate(e, 120)}")
            await asyncio.sleep(wait)


# ===========================================================================
# 九宫格裁剪
# ===========================================================================
def crop_grid(src: str | Path, out_dir: str | Path, rows: int, cols: int,
              prefix: str, *, log: Optional[Callable[[str], None]] = None) -> List[str]:
    """把一张图按 rows×cols 均匀裁剪，行优先编号（与九宫格 position 1-1 -> 3-3 对齐）。

    编号从 01 开始：{prefix}01.png … {prefix}09.png
    宽高不能被整除时，最后一列/行吃掉余数，避免留 1px 黑边与累计误差。
    """
    rows = max(1, int(rows))
    cols = max(1, int(cols))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        xs = [round(w * i / cols) for i in range(cols)] + [w]
        ys = [round(h * i / rows) for i in range(rows)] + [h]

        paths: List[str] = []
        n = 0
        for r in range(rows):
            for c in range(cols):
                n += 1
                box = (xs[c], ys[r], xs[c + 1], ys[r + 1])
                tile = im.crop(box)
                dest = out_dir / f"{prefix}{n:02d}.png"
                tile.save(dest, format="PNG")
                paths.append(str(dest))
        if log:
            log(f"    {rows}×{cols} 裁剪完成，共 {len(paths)} 格（{w}×{h} -> 每格约 {w//cols}×{h//rows}）")
        return paths


def _maybe_unzip_single_image(path: Path, dest: Path) -> Optional[Path]:
    """香蕉若回传 zip 包，取出里面第一张图。返回新路径；非 zip 返回 None。"""
    try:
        if not zipfile.is_zipfile(path):
            return None
        # 防止把提取结果覆盖到正在读取的 zip 自身（同名时会损坏源文件）
        if dest.resolve() == path.resolve():
            dest = path.with_name(f"{path.stem}_extracted.png")
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    with z.open(name) as fh:
                        dest.write_bytes(fh.read())
                    return dest
    except (zipfile.BadZipFile, OSError):
        return None
    return None


# ===========================================================================
# 参考图解析 + 上传缓存
# ===========================================================================
def is_direct_url(value: str) -> bool:
    """RunningHub 可直接接受公开 URL 或 base64 data URI，无需上传。"""
    v = str(value or "").strip().lower()
    return v.startswith(("http://", "https://", "data:"))


class UploadCache:
    """本地文件 -> RunningHub download_url 的缓存（含 TTL）。

    上传链接仅 1 天有效，超过 TTL 会重新上传。
    同一个文件被多个 shot 复用时只上传一次；并发请求同一文件时用 per-key 锁去重。
    """

    def __init__(self, ttl_hours: float = 20.0) -> None:
        self.ttl_seconds = max(1.0, ttl_hours) * 3600.0
        self._data: Dict[str, Dict[str, Any]] = {}
        self._key_locks: Dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    @staticmethod
    def _key(path: Path) -> str:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return f"{path.resolve()}::{mtime}"

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._guard:
            if key not in self._key_locks:
                self._key_locks[key] = asyncio.Lock()
            return self._key_locks[key]

    async def get_or_upload_detail(self, client: RunningHubClient,
                                   local_path: str | Path) -> Dict[str, Any]:
        """返回 {"url": download_url, "fileName": fileName}。

        ★ 为什么要 fileName：ComfyUI 的 LoadImage.image 字段要的是**上传后的文件名**
        （形如 openapi/<sha256>.jpg），不是 download_url。2026-09-18 实测确认。
        """
        p = Path(local_path)
        if not p.is_file():
            raise RunningHubError(f"参考图文件不存在: {p}")
        key = self._key(p)
        cached = self._data.get(key)
        if cached and (time.time() - cached["at"]) < self.ttl_seconds:
            return cached
        lock = await self._lock_for(key)
        async with lock:
            cached = self._data.get(key)
            if cached and (time.time() - cached["at"]) < self.ttl_seconds:
                return cached
            up = await client.upload_file(str(p))
            self._data[key] = {"url": up["download_url"], "at": time.time(),
                               "fileName": up.get("fileName")}
            return self._data[key]

    async def get_or_upload(self, client: RunningHubClient, local_path: str | Path) -> str:
        """兼容旧调用：只要 download_url。"""
        return (await self.get_or_upload_detail(client, local_path))["url"]


async def resolve_refs(ctx: "ShotContext", refs: Sequence[str], table: Dict[str, str],
                       table_name: str, base_dir: Path,
                       value_form: str = "url") -> Tuple[List[str], List[Dict[str, Any]]]:
    """把 refs 的 key 列表解析成可直接提交的值列表，并返回快照（便于排查）。

    值形态：http(s)/data: 直接透传；否则按本地路径（相对 base_dir）上传。

    value_form:
      "url"      -> 提交 download_url（适用于 imageUrls 这类数组型输入节点）
      "fileName" -> 提交上传后的 fileName（适用于 ComfyUI LoadImage.image）
    """
    values: List[str] = []
    snapshot: List[Dict[str, Any]] = []
    for key in refs or []:
        raw = table.get(key)
        if not raw:
            raise RunningHubError(f"{table_name}.json 里找不到 key='{key}'")
        if is_direct_url(raw):
            # 直传 URL 没有 fileName 概念，原样透传（两条路都这样，避免悄悄丢图）
            values.append(raw)
            snapshot.append({"key": key, "kind": "url", "source": raw, "submitted": raw})
            continue
        local = Path(raw)
        if not local.is_absolute():
            local = base_dir / local
        if ctx.dry_run:
            val = f"[将上传] {local}"
        else:
            detail = await ctx.uploads.get_or_upload_detail(ctx.client, local)
            val = detail["fileName"] if value_form == "fileName" else detail["url"]
            if value_form == "fileName" and not val:
                raise RunningHubError(f"上传未返回 fileName，无法用于 LoadImage: {local}")
        values.append(val)
        snapshot.append({"key": key, "kind": "local-upload", "form": value_form,
                         "source": str(local), "submitted": val})
    return values, snapshot


def _join_values(values: Sequence[str], multi: bool) -> Optional[str]:
    """按配置决定单值 / JSON 数组字符串。

    multi=True  -> 序列化成 JSON 数组字符串（给数组型输入节点，如 imageUrls）
    multi=False -> 单值；若有多张则取第一张（多图请改用 multi 节点或多节点映射）
    """
    vals = [v for v in (values or []) if v]
    if not vals:
        return None
    if multi:
        return json.dumps(vals, ensure_ascii=False)
    return vals[0]


# ===========================================================================
# 运行时上下文
# ===========================================================================
class CtxBase:
    pass


@dataclass
class ShotContext:
    """贯穿整条流水线的共享上下文（所有协程共用一份）。"""
    client: RunningHubClient
    sem: asyncio.Semaphore
    uploads: UploadCache
    logger: Logger
    state: Any                      # StateStore（main.py 实现）
    fuse: Any                       # Fusebox（main.py 实现）
    data_dir: Path
    output_root: Path
    characters: Dict[str, str] = field(default_factory=dict)
    scenes: Dict[str, str] = field(default_factory=dict)
    dry_run: bool = False
    # --video-only：跳过 ①出图 ②裁剪（不扣图费），H3 走多参 reference generation（无首帧/无九宫格）
    video_only: bool = False
    # 运行台账（run_ledger.RunLedger）：逐任务落盘，失败也记，带换算层
    ledger: Any = None
    batch_id: str = ""

    def log(self, msg: str) -> None:
        self.logger.log_sync(msg)


# ===========================================================================
# Provider 接口（为以后接 OpenAI / 即梦官方 API 预留）
# ===========================================================================
@dataclass
class ImageRequest:
    shot_id: str
    prompt: str
    ratio: Optional[str]
    character_refs: List[str] = field(default_factory=list)
    scene_refs: List[str] = field(default_factory=list)
    out_path: Path = Path("banana_raw.png")
    crop: Dict[str, int] = field(default_factory=lambda: dict(C.DEFAULT_CROP))
    # 提交成功后立刻回调，用于把 taskId 落盘（崩溃后重跑可复用，不重复扣费）
    on_task_created: Optional[Callable[[str], Any]] = None
    # 断点续跑：若上一轮已提交过任务，这里带原 taskId，只轮询不重复提交
    reuse_task_id: Optional[str] = None


@dataclass
class ImageResult:
    local_path: str
    task_id: Optional[str] = None
    result_url: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)


class ImageProvider(Protocol):
    """图片生成 Provider 契约：吃掉一个 ImageRequest，落盘一张本地图片。

    要接 GPT 图像 / 即梦官方 API：新建一个类实现 generate()，
    然后注册进 IMAGE_PROVIDER_REGISTRY，并在 config.IMAGE_PROVIDERS 里加一条 provider 名。
    """

    name: str

    async def generate(self, ctx: ShotContext, req: ImageRequest) -> ImageResult: ...


# ===========================================================================
# 任务执行：信号量包住「提交 + 轮询直到终态」
# ===========================================================================
async def run_task(ctx: ShotContext, *, kind: str, target_id: Any,
                   node_info_list: List[Dict[str, Any]], timeout_min: float,
                   tag: str, reuse_task_id: Optional[str] = None,
                   on_created: Optional[Callable[[str], Any]] = None) -> Tuple[str, Dict[str, Any]]:
    """提交（或复用）任务并轮询到终态。返回 (task_id, 归一化查询结果)。

    ★ 信号量包住的是整段「提交 + 轮询直到终态」，而不仅是提交请求。
      提交成功、还在轮询中的任务，依然占着一个槽位 —— 这是用户明确要求的行为。
      下载故意放在信号量之外：下载不占用 RunningHub 的任务槽位。

    ★ 断点续跑关键：若 reuse_task_id 存在，直接跳过提交、复用原 taskId 轮询，
      绝不重复提交（不重复扣费）。on_created 在提交成功后立刻回调，用于把
      task_id 落盘，这样进程崩溃后重跑也能复用。
    """
    if reuse_task_id:
        ctx.log(f"    ↺ 复用已提交的 taskId {reuse_task_id}，跳过重复提交")
        task_id = str(reuse_task_id)
    else:
        async with ctx.sem:      # 槽位从「提交」开始占用，直到轮询到终态才释放
            ctx.log(f"    → {tag} 提交任务 …")
            task_id = await retry_async(
                lambda: ctx.client.create_task(kind, target_id, node_info_list),
                max_retry=C.RETRY_MAX, backoff=C.RETRY_BACKOFF,
                label=f"{tag} 提交", logger=ctx.logger,
            )
            if on_created:
                await on_created(task_id)
            result = await retry_async(
                lambda: ctx.client.poll_until_done(
                    task_id, timeout_min=timeout_min, interval=C.POLL_INTERVAL),
                max_retry=C.RETRY_MAX, backoff=C.RETRY_BACKOFF,
                label=f"{tag} 轮询", logger=ctx.logger,
            )
        _check_status(result, tag)
        return task_id, result

    # 复用路径：只轮询，不提交
    async with ctx.sem:
        result = await retry_async(
            lambda: ctx.client.poll_until_done(
                task_id, timeout_min=timeout_min, interval=C.POLL_INTERVAL),
            max_retry=C.RETRY_MAX, backoff=C.RETRY_BACKOFF,
            label=f"{tag} 轮询(复用)", logger=ctx.logger,
        )
    _check_status(result, tag)
    return task_id, result


def _check_status(result: Dict[str, Any], tag: str) -> None:
    status = result.get("status")
    if status == "SUCCESS":
        return
    reason = (result.get("raw") or {}).get("errorMessage") or \
             (result.get("raw") or {}).get("failedReason") or \
             (result.get("raw") or {}).get("error") or "任务执行失败"
    raise RunningHubError(f"{tag} 返回 {status}: {truncate(reason, 200)}", retriable=False)


# ===========================================================================
# 图片 Provider：RunningHub
# ===========================================================================
def build_image_node_list(prov: Dict[str, Any], *, prompt: str, ratio: Optional[str],
                          char_values: List[str], scene_values: List[str]) -> List[Dict[str, Any]]:
    """按模型注册表的 nodes 映射拼 nodeInfoList（图生图：提示词 + 角色图 + 场景图）。"""
    nodes = prov.get("nodes") or {}
    pairs: List[tuple] = []

    n = nodes.get("prompt")
    if n and prompt:
        pairs.append((n.get("nodeId"), n.get("fieldName"), prompt))

    n = nodes.get("ratio")
    if n and ratio:
        pairs.append((n.get("nodeId"), n.get("fieldName"), ratio))

    # 优先用分离的 char_ref / scene_ref 节点；没有则合并进单一 image 节点
    if nodes.get("char_ref") or nodes.get("scene_ref"):
        for key, vals in (("char_ref", char_values), ("scene_ref", scene_values)):
            n = nodes.get(key)
            if not n or not n.get("nodeId"):
                continue
            joined = _join_values(vals, bool(n.get("multi")))
            if joined:
                pairs.append((n.get("nodeId"), n.get("fieldName"), joined))
    else:
        n = nodes.get("image")
        if n and n.get("nodeId"):
            joined = _join_values(list(char_values) + list(scene_values), bool(n.get("multi")))
            if joined:
                pairs.append((n.get("nodeId"), n.get("fieldName"), joined))

    return RunningHubClient.build_node_list(pairs)


class RunningHubImageProvider:
    """默认 Provider：走 RunningHub 工作流 / AI 应用出图。"""

    name = "runninghub"

    def __init__(self, provider_key: str) -> None:
        self.provider_key = provider_key
        self.prov = C.IMAGE_PROVIDERS[provider_key]

    async def generate(self, ctx: ShotContext, req: ImageRequest) -> ImageResult:
        if req.reuse_task_id:
            # 复用路径：任务早就提交过了，只需轮询 —— 不再上传参考图、不再拼 nodeInfoList
            node_list: List[Dict[str, Any]] = []
        else:
            char_values, _char_snap = await resolve_refs(
                ctx, req.character_refs, ctx.characters, "characters", ctx.data_dir)
            scene_values, _scene_snap = await resolve_refs(
                ctx, req.scene_refs, ctx.scenes, "scenes", ctx.data_dir)
            # 说明：参考图快照由流水线阶段④统一写 refs_used.json，这里只需解析出可提交值
            node_list = build_image_node_list(
                self.prov, prompt=req.prompt, ratio=req.ratio,
                char_values=char_values, scene_values=scene_values)

        if ctx.dry_run:
            ctx.log(f"    [DRY-RUN] 图片 nodeInfoList = {json.dumps(node_list, ensure_ascii=False)}")
            return ImageResult(local_path="", task_id=None)

        task_id, result = await run_task(
            ctx, kind=self.prov.get("kind", "workflow"), target_id=self.prov.get("id"),
            node_info_list=node_list, timeout_min=C.IMAGE_TIMEOUT_MIN,
            tag=f"分镜图({self.prov.get('label') or self.provider_key})",
            reuse_task_id=req.reuse_task_id,
            on_created=req.on_task_created,
        )
        url = pick_output_url(result, "image")
        if not url:
            raise RunningHubError(
                f"任务成功但没找到图片产物（results={truncate(result.get('results'), 200)}）",
                retriable=False)

        # 下载放在信号量之外：不占用 RunningHub 的任务槽位
        await ctx.client.download(url, str(req.out_path))
        fixed = _maybe_unzip_single_image(req.out_path, req.out_path.with_name("banana_raw.png"))
        local = str(fixed or req.out_path)
        return ImageResult(local_path=local, task_id=task_id, result_url=url,
                           usage=extract_usage(result))


IMAGE_PROVIDER_REGISTRY: Dict[str, type] = {
    "runninghub": RunningHubImageProvider,     # 以后注册 OpenAIImageProvider / JimengProvider 到此
}


def get_image_provider() -> ImageProvider:
    key = C.ACTIVE_IMAGE_PROVIDER
    prov = C.get_active_provider()
    impl_key = str(prov.get("provider") or "runninghub")
    if impl_key not in IMAGE_PROVIDER_REGISTRY:
        raise KeyError(f"未注册的图片 Provider 实现 '{impl_key}'，已注册：{list(IMAGE_PROVIDER_REGISTRY)}")
    return IMAGE_PROVIDER_REGISTRY[impl_key](key)


# ===========================================================================
# H3 视频
# ===========================================================================
def build_h3_node_list(*, prompt: str, frame_url: Optional[str], char_values: List[str],
                       scene_values: List[str], grid_url: Optional[str],
                       duration: Any, ratio: Optional[str]) -> List[Dict[str, Any]]:
    """组装 H3 nodeInfoList。配置里 nodeId 为空的节点自动跳过。

    ★ 支持两种节点形态（2026-09-18 新增，为「本地 RH 接口版」这类工作流而改）：

    1) 单节点（旧行为）—— 一个 nodeId 吃多张图，`multi: true` 时拼成 JSON 数组：
         "char_ref": {"nodeId": "49", "fieldName": "image", "multi": true}

    2) 有序多槽位（新增）—— N 个独立节点，按顺序各吃一张图：
         "char_ref": {"value": "fileName",
                      "slots": [{"nodeId": "51", "fieldName": "image"},
                                {"nodeId": "49", "fieldName": "image"},
                                ... §共 N 个]}
    适用：H3「本地RH接口版」/对应 AI 应用有 6 个各自独立的 LoadImage（image1..image6）。
    第 i 个参考图落到第 i 个槽位；参考图少于槽位时，后面的槽位**不发送**
    （实测：不发 = 沿用工作流内置默认，安全，不会塞进多余角色）。
    """
    nodes = C.H3.get("nodes") or {}

    def node(key: str) -> Dict[str, Any]:
        return nodes.get(key) or {}

    def role_pairs(key: str, values: Sequence[str]) -> List[tuple]:
        """把某个角色（char_ref / scene_ref）展开成 [(nodeId, fieldName, value), ...]。"""
        spec = node(key)
        slots = spec.get("slots")
        if isinstance(slots, list) and slots:
            vals = [v for v in (values or []) if v]
            out: List[tuple] = []
            for i, slot in enumerate(slots):
                if not isinstance(slot, dict):
                    continue
                out.append((slot.get("nodeId"), slot.get("fieldName"),
                            vals[i] if i < len(vals) else None))
            return out
        return [(spec.get("nodeId"), spec.get("fieldName"),
                 _join_values(values, bool(spec.get("multi"))))]

    pairs: List[tuple] = [
        (node("prompt").get("nodeId"), node("prompt").get("fieldName"), prompt),
        (node("first_frame").get("nodeId"), node("first_frame").get("fieldName"), frame_url),
    ]
    pairs += role_pairs("char_ref", char_values)
    pairs += role_pairs("scene_ref", scene_values)

    def mapped(key: str, value: Any) -> Any:
        """按节点配置的 value_map 换值。

        为什么需要：工作流的 ResolutionSelector.aspect_ratio 只认带人话后缀的枚举，
        如 "16:9 (Widescreen)"，而我们的 shots.json 里写的是裸 "16:9"。
        没有这层映射会把裸值直接送进去 → 比例节点取值非法。
        """
        spec = node(key)
        vm = spec.get("value_map")
        if isinstance(vm, dict) and value is not None:
            return vm.get(str(value), value)
        return value

    def dur_str(v: Any) -> Optional[str]:
        """时长规范化：数据里是 float（13.0），但工作流要整数秒。

        帧数公式 round(a*24) 本来就把小数抹平，所以 13.0 与 13 帧数相同；
        但送 "13.0" 给 PrimitiveFloat 属于不必要的风险 —— 整数就送整数。
        """
        if v in (None, ""):
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v)
        return str(int(f)) if f == int(f) else str(f)

    pairs += [
        (node("grid_ref").get("nodeId"), node("grid_ref").get("fieldName"), grid_url),
        (node("duration").get("nodeId"), node("duration").get("fieldName"), dur_str(duration)),
        (node("ratio").get("nodeId"), node("ratio").get("fieldName"), mapped("ratio", ratio)),
    ]
    # 固定值节点（如 megapixels）：{"megapixels": {"nodeId":"252","fieldName":"megapixels","value":"0.5"}}
    for _k, spec in (C.H3.get("static_nodes") or {}).items():
        if isinstance(spec, dict) and spec.get("nodeId") and spec.get("fieldName"):
            pairs.append((spec.get("nodeId"), spec.get("fieldName"),
                          None if spec.get("value") is None else str(spec.get("value"))))
    return RunningHubClient.build_node_list(pairs)


# ===========================================================================
# 单分镜流水线
# ===========================================================================
def _load_h3_prompt(prompts: Dict[str, Any], shot_id: str) -> Dict[str, Any]:
    item = prompts.get(shot_id)
    if item is None:
        raise RunningHubError(f"h3_prompts.json 缺少 shot_id='{shot_id}' 的提示词入口", retriable=False)
    if isinstance(item, str):
        return {"prompt": item}
    if isinstance(item, dict) and item.get("prompt"):
        return item
    raise RunningHubError(f"h3_prompts.json['{shot_id}'] 结构不合法（需要 {{prompt, duration?, ratio?}}）",
                          retriable=False)


def normalize_crop(shot: Dict[str, Any]) -> Dict[str, int]:
    crop = shot.get("crop") or C.DEFAULT_CROP
    if isinstance(crop, str):
        parts = crop.lower().replace("×", "x").split("x")
        if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
            return {"rows": int(parts[0]), "cols": int(parts[1])}
        raise RunningHubError(f"crop 字符串无法解析: {crop}（应形如 '3x3'）", retriable=False)
    if isinstance(crop, dict):
        return {"rows": int(crop.get("rows", C.DEFAULT_CROP["rows"])),
                "cols": int(crop.get("cols", C.DEFAULT_CROP["cols"]))}
    raise RunningHubError(f"crop 配置不合法: {crop}", retriable=False)


def _record_run(ctx: "ShotContext", *, shot_id: str, status: str, stage: str = "h3-video",
                task_id: Optional[str] = None, duration_s: Any = None,
                usage: Optional[Dict[str, Any]] = None, wall_elapsed_s: Any = None,
                out_bytes: Any = None, artifact: Optional[str] = None,
                output_url: Optional[str] = None, error: Optional[str] = None) -> None:
    """往运行台账写一条。**台账失败绝不能拖垮主流程**，所以整体 try 兜住。

    attempt_key 用 task_id：这样「提交时写 RUNNING」与「结束时写 SUCCESS/FAILED」
    会更新同一条 attempt，而不是变成两条。
    **只有 task_id 为空**（提交前就失败）时才传 None —— 那种情况该每次新建 attempt，
    好让重试历史累积下来。
    """
    if ctx.ledger is None:
        return
    try:
        ctx.ledger.record(
            batch_id=ctx.batch_id or "unknown", shot_id=shot_id, stage=stage, status=status,
            route=str(C.H3.get("kind") or ""), kind=str(C.H3.get("kind") or ""),
            target_id=str(C.H3.get("id") or ""), task_id=task_id, duration_s=duration_s,
            usage=usage, wall_elapsed_s=wall_elapsed_s, out_bytes=out_bytes,
            artifact=artifact, output_url=output_url, error=error,
            attempt_key=task_id,
        )
    except Exception as e:  # noqa: BLE001 - 台账是旁路，不许影响出片
        try:
            ctx.log(f"[{shot_id}] [WARN] 台账写入失败（不影响主流程）: {truncate(e, 120)}")
        except Exception:  # noqa: BLE001
            pass


async def process_shot(ctx: ShotContext, shot: Dict[str, Any], h3_prompts: Dict[str, Any],
                       provider: ImageProvider) -> str:
    """处理单个分镜，返回最终状态（SUCCESS / FAILED / SKIPPED）。绝不抛出，不阻塞其他 shot。"""
    shot_id = str(shot.get("shot_id") or "").strip()
    if not shot_id:
        await ctx.logger.log("[?] 跳过一条缺少 shot_id 的记录")
        return ST_FAILED

    sdir = ctx.output_root / shot_id
    sdir.mkdir(parents=True, exist_ok=True)
    cropped_dir = sdir / "cropped"
    state = ctx.state.get(shot_id)          # 指向 state.json 里的活引用，随 update() 同步变化
    crop = normalize_crop(shot)
    ctx.log(f"[{shot_id}] === 开始处理 {crop['rows']}×{crop['cols']} 九宫格 ===")

    _t_video: Optional[float] = None        # 供失败路径算墙钟耗时

    try:
        # ---------- ① 图生图出分镜图 ----------
        raw_path = sdir / "banana_raw.png"
        if ctx.video_only:
            ctx.log(f"[{shot_id}] ① --video-only：跳过分镜图生成（不扣图费）")
            await ctx.state.update(shot_id, image_status=ST_SKIPPED)
        elif state.get("image_status") == ST_SUCCESS and raw_path.is_file():
            ctx.log(f"[{shot_id}] ① 分镜图已存在，跳过")
        else:
            if ctx.fuse.blown():
                await ctx.state.update(shot_id, image_status=ST_SKIPPED)
                ctx.log(f"[{shot_id}] ① 熔断已触发，跳过提交")
                return ST_SKIPPED
            await ctx.state.update(shot_id, image_status=ST_RUNNING, error=None)

            async def _on_image_created(tid: str) -> None:
                await ctx.state.update(shot_id, image_task_id=tid)

            # 断点续跑：上一轮已提交过出图任务但没跑完 -> 复用 taskId 只轮询，不重复扣费
            img_reuse = None
            if state.get("image_task_id") and state.get("image_status") == ST_RUNNING:
                img_reuse = state["image_task_id"]

            req = ImageRequest(
                shot_id=shot_id,
                prompt=str(shot.get("banana_prompt") or shot.get("prompt") or ""),
                ratio=shot.get("ratio"),
                character_refs=list(shot.get("character_refs") or []),
                scene_refs=list(shot.get("scene_refs") or []),
                out_path=raw_path,
                crop=crop,
                on_task_created=_on_image_created,
                reuse_task_id=img_reuse,
            )
            if not req.prompt:
                raise RunningHubError("shots.json 缺少 banana_prompt / prompt", retriable=False)

            res = await provider.generate(ctx, req)

            if ctx.dry_run:
                await ctx.state.update(shot_id, image_status=ST_PENDING)
                ctx.log(f"[{shot_id}] ① [DRY-RUN] 已完成 nodeInfoList 组装")
            else:
                await ctx.state.update(shot_id, image_status=ST_SUCCESS,
                                       banana_raw=res.local_path,
                                       image_result_url=res.result_url,
                                       image_usage=res.usage or {})
                ctx.log(f"[{shot_id}] ① 生成分镜图完成 -> {Path(res.local_path).name}")

        # ---------- ② 裁剪 + 编号 ----------
        if ctx.video_only:
            ctx.log(f"[{shot_id}] ② --video-only：跳过裁剪（无分镜图可裁）")
            await ctx.state.update(shot_id, crop_status=ST_SKIPPED)
        elif ctx.dry_run:
            ctx.log(f"[{shot_id}] ② [DRY-RUN] 将裁剪为 {crop['rows']}×{crop['cols']} = "
                    f"{crop['rows'] * crop['cols']} 格")
        elif state.get("crop_status") == ST_SUCCESS and _cropped_intact(state, crop):
            ctx.log(f"[{shot_id}] ② 裁剪结果已存在，跳过")
        else:
            await ctx.state.update(shot_id, crop_status=ST_RUNNING)
            paths = crop_grid(raw_path, cropped_dir, crop["rows"], crop["cols"],
                              f"{shot_id}_", log=ctx.log)
            await ctx.state.update(shot_id, crop_status=ST_SUCCESS, cropped=paths)
            ctx.log(f"[{shot_id}] ② 裁剪完成 -> {len(paths)} 张（{shot_id}_01 … "
                    f"{shot_id}_{len(paths):02d}）")

        # ---------- 整条链路是否已完成？ ----------
        final_mp4 = sdir / "final.mp4"
        if (not ctx.dry_run and state.get("video_status") == ST_SUCCESS and final_mp4.is_file()):
            ctx.log(f"[{shot_id}] ✓ 视频已存在，该分镜全部完成，跳过（不再重复上传参考图）")
            return ST_SUCCESS

        # ---------- ③ 取 H3 提示词 ----------
        h3 = _load_h3_prompt(h3_prompts, shot_id)
        (sdir / "h3_prompt.json").write_text(
            json.dumps(h3, ensure_ascii=False, indent=2), encoding="utf-8")
        ctx.log(f"[{shot_id}] ③ 已载入 H3 提示词（{len(str(h3.get('prompt')))} 字"
                f"{', duration=' + str(h3.get('duration')) if h3.get('duration') else ''}）")

        # ---------- ④ 匹配参考图 ----------
        # 取值形态由节点配置决定：LoadImage 型节点要 fileName，数组型输入要 download_url
        h3_nodes = (C.H3.get("nodes") or {})
        char_form = (h3_nodes.get("char_ref") or {}).get("value") or "url"
        scene_form = (h3_nodes.get("scene_ref") or {}).get("value") or "url"
        char_values, char_snap = await resolve_refs(
            ctx, shot.get("character_refs") or [], ctx.characters, "characters", ctx.data_dir,
            value_form=char_form)
        scene_values, scene_snap = await resolve_refs(
            ctx, shot.get("scene_refs") or [], ctx.scenes, "scenes", ctx.data_dir,
            value_form=scene_form)
        (sdir / "refs_used.json").write_text(json.dumps(
            {"characters": char_snap, "scenes": scene_snap}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        ctx.log(f"[{shot_id}] ④ 参考图匹配完成（角色 {len(char_values)} 张 / 场景 {len(scene_values)} 张）")

        # ---------- ⑤ 生成视频 ----------
        reuse_task_id = None
        if state.get("video_task_id") and state.get("video_status") in (ST_RUNNING, "SUBMITTING"):
            reuse_task_id = state["video_task_id"]

        if ctx.fuse.blown():
            await ctx.state.update(shot_id, video_status=ST_SKIPPED)
            ctx.log(f"[{shot_id}] ⑤ 熔断已触发，跳过提交")
            return ST_SKIPPED

        await ctx.state.update(shot_id, video_status=ST_RUNNING, error=None)

        # 首帧图 & 九宫格整图（复用路径下不需要重新构造/上传）
        frame_url = grid_url = None
        if ctx.video_only:
            pass    # 多参 reference generation：不需要首帧图 / 九宫格整图
        elif reuse_task_id:
            frame_url = state.get("frame_url")
            grid_url = state.get("grid_url")
        else:
            cropped = state.get("cropped") or []
            if ctx.dry_run and not cropped:
                frame_local = raw_path          # 预演时还没裁剪，用原图占位
            elif C.H3.get("frame_source") == "grid_raw":
                frame_local = raw_path
            else:
                if not cropped:
                    raise RunningHubError("裁剪结果为空，无法确定首帧图", retriable=False)
                frame_local = Path(cropped[0])
            if ctx.dry_run:
                frame_url = f"[将上传] {frame_local}"
                grid_url = f"[将上传] {raw_path}"
            else:
                frame_url = await ctx.uploads.get_or_upload(ctx.client, frame_local)
                grid_url = await ctx.uploads.get_or_upload(ctx.client, raw_path)

        node_list = build_h3_node_list(
            prompt=str(h3.get("prompt")), frame_url=frame_url,
            char_values=char_values, scene_values=scene_values, grid_url=grid_url,
            duration=h3.get("duration"), ratio=h3.get("ratio") or shot.get("ratio"))

        if ctx.dry_run:
            ctx.log(f"[{shot_id}] ⑤ [DRY-RUN] H3 nodeInfoList = "
                    f"{json.dumps(node_list, ensure_ascii=False)}")
            await ctx.state.update(shot_id, video_status=ST_PENDING)
            return ST_PENDING

        async def _on_video_created(tid: str) -> None:
            await ctx.state.update(shot_id, video_task_id=tid, video_status=ST_RUNNING,
                                   frame_url=frame_url, grid_url=grid_url)
            _record_run(ctx, shot_id=shot_id, status="RUNNING", task_id=tid,
                        duration_s=h3.get("duration"))

        _t_video = time.time()
        task_id, result = await run_task(
            ctx, kind=C.H3.get("kind", "workflow"), target_id=C.H3.get("id"),
            node_info_list=node_list, timeout_min=C.VIDEO_TIMEOUT_MIN, tag="H3 视频",
            reuse_task_id=reuse_task_id, on_created=_on_video_created,
        )
        vurl = pick_output_url(result, "video")
        if not vurl:
            raise RunningHubError(
                f"H3 任务成功但没找到视频产物（results={truncate(result.get('results'), 200)}）",
                retriable=False)

        await ctx.client.download(vurl, str(final_mp4))
        (sdir / "video_task.json").write_text(json.dumps({
            "task_id": task_id, "video_url": vurl, "channel": result.get("_channel"),
            "usage": extract_usage(result), "node_info_list": node_list,
            "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        await ctx.state.update(shot_id, video_status=ST_SUCCESS, video_url=vurl,
                               video_task_id=task_id, video_usage=extract_usage(result))
        _record_run(ctx, shot_id=shot_id, status="SUCCESS", task_id=task_id,
                    duration_s=h3.get("duration"), usage=extract_usage(result),
                    wall_elapsed_s=round(time.time() - _t_video, 2),
                    out_bytes=final_mp4.stat().st_size if final_mp4.is_file() else None,
                    artifact=str(final_mp4), output_url=vurl)
        ctx.log(f"[{shot_id}] ⑤ H3 视频完成 -> {final_mp4.name}")
        return ST_SUCCESS

    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - 单分镜级容错：标记 FAILED，不阻塞其他 shot
        err = truncate(e, C.ERROR_TEXT_LIMIT)
        cur = ctx.state.get(shot_id)
        patch: Dict[str, Any] = {"error": err}
        if cur.get("image_status") == ST_RUNNING:
            patch["image_status"] = ST_FAILED
        elif cur.get("crop_status") == ST_RUNNING:
            patch["crop_status"] = ST_FAILED
        elif cur.get("video_status") == ST_RUNNING:
            patch["video_status"] = ST_FAILED
        else:
            patch["image_status"] = ST_FAILED
        await ctx.state.update(shot_id, **patch)
        # ★ 失败也要进台账 —— 否则「成本优化」只能看到成功的一半，
        #   也看不出失败是白花钱（billed）还是没花钱（未提交/被拒）。
        _record_run(
            ctx, shot_id=shot_id, status="FAILED",
            stage="h3-video" if patch.get("video_status") == ST_FAILED else "image",
            task_id=(ctx.state.get(shot_id) or {}).get("video_task_id"),
            duration_s=((h3_prompts.get(shot_id) or {}) if isinstance(
                h3_prompts.get(shot_id), dict) else {}).get("duration"),
            usage=(ctx.state.get(shot_id) or {}).get("video_usage"),
            wall_elapsed_s=(round(time.time() - _t_video, 2) if _t_video else None),
            error=err,
        )
        await ctx.logger.log(f"[{shot_id}] ✗ FAILED: {err}")
        return ST_FAILED


def _cropped_intact(state: Dict[str, Any], crop: Dict[str, int]) -> bool:
    """裁剪状态为 SUCCESS，还要确认文件真的都在（防止手工删文件后误跳过）。"""
    files = state.get("cropped") or []
    expect = int(crop["rows"]) * int(crop["cols"])
    if len(files) != expect:
        return False
    return all(Path(f).is_file() for f in files)
