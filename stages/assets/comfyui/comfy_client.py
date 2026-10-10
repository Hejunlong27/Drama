#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""comfy_client.py —— 本地 ComfyUI 批量出图客户端（零第三方依赖，纯标准库）

收编来源：WorkBuddy Skill `comfyui-local-imagegen`（2026-10-10 拷贝固化）
收编方式：**拷贝固化**，核心逻辑零改动；仅把工作流节点 id 与工作流路径改为可配置（见下）。
产物纪律：本脚本只负责「提交工作流 → 轮询 → 取图落盘」；**不把产物写进引擎目录**，一律由 `--out` 指定（落工作区）。

与云端通道的差别
----------------
* **零成本、零账号**：只连本机 ComfyUI 的 HTTP 接口，不出网、不计费。
* **异步**：拿到 `prompt_id` 后轮询 `/history`，直到 `outputs` 非空或 `status.completed`。
* **断点续跑**：`--skip-existing` 跳过已有产物的资产（本地出图慢，续跑是刚需）。

用法:
  # 体检：确认 ComfyUI 在线（读 /system_stats，不占用 GPU 出图）
  python comfy_client.py --check

  # 单张出图
  python comfy_client.py --prompt "..." --prefix valentina --out assets/characters

  # 按清单批量出图（清单格式见 references/本地ComfyUI出图.md §五）
  python comfy_client.py --spec assets/manifest.json --kind char --out assets
  python comfy_client.py --spec assets/manifest.json --only char_01 --out assets --skip-existing

可配置项（环境变量，缺省即本机默认工作流的实测值）
--------------------------------------------------
  COMFYUI_URL           ComfyUI 地址，默认 http://127.0.0.1:8188
  COMFYUI_WORKFLOW      工作流 JSON 路径，默认本目录 workflow_qwen_image21.json
  COMFYUI_NODE_PROMPT   正向提示词节点，默认 "6"
  COMFYUI_NODE_PREFIX   保存图像节点（决定输出文件名前缀），默认 "2"
  COMFYUI_NODE_SEED     种子节点，默认 "10"
  COMFYUI_NODE_ASPECT   分辨率节点，默认 "13"
  COMFYUI_NODE_LATENT   空 Latent 节点（batch_size），默认 "7"
"""
import argparse
import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.parse

# Windows 控制台默认 GBK，强制 UTF-8 输出，避免中文资产名乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))

COMFY = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
DEFAULT_WORKFLOW = os.environ.get("COMFYUI_WORKFLOW",
                                  os.path.join(HERE, "workflow_qwen_image21.json"))

# ★ 节点映射：默认值对应本目录 workflow_qwen_image21.json（Qwen-Image 2.1）。
#   换工作流**必须**核对节点 id —— 见 references/workflow-qwen-image21.md「换工作流的适配清单」。
PROMPT_NODE = os.environ.get("COMFYUI_NODE_PROMPT", "6")    # PrimitiveStringMultiline
PREFIX_NODE = os.environ.get("COMFYUI_NODE_PREFIX", "2")    # SaveImage
SEED_NODE = os.environ.get("COMFYUI_NODE_SEED", "10")       # Seed (rgthree)
ASPECT_NODE = os.environ.get("COMFYUI_NODE_ASPECT", "13")   # ResolutionSelector
LATENT_NODE = os.environ.get("COMFYUI_NODE_LATENT", "7")    # EmptyLatentImage


# ★ 本客户端只连**本机 / 局域网的 ComfyUI**，必须**绕开系统代理**：
#   实测环境里存在 HTTP_PROXY/HTTPS_PROXY（如 http://127.0.0.1:49724），urllib 会连
#   `http://127.0.0.1:8188` 也塞进代理，返回 `HTTP Error 502: Bad Gateway`。
#   这里显式用「空代理」的 opener，与系统代理环境变量彻底解耦。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _req(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with _OPENER.open(req, timeout=timeout) as r:
        return r.read()


def check():
    """体检：读 /system_stats（不占用出图显存），返回 ComfyUI 版本 / 显卡 / 空闲显存。"""
    raw = _req(COMFY + "/system_stats", timeout=10).decode("utf-8")
    st = json.loads(raw)
    dev = st.get("devices", [{}])[0]
    return {
        "ok": True,
        "comfyui": st.get("system", {}).get("comfyui_version"),
        "gpu": dev.get("name"),
        "vram_free_gb": round(dev.get("vram_free", 0) / 1024**3, 2),
    }


def submit(workflow, client_id):
    payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode("utf-8")
    raw = _req(COMFY + "/prompt", data=payload, headers={"Content-Type": "application/json"})
    return json.loads(raw.decode("utf-8"))


def wait(pid, timeout=1200, poll=2.0):
    end = time.time() + timeout
    while time.time() < end:
        raw = _req("{}/history/{}".format(COMFY, pid), timeout=30)
        h = json.loads(raw.decode("utf-8"))
        if pid in h:
            entry = h[pid]
            status = entry.get("status", {})
            if entry.get("outputs") or status.get("completed"):
                return entry
            if status.get("status_str") == "error":
                raise RuntimeError("workflow error: " + json.dumps(status, ensure_ascii=False)[:600])
        time.sleep(poll)
    raise TimeoutError("timeout waiting for prompt " + pid)


def fetch_image(meta, out_path):
    q = urllib.parse.urlencode({
        "filename": meta.get("filename", ""),
        "subfolder": meta.get("subfolder", ""),
        "type": meta.get("type", "output"),
    })
    raw = _req("{}/view?{}".format(COMFY, q), timeout=180)
    with open(out_path, "wb") as f:
        f.write(raw)
    return len(raw)


def build(base, prompt, prefix, seed=None, aspect=None, count=1):
    """把「提示词 / 文件名前缀 / 种子 / 画幅 / 张数」写进工作流副本（不改原文件）。"""
    wf = json.loads(json.dumps(base))
    wf[PROMPT_NODE]["inputs"]["value"] = prompt
    wf[PREFIX_NODE]["inputs"]["filename_prefix"] = prefix
    if seed is not None:
        wf[SEED_NODE]["inputs"]["seed"] = int(seed)
    if aspect:
        wf[ASPECT_NODE]["inputs"]["aspect_ratio"] = aspect
    if count and int(count) > 1:
        wf[LATENT_NODE]["inputs"]["batch_size"] = int(count)
    return wf


def run_one(base, item, out_dir):
    prefix = item.get("prefix") or item.get("id")
    wf = build(base, item["prompt"], prefix, item.get("seed"), item.get("aspect"), item.get("count", 1))
    res = submit(wf, str(uuid.uuid4()))
    # node_errors 非空 = 参数错误（提示词节点 id 错 / 模型没装），直接中止不要重试
    if res.get("node_errors"):
        raise RuntimeError("node errors: " + json.dumps(res["node_errors"], ensure_ascii=False)[:800])
    pid = res["prompt_id"]
    entry = wait(pid)
    outs = []
    for _node_id, out in sorted(entry.get("outputs", {}).items()):
        for img in out.get("images", []):
            ext = os.path.splitext(img.get("filename", ""))[1] or ".png"
            safe = "{}_{:02d}{}".format(prefix, len(outs), ext)
            path = os.path.join(out_dir, safe)
            n = fetch_image(img, path)
            outs.append((path, n))
    return outs


KIND_DIR = {"char": "characters", "scene": "scenes", "prop": "props"}


def _has_output(d, prefix):
    """判断某资产是否已有产物，用于断点续跑（跳过已生成项）。"""
    if not os.path.isdir(d):
        return False
    for f in os.listdir(d):
        _stem, ext = os.path.splitext(f)
        if f.startswith(prefix + "_") and ext.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description="本地 ComfyUI 批量出图（零成本、零账号、纯标准库）")
    ap.add_argument("--workflow", default=DEFAULT_WORKFLOW, help="工作流 JSON（默认本目录 workflow_qwen_image21.json）")
    ap.add_argument("--spec", help="资产清单 JSON（格式见 references/本地ComfyUI出图.md §五）")
    ap.add_argument("--kind", help="按 kind 过滤: char/scene/prop")
    ap.add_argument("--only", help="逗号分隔的资产 id")
    ap.add_argument("--prompt")
    ap.add_argument("--prefix")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--aspect")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--skip-existing", action="store_true", help="跳过已有产物的资产（断点续跑）")
    ap.add_argument("--out", default=".", help="产物落盘目录（按 kind 自动分 characters/scenes/props 子目录）")
    ap.add_argument("--check", action="store_true", help="只体检：确认 ComfyUI 在线，然后退出")
    args = ap.parse_args()

    if args.check:
        try:
            print(json.dumps(check(), ensure_ascii=False))
        except Exception as e:                                   # noqa: BLE001
            print(json.dumps({"ok": False, "url": COMFY,
                              "error": "%s: %s" % (type(e).__name__, str(e)[:200])},
                             ensure_ascii=False))
            raise SystemExit(1)
        return

    with open(args.workflow, "r", encoding="utf-8") as f:
        base = json.load(f)

    os.makedirs(args.out, exist_ok=True)

    if args.spec:
        with open(args.spec, "r", encoding="utf-8") as f:
            spec = json.load(f)
        items = spec.get("assets", spec if isinstance(spec, list) else [])
        # ★ 清单级 style_lock：统一风格串，逐条拼到 prompt 前，保证系列一致
        style_lock = (spec.get("style_lock") or "").strip() if isinstance(spec, dict) else ""
        spec_aspect = (spec.get("aspect") or "").strip() if isinstance(spec, dict) else ""
        only = set(x.strip() for x in args.only.split(",")) if args.only else None
        done = skipped = failed = 0
        for it in items:
            if only and it.get("id") not in only:
                continue
            if args.kind and it.get("kind") != args.kind:
                continue
            prefix = it.get("prefix") or it.get("id")
            sub = KIND_DIR.get(it.get("kind", ""), "")
            out_dir = os.path.join(args.out, sub) if sub else args.out
            os.makedirs(out_dir, exist_ok=True)
            if args.skip_existing and _has_output(out_dir, prefix):
                print("[skip] {}".format(it.get("id")), flush=True)
                skipped += 1
                continue
            t0 = time.time()
            print("[run] {} ({})".format(it.get("id"), it.get("name", "")), flush=True)
            try:
                prompt = it["prompt"]
                if style_lock and style_lock not in prompt:
                    prompt = style_lock + "。" + prompt
                outs = run_one(base, {**it, "prompt": prompt,
                                      "aspect": it.get("aspect") or spec_aspect}, out_dir)
                for p, n in outs:
                    print("   -> {} ({:.0f} KB, {:.0f}s)".format(p, n / 1024.0, time.time() - t0), flush=True)
                done += 1
            except Exception as e:
                print("   !! FAILED: {}".format(e), flush=True)
                failed += 1
        print("[done] generated={} skipped={} failed={}".format(done, skipped, failed), flush=True)
    else:
        it = {"id": args.prefix or "out", "prefix": args.prefix or "out", "prompt": args.prompt,
              "seed": args.seed, "aspect": args.aspect, "count": args.count}
        outs = run_one(base, it, args.out)
        for p, n in outs:
            print("-> {} ({:.0f} KB)".format(p, n / 1024.0), flush=True)


if __name__ == "__main__":
    main()
