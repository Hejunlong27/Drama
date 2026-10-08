#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""rh_doctor.py —— RunningHub 双 Key 体检与能力路由（默认零网络、零计费）

为什么需要它
------------
RunningHub 的 API Key 分三类，且**不同 API 类型接受的 Key 类型不同**。
官方《开始》文档原文（https://www.runninghub.cn/runninghub-api-doc-cn/doc-8287334）：

    API 类型        | 可使用的 API Key
    ----------------|-----------------------------------------------
    模型 API        | 企业级-共享
    LLM API         | 企业级-共享
    AI 应用 API     | 消费级-会员、企业级-共享、企业级-独占
    工作流 API      | 消费级-会员、企业级-共享、企业级-独占

    「注意：模型 API 和 LLM API 仅支持企业级-共享 API Key 调用。」

推论（重要，直接影响设计）：
  * 「工作流 / AI 应用」用消费级 Key 就行，**不该白耗企业级的按量余额**；
  * 「闭源模型 / LLM」**只能用企业级-共享 Key**，消费级 Key 一定失败；
  * 企业级-共享在能力上是消费级的**超集**（四类都能调），
    所以留两把 Key 的理由是**计费路由**（会员权益 vs 按量钱包），不是能力，而是省钱。

本脚本做两件事，且**只做只读调用，绝不提交任何任务**：
  1) 打码显示每把 Key 的类型与余额（/uc/openapi/accountStatus，官方 released 接口）；
  2) 按官方能力矩阵做**提交前预检**：给定「想调什么 API + 用哪把 Key」，直接判 OK / 阻断。

安全约定：
  * 默认**不联网**，只打印「将要调用什么」，等你确认；
  * 加 --live 才真发请求，且只发下面这两个**不产生任务、不计费**的只读接口；
  * Key 一律打码，永不落日志、永不回显全文。

用法：
  python rh_doctor.py                      # 只打印计划（零网络）
  python rh_doctor.py --live               # 真体检（只读，不计费）
  python rh_doctor.py --check model        # 只做预检：模型 API 该用哪把 Key
  python rh_doctor.py --check workflow --key consumer

退出码：0 通过 / 1 预检阻断 / 2 环境或网络错误
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.getenv("RUNNINGHUB_BASE_URL", "https://www.runninghub.cn")
TIMEOUT = 30.0

# --- 官方能力矩阵（单一真源；改这里就等于改路由规则）---------------------
# tier 取值：consumer=消费级-会员 / ent_shared=企业级-共享 / ent_exclusive=企业级-独占
CAPABILITY: dict[str, set[str]] = {
    "workflow":  {"consumer", "ent_shared", "ent_exclusive"},
    "ai-app":    {"consumer", "ent_shared", "ent_exclusive"},
    "model":     {"ent_shared"},
    "llm":       {"ent_shared"},
}

API_LABEL = {
    "workflow": "工作流 API",
    "ai-app":   "AI 应用 API",
    "model":    "模型 API（闭源模型）",
    "llm":      "LLM API",
}

TIER_LABEL = {
    "consumer":      "消费级-会员",
    "ent_shared":    "企业级-共享",
    "ent_exclusive": "企业级-独占",
}

# 环境变量名 -> 我们内部的 tier 假设（拿不准的由 apiType 实测结果修正）
KEY_ENV = {
    "consumer":   "RUNNINGHUB_API_KEY",
    "ent_shared": "RUNNINGHUB_ENT_API_KEY",
}

# apiType 实测值 -> tier。官方文档只给了 NORMAL 这一例，其余靠 --live 实测补。
# 认不出来的值一律如实打印原文，不猜。
APITYPE_TO_TIER = {
    "NORMAL": "consumer",
    # 2026-09-18 实测补：企业级-共享 Key 的 apiType 实测值是 SHARED
    # （该 Key 随后成功调用了模型 API，反证 tier 判定正确）
    "SHARED": "ent_shared",
}


def mask(key: str) -> str:
    k = (key or "").strip()
    if not k:
        return "(未设置)"
    if len(k) <= 10:
        return k[:2] + "****"
    return f"{k[:6]}****{k[-4:]}  (len={len(k)})"


def post(path: str, body: dict, key: str) -> tuple[int, dict | None, str]:
    """极简 stdlib POST。返回 (http_status, json_or_None, raw_text)。"""
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Host": BASE_URL.split("//")[-1].split("/")[0],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw), raw
            except json.JSONDecodeError:
                return resp.status, None, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw), raw
        except json.JSONDecodeError:
            return e.code, None, raw
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, None, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# 能力路由预检 —— 本脚本的核心：把官方表格变成代码，提交前就拦住
# --------------------------------------------------------------------------
def preflight(api_type: str, tier: str) -> tuple[bool, str]:
    allowed = CAPABILITY.get(api_type)
    if allowed is None:
        return False, f"未知的 API 类型 '{api_type}'（可选：{'/'.join(CAPABILITY)}）"
    if tier in allowed:
        return True, f"{API_LABEL[api_type]} 可以走「{TIER_LABEL[tier]}」Key。"
    ok_tiers = "、".join(TIER_LABEL[t] for t in ("consumer", "ent_shared", "ent_exclusive")
                        if t in allowed)
    return False, (f"阻断：{API_LABEL[api_type]} **不接受**「{TIER_LABEL[tier]}」Key。"
                   f"只能用：{ok_tiers}。")


def load_keys(key_file: Path | None) -> dict[str, str]:
    """环境变量优先；可选从 json 文件补（该文件不应提交）。"""
    keys = {tier: os.getenv(env, "").strip() for tier, env in KEY_ENV.items()}
    if key_file and key_file.is_file():
        try:
            data = json.loads(key_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[WARN] 读不了 {key_file}: {e}")
            data = {}
        for tier in ("consumer", "ent_shared"):
            # 允许写 "consumer" / "RUNNINGHUB_API_KEY" 两种键名
            v = data.get(tier) or data.get(KEY_ENV[tier]) or ""
            if v and not keys[tier]:
                keys[tier] = str(v).strip()
    return keys


def print_matrix() -> None:
    print("官方能力矩阵（来源：RunningHub《开始》文档）")
    print("-" * 74)
    print(f"{'API 类型':<22}{'消费级-会员':>12}{'企业级-共享':>14}{'企业级-独占':>14}")
    print("-" * 74)
    for api in ("workflow", "ai-app", "model", "llm"):
        row = [("是" if t in CAPABILITY[api] else "否") for t in
               ("consumer", "ent_shared", "ent_exclusive")]
        print(f"{API_LABEL[api]:<22}{row[0]:>12}{row[1]:>14}{row[2]:>14}")
    print("-" * 74)
    print("⇒ 工作流 / AI 应用：消费级即可，别白耗企业级按量余额")
    print("⇒ 闭源模型 / LLM：只有企业级-共享可用，消费级一定失败")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="RunningHub 双 Key 体检（默认零网络）")
    ap.add_argument("--live", action="store_true", help="真发只读请求；不给则只打印计划")
    ap.add_argument("--key-file", default=None, help="可选 json，含 consumer / ent_shared 两把 Key")
    ap.add_argument("--check", choices=tuple(CAPABILITY), default=None,
                    help="只做提交前预检，不联网")
    ap.add_argument("--key", choices=("consumer", "ent_shared", "ent_exclusive"), default=None,
                    help="配合 --check 指定 Key 类型")
    args = ap.parse_args()

    print("=" * 74)
    print("RunningHub Key 体检 / 能力路由")
    print("=" * 74)

    # ---- 纯离线预检模式 -------------------------------------------------
    if args.check:
        print_matrix()
        if not args.key:
            print(f"你想调「{API_LABEL[args.check]}」，可选 Key 类型：")
            for t in ("consumer", "ent_shared", "ent_exclusive"):
                ok, why = preflight(args.check, t)
                print(f"  [{'OK  ' if ok else 'BLOCK'}] {TIER_LABEL[t]:<12} {why}")
            return 0
        ok, why = preflight(args.check, args.key)
        print(f"[{'OK' if ok else 'BLOCK'}] {why}")
        return 0 if ok else 1

    key_file = Path(args.key_file).resolve() if args.key_file else None
    keys = load_keys(key_file)
    print_matrix()

    print("Key 来源")
    print("-" * 74)
    for tier in ("consumer", "ent_shared"):
        env = KEY_ENV[tier]
        src = f"env {env}" if os.getenv(env) else ("key-file" if keys[tier] else "未提供")
        print(f"  {TIER_LABEL[tier]:<12} {mask(keys[tier]):<34} [{src}]")
    print()

    # ---- 零网络：只说明将调用什么 ---------------------------------------
    if not args.live:
        print("计划模式（未联网）—— 加 --live 才真发请求")
        print("-" * 74)
        print("将要调用的接口（均为只读，不提交任务、不计费）：")
        print(f"  POST {BASE_URL}/uc/openapi/accountStatus")
        print("       → 返回 apiType(Key 类型) / remainCoins(RH币) / remainMoney(钱包) / currency")
        print("  每把已提供的 Key 各调一次。")
        print()
        print("不会调用：/task/openapi/create、/task/openapi/ai-app/run、任何 /openapi/v2/{模型} 任务接口")
        print("⇒ 本次体检无论成败，都不会产生费用。")
        return 0

    # ---- 联网体检 -------------------------------------------------------
    print("实时体检（只读）")
    print("-" * 74)
    problems = 0
    observed: dict[str, str] = {}

    for tier in ("consumer", "ent_shared"):
        key = keys.get(tier) or ""
        assume = TIER_LABEL[tier]
        if not key:
            print(f"{assume:<12} 跳过（未提供）")
            continue

        status, payload, raw = post("/uc/openapi/accountStatus", {"apikey": key}, key)
        if status == 0:
            print(f"{assume:<12} [FAIL] 连不上：{raw}")
            problems += 1
            continue
        if payload is None:
            print(f"{assume:<12} [FAIL] HTTP {status} 非 JSON：{raw[:160]}")
            problems += 1
            continue

        code = payload.get("code")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if code not in (0, "0", None):
            print(f"{assume:<12} [FAIL] code={code} msg={payload.get('msg')!r}"
                  f"  原始：{json.dumps(payload, ensure_ascii=False)[:200]}")
            problems += 1
            continue

        api_type = str(data.get("apiType", "") or "")
        actual = APITYPE_TO_TIER.get(api_type.upper(), "")
        actual_label = TIER_LABEL.get(actual, f"未知({api_type})")
        observed[tier] = actual

        print(f"{assume:<12} [OK] apiType={api_type or '(空)'} → 实测类型 {actual_label}"
              f" | RH币={data.get('remainCoins')} 钱包={data.get('remainMoney')}"
              f" {data.get('currency') or ''} | 在跑任务={data.get('currentTaskCounts')}")
        if actual and actual != tier:
            print(f"{'':<12} [WARN] 与预期不符：你把「{TIER_LABEL[actual]}」Key 放在了 "
                  f"{KEY_ENV[tier]} 里，建议换到 {KEY_ENV.get(actual, '?')}")
        elif not actual:
            print(f"{'':<12} [WARN] apiType='{api_type}' 不在已知映射里，"
                  f"请把原文反馈给我，我好补进 APITYPE_TO_TIER（不猜）")

    print()
    print("结论（按官方能力矩阵）")
    print("-" * 74)
    have = {t: bool(keys.get(t)) for t in ("consumer", "ent_shared")}
    for api in ("workflow", "ai-app", "model", "llm"):
        usable = [TIER_LABEL[t] for t in ("consumer", "ent_shared") if have[t] and t in CAPABILITY[api]]
        if usable:
            print(f"  {API_LABEL[api]:<22} 可用（{'/'.join(usable)}）")
        else:
            need = "、".join(TIER_LABEL[t] for t in ("consumer", "ent_shared", "ent_exclusive")
                             if t in CAPABILITY[api])
            print(f"  {API_LABEL[api]:<22} [阻断] 缺可用 Key，需要：{need}")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
