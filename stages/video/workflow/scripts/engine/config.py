# -*- coding: utf-8 -*-
"""
================================================================================
 config.py —— 唯一需要你手动修改的文件
================================================================================

【必填清单】（一共 4 处，填完即可直接运行）

  [1] API Key            —— 不用改代码：设置环境变量 RUNNINGHUB_API_KEY
                            Windows CMD:      set RUNNINGHUB_API_KEY=你的key
                            Windows PowerShell: $env:RUNNINGHUB_API_KEY="你的key"
                            也可以直接在下面 RUNNINGHUB_API_KEY 里硬编码（不推荐）

  [2] 激活的图片模型      —— 改 ACTIVE_IMAGE_PROVIDER（"banana" / "gpt_image" / "jimeng"）
                            并在 IMAGE_PROVIDERS 里填该模型的 workflow/应用 ID + 节点映射

  [3] 图片工作流节点       —— IMAGE_PROVIDERS[...]["nodes"] 里的 nodeId / fieldName
                            香蕉是「图生图」：必须填 提示词 + 角色参考图 + 场景参考图 三类节点

  [4] H3 视频工作流        —— H3["id"] 与 H3["nodes"] 里的 nodeId / fieldName

【怎么拿到真实的 nodeId / fieldName？】
  跑一次：  python main.py --probe
  它会调用 RunningHub 的节点发现接口，把每个工作流/应用的 nodeId + fieldName + 示例值
  打印出来。你把打印结果粘到下面的 nodes 里即可。
  （你也可以跑 tools/probe_nodes.py，功能相同）

【配置分层】（优先级从低到高，高者覆盖低者）
  ① 本文件                       —— 结构与默认值，Agent/脚本永不修改
  ② <工作区>/config.local.json   —— 用户私有覆盖层（Key / workflow ID / 节点映射都写这里）
  ③ 环境变量                     —— 只放机密：RUNNINGHUB_API_KEY / RUNNINGHUB_BASE_URL

【不用猜的部分 —— 已由本机验证过的代码确认】
  - 提交工作流: POST /task/openapi/create          body {apiKey, workflowId, nodeInfoList} -> data.taskId
  - 提交AI应用: POST /task/openapi/ai-app/run      body {apiKey, webappId,  nodeInfoList} -> data.taskId
  - 查询任务  : POST /openapi/v2/query             body {taskId} -> status + results[].url + usage
  - 上传文件  : POST /openapi/v2/media/upload/binary  multipart 字段名必须是 file
                返回 data.fileName / data.download_url（download_url 仅 1 天有效）
  - 状态值    : CREATE / QUEUED / RUNNING 为非终态；SUCCESS 成功；FAILED / CANCEL 失败
================================================================================
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ==============================================================================
# [1] API Key —— 优先读环境变量，读不到就用下面的兜底值
# ==============================================================================
RUNNINGHUB_API_KEY: str = os.getenv("RUNNINGHUB_API_KEY", "")  # ← 建议用环境变量，别写这里

# RunningHub 统一入口（注意是 .cn）
RH_BASE_URL: str = os.getenv("RUNNINGHUB_BASE_URL", "https://www.runninghub.cn")
# ⚠️ 站点必须与账号一致：账号在「国际站/AI 站」(.ai) 时，上传接口在 .cn 会返回
#    code=1「API Key不存在」（账号类接口两站共用所以能过，唯独上传不行 —— 极易误判成 Key 错）。
#    AI 站账号请设 RUNNINGHUB_BASE_URL=https://www.runninghub.ai
# RH币 -> 人民币 折算率。用户 2026-09-18 提供：1 人民币 ≈ 400 RH币。
# 仅用于台账把消耗换算成可比金额，不影响任何提交行为。
RH_CNY_PER_COIN: float = float(os.getenv("RUNNINGHUB_CNY_PER_COIN", "0.0025"))

# 单次 HTTP 请求超时（秒）。下载大文件会用这个值 ×3
HTTP_TIMEOUT: float = 60.0


# ==============================================================================
# [2] 图片模型注册表 —— 「选择香蕉 / GPT / 即梦」就是切换 ACTIVE_IMAGE_PROVIDER
# ==============================================================================
# nodes 里每个键的含义：
#   prompt     必填  分镜提示词（文本）
#   ratio      可选  画幅比例，如 "16:9"
#   char_ref   可选  角色参考图（图生图关键输入）
#   scene_ref  可选  场景参考图（图生图关键输入）
#   image      可选  如果你的工作流只有一个「多图输入」节点，把 char_ref/scene_ref
#                    删掉，改在这里配，代码会把角色图+场景图合并成一个数组塞进去
#
# 每个节点项支持：
#   nodeId     节点 ID（字符串，如 "64"）
#   fieldName  字段名（如 "text" / "image" / "imageUrls"）
#   multi      是否数组型输入（true 时多个值会被序列化成 JSON 数组字符串）
#
IMAGE_PROVIDERS: dict = {
    "banana": {
        "label": "香蕉（图生图）",
        "kind": "workflow",       # workflow = 自建 ComfyUI 工作流 | ai-app = RunningHub AI 应用
        "id": "",                 # TODO[3] 填香蕉的 workflowId（若 kind=ai-app 则填 webappId）
        "instance_type": "default",
        "nodes": {
            # TODO[3] 用 python main.py --probe 打印后回填下面 4 行
            "prompt":    {"nodeId": "", "fieldName": "text"},         # TODO 不确定是 text 还是 prompt
            "ratio":     {"nodeId": "", "fieldName": "aspectRatio"},  # TODO
            "char_ref":  {"nodeId": "", "fieldName": "image", "multi": True},   # TODO 图生图必填
            "scene_ref": {"nodeId": "", "fieldName": "image", "multi": True},   # TODO 图生图必填
        },
    },
    "gpt_image": {
        "label": "GPT 图像",
        "kind": "workflow",       # 若 GPT 图像是 RunningHub 的 AI 应用，改成 "ai-app"
        "id": "",                 # TODO 填 GPT 图像工作流 ID
        "instance_type": "default",
        "nodes": {
            "prompt":    {"nodeId": "", "fieldName": "text"},         # TODO
            "ratio":     {"nodeId": "", "fieldName": "aspectRatio"},  # TODO
            "char_ref":  {"nodeId": "", "fieldName": "image", "multi": True},   # TODO
            "scene_ref": {"nodeId": "", "fieldName": "image", "multi": True},   # TODO
        },
    },
    "jimeng": {
        "label": "即梦",
        "kind": "workflow",
        "id": "",                 # TODO 填即梦工作流 ID
        "instance_type": "default",
        "nodes": {
            "prompt":    {"nodeId": "", "fieldName": "text"},         # TODO
            "ratio":     {"nodeId": "", "fieldName": "aspectRatio"},  # TODO
            "char_ref":  {"nodeId": "", "fieldName": "image", "multi": True},   # TODO
            "scene_ref": {"nodeId": "", "fieldName": "image", "multi": True},   # TODO
        },
    },
}

# ← 切换图片模型只改这一行
ACTIVE_IMAGE_PROVIDER: str = "banana"


# ==============================================================================
# [4] MiniMax H3 视频工作流
# ==============================================================================
H3: dict = {
    "kind": "workflow",           # 你自己的 H3 工作流 -> workflow
    "id": "",                     # TODO[4] 填 H3 workflowId
    "instance_type": "default",

    # 首帧图取哪张： "cell_01" = 九宫格裁出的第 01 格 | "grid_raw" = 整张九宫格原图
    # 依据你的 H3 工作流实际接收什么来定。默认 cell_01（更像"首帧"）。
    "frame_source": "cell_01",

    "nodes": {
        # TODO[4] 用 python main.py --probe 打印后回填
        "prompt":      {"nodeId": "", "fieldName": ""},   # TODO 文本提示词节点
        "first_frame": {"nodeId": "", "fieldName": ""},   # TODO 首帧图节点（图生视频必需）
        "char_ref":    {"nodeId": "", "fieldName": ""},   # TODO 角色参考图节点（无则留空，自动跳过）
        "scene_ref":   {"nodeId": "", "fieldName": ""},   # TODO 场景参考图节点（无则留空，自动跳过）
        "grid_ref":    {"nodeId": "", "fieldName": ""},   # TODO 九宫格整图作为参考（无则留空）
        "duration":    {"nodeId": "", "fieldName": ""},   # TODO 时长（无则留空）
        "ratio":       {"nodeId": "", "fieldName": ""},   # TODO 画幅（无则留空）
    },
}


# ==============================================================================
# 路径配置
# ==============================================================================
DATA_DIR: str = "data"            # 输入 JSON 目录（相对「工作区」，也可写绝对路径）
OUTPUT_DIR: str = "output"        # 批次输出根目录（相对「工作区」，也可写绝对路径）


# ==============================================================================
# 并发与限流  —— ★★★ 不要调高 ★★★
# RunningHub 同一时间最多只能同时执行 5 个任务。
# 该信号量包住「提交 + 轮询直到终态 + 下载」的完整生命周期。
# ==============================================================================
CONCURRENCY_MAX: int = 5          # ★ 硬上限，全局唯一来源（并发不得高于此值）
CONCURRENCY: int = CONCURRENCY_MAX  # 默认与上限相同，调高会被收敛
BATCH_SIZE: int = 20              # 每批处理多少个 shot，批末检查熔断
POLL_INTERVAL: float = 5.0        # 轮询间隔（秒）

# 轮询通道：
#   "v2"     = 只用 POST /openapi/v2/query（本机已验证，推荐，保持默认）
#   "status" = 只用 POST /task/openapi/status（字段名未实测，留给你验证）
#   "auto"   = 优先 v2，结构无法识别时自动降级到 status
POLL_CHANNEL: str = "v2"
IMAGE_TIMEOUT_MIN: float = 15.0   # 单张分镜图任务超时（分钟）
VIDEO_TIMEOUT_MIN: float = 60.0   # 单个 H3 视频任务超时（分钟）


# ==============================================================================
# 失败重试（单任务级）
# ==============================================================================
RETRY_MAX: int = 3
RETRY_BACKOFF: tuple = (5, 15, 45)     # 秒；第 n 次重试前等待 RETRY_BACKOFF[n-1]
ERROR_TEXT_LIMIT: int = 200             # 错误信息截断长度


# ==============================================================================
# 熔断（整批级）
# ==============================================================================
FUSE_CONSECUTIVE_FAILS: int = 5     # 连续失败达到该值 -> 熔断
FUSE_FAIL_RATE: float = 0.5         # 失败率超过该值 -> 熔断
FUSE_MIN_SAMPLES: int = 4           # 至少完成这么多个才参与"失败率"判定，避免早批误熔断


# ==============================================================================
# 裁剪与其它
# ==============================================================================
DEFAULT_CROP: dict = {"rows": 3, "cols": 3}   # shots.json 未写 crop 时的默认值（九宫格）
UPLOAD_CACHE_TTL_HOURS: float = 20.0          # 上传链接 1 天有效，超过该时长则重传（留出安全余量）
KEEP_RAW_ZIP: bool = False                    # 香蕉若回传 zip 包，是否保留原包


def get_active_provider() -> dict:
    """返回当前激活的图片模型配置；顺便做基础校验。"""
    if ACTIVE_IMAGE_PROVIDER not in IMAGE_PROVIDERS:
        raise KeyError(
            f"ACTIVE_IMAGE_PROVIDER='{ACTIVE_IMAGE_PROVIDER}' 不在 IMAGE_PROVIDERS 里，"
            f"可选：{list(IMAGE_PROVIDERS.keys())}"
        )
    return IMAGE_PROVIDERS[ACTIVE_IMAGE_PROVIDER]


def _node_ready(spec: Any) -> bool:
    """节点算不算「已配置」。

    两种合法形态都认：
      - 单节点：{"nodeId": "...", "fieldName": "..."}
      - 有序多槽位：{"slots": [{"nodeId": "...", "fieldName": "..."}, ...]}
    """
    if not isinstance(spec, dict):
        return False
    if spec.get("nodeId"):
        return True
    slots = spec.get("slots")
    if isinstance(slots, list):
        return any(isinstance(s, dict) and s.get("nodeId") for s in slots)
    return False


def missing_configs(video_only: bool = False) -> list:
    """返回所有「还没填」的关键配置项，用于启动时友好提示（而不是跑到一半才炸）。

    注意：`first_frame` **不再强制** —— H3 多参模式（--video-only）走的是
    reference generation，提示词 + 有序参考图，不需要首帧图。
    真正必填的是 prompt 与（视频阶段的）参考图槽位。

    注意（2026-09-19 修）：`video_only=True` 时**不再索要 image_providers** ——
    该模式下分镜图整段被跳过（见 pipeline.py `if ctx.video_only:` 分支），
    图片 Provider 对象永远不会被构造，缺它完全不影响出片。
    此前这里无条件索要，于是出现了一个很难发现的矛盾：
      * `--dry-run` 不做这项校验 ⇒ **预演永远绿**；
      * 真实出片才校验 ⇒ **一到花钱那一步才被自己的闸门挡下**（退出码 1、零花费）。
    本项目整条线只用 `--video-only`，所以这个假绿一直是「预演通过但跑不起来」的根因。
    """
    miss = []
    if not RUNNINGHUB_API_KEY:
        miss.append("RUNNINGHUB_API_KEY（环境变量未设置，config.local.json 也没有）")
    if not video_only:
        prov = IMAGE_PROVIDERS.get(ACTIVE_IMAGE_PROVIDER) or {}
        if not prov.get("id"):
            miss.append(f"image_providers['{ACTIVE_IMAGE_PROVIDER}'].id（图片工作流 ID）")
        pnodes = prov.get("nodes") or {}
        for key in ("prompt", "char_ref", "scene_ref"):
            if not _node_ready(pnodes.get(key)):
                miss.append(f"image_providers['{ACTIVE_IMAGE_PROVIDER}'].nodes.{key}.nodeId")
    if not H3.get("id"):
        miss.append("h3.id（H3 视频工作流 ID）")
    hnodes = H3.get("nodes") or {}
    if not _node_ready(hnodes.get("prompt")):
        miss.append("h3.nodes.prompt.nodeId")
    if not _node_ready(hnodes.get("char_ref")):
        miss.append("h3.nodes.char_ref.nodeId（或 .slots[]，多参模式的参考图槽位）")
    return miss


# ##############################################################################
# 配置分层：<工作区>/config.local.json 覆盖层
# ##############################################################################
OVERLAY_FILENAME = "config.local.json"

# 允许覆盖的顶层键白名单（含别名映射）
_KEY_ALIASES = {
    "runninghub_api_key": "RUNNINGHUB_API_KEY",
    "runninghub_base_url": "RH_BASE_URL",
    "runninghub_cny_per_coin": "RH_CNY_PER_COIN",
    "http_timeout": "HTTP_TIMEOUT",
    "active_image_provider": "ACTIVE_IMAGE_PROVIDER",
    "image_providers": "IMAGE_PROVIDERS",
    "h3": "H3",
    "concurrency": "CONCURRENCY",
    "batch_size": "BATCH_SIZE",
    "poll_interval": "POLL_INTERVAL",
    "poll_channel": "POLL_CHANNEL",
    "image_timeout_min": "IMAGE_TIMEOUT_MIN",
    "video_timeout_min": "VIDEO_TIMEOUT_MIN",
    "retry_max": "RETRY_MAX",
    "retry_backoff": "RETRY_BACKOFF",
    "error_text_limit": "ERROR_TEXT_LIMIT",
    "fuse_consecutive_fails": "FUSE_CONSECUTIVE_FAILS",
    "fuse_fail_rate": "FUSE_FAIL_RATE",
    "fuse_min_samples": "FUSE_MIN_SAMPLES",
    "default_crop": "DEFAULT_CROP",
    "upload_cache_ttl_hours": "UPLOAD_CACHE_TTL_HOURS",
    "keep_raw_zip": "KEEP_RAW_ZIP",
    "data_dir": "DATA_DIR",
    "out_dir": "OUTPUT_DIR",
    "output_dir": "OUTPUT_DIR",
}

# 需要深合并的 dict 键
_DEEP_KEYS = {"IMAGE_PROVIDERS", "H3", "DEFAULT_CROP"}

OVERLAY_WARNINGS: list = []      # apply_overlay 产生的非致命警告（供 doctor / main 打印）


def mask_key(value: str) -> str:
    """API Key 打码展示：rh_****abcd。永不回显完整值。"""
    v = str(value or "")
    if not v:
        return "(空)"
    if len(v) <= 6:
        return "*" * len(v)
    return f"{v[:3]}****{v[-4:]}"


def _deep_merge(base: dict, patch: dict) -> dict:
    """递归合并 patch 到 base 的副本上，返回新 dict（不改 base）。"""
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _warn(msg: str) -> None:
    OVERLAY_WARNINGS.append(msg)


def _shorten(text: str, limit: int = 160) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _validate_nodes(where: str, nodes: Any) -> None:
    """只做结构/类型校验；nodeId / fieldName 为空视为「未配置」，由 missing_configs()
    按「当前激活 provider 的必填项」统一判定，避免可选节点刷屏警告。"""
    if nodes is None:
        return
    if not isinstance(nodes, dict):
        _warn(f"{where}.nodes 应为对象，已忽略该字段")
        return
    for name, node in nodes.items():
        if node is None:
            continue
        if not isinstance(node, dict):
            _warn(f"{where}.nodes.{name} 应为对象，已忽略")
            continue
        for f in ("nodeId", "fieldName"):
            v = node.get(f)
            if v in (None, ""):
                continue                      # 未配置，不算错
            if not isinstance(v, str):
                node[f] = str(v)              # 数字型 nodeId 宽容转字符串
        if "multi" in node and not isinstance(node["multi"], bool):
            _warn(f"{where}.nodes.{name}.multi 应为布尔值，已忽略")
            node.pop("multi", None)


def _coerce(key: str, value: Any) -> Any:
    """把 JSON 值转换成 config.py 里对应变量的 Python 类型；非法返回 _INVALID 哨兵。"""
    if key == "RETRY_BACKOFF":
        if isinstance(value, list) and all(isinstance(x, (int, float)) for x in value):
            return tuple(float(x) if isinstance(x, float) else int(x) for x in value)
        return _INVALID
    if key in ("CONCURRENCY", "BATCH_SIZE", "RETRY_MAX", "ERROR_TEXT_LIMIT",
               "FUSE_CONSECUTIVE_FAILS", "FUSE_MIN_SAMPLES"):
        if isinstance(value, bool) or not isinstance(value, int):
            return _INVALID
        return value
    if key in ("HTTP_TIMEOUT", "POLL_INTERVAL", "IMAGE_TIMEOUT_MIN", "VIDEO_TIMEOUT_MIN",
               "UPLOAD_CACHE_TTL_HOURS"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return _INVALID
        return float(value)
    if key in ("FUSE_FAIL_RATE",):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return _INVALID
        return float(value)
    if key in ("KEEP_RAW_ZIP",):
        if not isinstance(value, bool):
            return _INVALID
        return value
    if key in ("RUNNINGHUB_API_KEY", "RH_BASE_URL", "ACTIVE_IMAGE_PROVIDER",
               "POLL_CHANNEL", "DATA_DIR", "OUTPUT_DIR"):
        if not isinstance(value, str):
            return _INVALID
        return value
    return value


class _Invalid:
    pass


_INVALID = _Invalid()


def apply_overlay(path) -> list:
    """读取 <工作区>/config.local.json 并覆盖本模块的配置常量。

    - 白名单之外的顶层键：警告并忽略（不崩溃、不静默）
    - 以下划线开头的键：注释约定，直接忽略
    - concurrency 超过 CONCURRENCY_MAX：自动收敛并警告
    - runninghub_api_key：仅在环境变量未设置时生效（环境变量优先）
    - 返回警告列表（同时存入 OVERLAY_WARNINGS）
    """
    del OVERLAY_WARNINGS[:]
    p = Path(path)
    if not p.is_file():
        return OVERLAY_WARNINGS
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as e:
        _warn(f"{p.name} 无法解析，已忽略该覆盖层: {_shorten(str(e), 120)}")
        return OVERLAY_WARNINGS
    if not isinstance(raw, dict):
        _warn(f"{p.name} 顶层应为 JSON 对象，已忽略该覆盖层")
        return OVERLAY_WARNINGS

    for key, value in raw.items():
        if key.startswith("_"):
            continue
        target = _KEY_ALIASES.get(key)
        if target is None:
            _warn(f"{p.name}: 未知配置项 '{key}'，已忽略（白名单见 references/config-layers.md）")
            continue

        coerced = _coerce(target, value)
        if coerced is _INVALID:
            _warn(f"{p.name}: '{key}' 类型不符（期望 {_TYPE_HINT.get(target, '标量')}），已忽略")
            continue

        if target == "CONCURRENCY":
            if coerced > CONCURRENCY_MAX:
                _warn(f"{p.name}: concurrency={coerced} 超过上限 {CONCURRENCY_MAX}，已收敛为 {CONCURRENCY_MAX}")
                coerced = CONCURRENCY_MAX
        elif target == "POLL_CHANNEL":
            if coerced not in ("v2", "status", "auto"):
                _warn(f"{p.name}: poll_channel='{coerced}' 非法（可选 v2/status/auto），已忽略")
                continue
        elif target == "ACTIVE_IMAGE_PROVIDER":
            if coerced not in IMAGE_PROVIDERS:
                _warn(f"{p.name}: active_image_provider='{coerced}' 不在注册表里"
                      f"（可选 {list(IMAGE_PROVIDERS.keys())}），已忽略")
                continue
        elif target in _DEEP_KEYS:
            if not isinstance(coerced, dict):
                _warn(f"{p.name}: '{key}' 应为对象，已忽略")
                continue
            coerced = _deep_merge(globals()[target], coerced)
            if target == "IMAGE_PROVIDERS":
                for pname, pcfg in coerced.items():
                    if not isinstance(pcfg, dict):
                        continue
                    if pcfg.get("kind") not in (None, "workflow", "ai-app"):
                        _warn(f"image_providers.{pname}.kind='{pcfg.get('kind')}' 非法"
                              "（可选 workflow/ai-app），已忽略该字段")
                        pcfg.pop("kind", None)
                    _validate_nodes(f"image_providers.{pname}", pcfg.get("nodes"))
            elif target == "H3":
                if coerced.get("kind") not in (None, "workflow", "ai-app"):
                    _warn(f"h3.kind='{coerced.get('kind')}' 非法（可选 workflow/ai-app），已忽略该字段")
                    coerced.pop("kind", None)
                if coerced.get("frame_source") not in (None, "cell_01", "grid_raw"):
                    _warn(f"h3.frame_source='{coerced.get('frame_source')}' 非法"
                          "（可选 cell_01/grid_raw），已忽略该字段")
                    coerced.pop("frame_source", None)
                _validate_nodes("h3", coerced.get("nodes"))
        elif target == "RUNNINGHUB_API_KEY":
            if os.getenv("RUNNINGHUB_API_KEY"):
                _warn(f"{p.name}: 已设置环境变量 RUNNINGHUB_API_KEY，文件里的 key 已被忽略（环境变量优先）")
                continue

        globals()[target] = coerced
    return OVERLAY_WARNINGS


_TYPE_HINT = {
    "CONCURRENCY": "整数", "BATCH_SIZE": "整数", "RETRY_MAX": "整数",
    "ERROR_TEXT_LIMIT": "整数", "FUSE_CONSECUTIVE_FAILS": "整数",
    "FUSE_MIN_SAMPLES": "整数", "HTTP_TIMEOUT": "数字", "POLL_INTERVAL": "数字",
    "IMAGE_TIMEOUT_MIN": "数字", "VIDEO_TIMEOUT_MIN": "数字",
    "UPLOAD_CACHE_TTL_HOURS": "数字", "FUSE_FAIL_RATE": "0~1 之间的小数",
    "RETRY_BACKOFF": "数字数组，如 [5, 15, 45]", "KEEP_RAW_ZIP": "true/false",
    "RUNNINGHUB_API_KEY": "字符串", "RH_BASE_URL": "字符串",
    "RH_CNY_PER_COIN": "数字（1 RH币 = ? 人民币，默认 0.0025）",
    "ACTIVE_IMAGE_PROVIDER": "字符串", "POLL_CHANNEL": "v2 | status | auto",
    "DATA_DIR": "字符串", "OUTPUT_DIR": "字符串",
    "IMAGE_PROVIDERS": "对象", "H3": "对象", "DEFAULT_CROP": "对象 {rows, cols}",
}
