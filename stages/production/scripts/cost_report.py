#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cost_report.py —— 完整生成成本报表（本地 Agent token + RunningHub 平台费）

为什么要它
----------
「一支 AI 短剧的真实成本」由两半构成，过去只看得到一半：

  ① 云端平台费 —— RunningHub 的 RH币消耗（台账：docs/runninghub-runs.json）
  ② 本地 Agent —— 跑这套流水线时 LLM 烧掉的 token（台账：DSH 的 cost-meter）

本脚本把两边合成一张表。

数据来源（都是本机真实账本，不是估算）
--------------------------------------
* `$DSH_HOME/storages/cost-meter/ledger.json`
    DSH 自带成本计量插件的账本。结构：
      days['YYYY-MM-DD'] = {input, output, cacheRead, cacheWrite, reasoning,
                            calls, cost, apiCost, byProviderModel, sessions[]}
    已校准：单位是**每 1M tokens**，公式
      cost = (input×cacheMiss + cacheRead×cacheHit + output×output) / 1e6
    在 2026-09-06/07/08/09/12/14 六个历史日期上与账本记录**逐位吻合**。
* `<项目>/docs/runninghub-runs.json` —— RunningHub 实际消耗台账

两种口径（务必分清）
--------------------
* **实际计费(actual/apiCost)**：账本里真实扣掉的。当前 agent 走 Command Code GOAT
  订阅，所以 = 0（包月，边际成本为零）。
* **名义计费(notional)**：同样的 token 若按官方 API 挂牌价付，会是多少。
  这才是「这套 agent 客观上值多少钱」的度量，也是换模型/换供应商时的比较基准。

用法：
  python cost_report.py                    # 本会话 + 本项目全部会话
  python cost_report.py --project-only     # 只看本项目（默认就是）
  python cost_report.py --json             # 机器可读
  python cost_report.py --rh-rate 0.01     # 给 RH币→CNY 折算率（未知则不折算）
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections import Counter, defaultdict

# ★ 2026-09-19 用户修正：1 美元 = 2746 RH币（1 RH币 ≈ $0.0003642）。
#   有了它，才能把「RH币池」（视频/AI应用/人物图）与「美元钱包」（闭源模型 API，即场景图）并成一个数字。
#   USD→CNY 需两步：先 ×RH_PER_USD 折成 RH币，再 ×rh_rate（CNY per RH）折成人民币。
RH_PER_USD = 2746.0

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _ws import WS  # noqa: E402  ★ 工作区解析（env > 位置推断 > 报错），见 scripts/_ws.py
DSH_HOME = pathlib.Path(os.environ.get('DSH_HOME') or (pathlib.Path.home() / '.dsh'))
LEDGER = DSH_HOME / 'storages' / 'cost-meter' / 'ledger.json'
RUNS = WS / 'docs' / 'runninghub-runs.json'
AUTO_RUNS_DIR = WS / 'video-pipeline' / 'ledger' / 'runs'


def load_auto_ledger() -> list[dict]:
    """读引擎**自动**写的逐任务台账 <工作区>/ledger/runs/<batch>__<shot>.json。

    与 docs/runninghub-runs.json（手工历史台账）互补：
    自动台账记录引擎跑的每一次任务（含失败与重试），手工台账留着早期人工登记的记录。
    """
    if not AUTO_RUNS_DIR.is_dir():
        return []
    out = []
    for p in sorted(AUTO_RUNS_DIR.glob('*.json')):
        try:
            out.append(json.loads(p.read_text(encoding='utf-8')))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _avg(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 4) if vals else None

MODEL_TOKEN_KEYS = ('input', 'output', 'cacheRead', 'cacheWrite', 'reasoning', 'calls')


def project_session_ids() -> set[str]:
    """本项目对应的会话 id 集合：从 DSH_SESSION_JSONL 反推项目会话目录。"""
    ids: set[str] = set()
    jsonl = os.environ.get('DSH_SESSION_JSONL', '')
    if jsonl:
        try:
            proj_dir = pathlib.Path(jsonl).resolve().parent.parent
            if proj_dir.is_dir():
                ids |= {p.name for p in proj_dir.iterdir() if p.is_dir()}
        except OSError:
            pass
    if not ids:
        sid = os.environ.get('DSH_SESSION_ID', '')
        if sid:
            ids.add(sid)
    return ids


def price_for(model_key: str, prices: dict) -> tuple[dict, str]:
    """把 'commandcode:deepseek/deepseek-v4.1-flash' 匹配到价格表条目。"""
    models = prices.get('models') or {}
    name = model_key.split(':', 1)[-1]                 # 去掉 provider 前缀
    candidates = [name, name.split('/')[-1], name.replace('/', '-')]
    for c in candidates:
        if c in models:
            return models[c], c
    # 退化匹配：去掉版本小号再试（v4.1-flash -> v4-flash）
    base = name.split('/')[-1]
    for c in candidates:
        for mk in models:
            if mk.replace('.', '') == c.replace('.', ''):
                return models[mk], mk
    for mk in models:
        if mk.split('-')[0] in base:
            return models[mk], mk + '(近似)'
    return prices.get('default') or {}, 'default(兜底)'


def notional_cny(agg: dict, price: dict) -> float:
    """按每 1M tokens 挂牌价折算。"""
    return (agg.get('input', 0) * price.get('cacheMiss', 0)
            + agg.get('cacheRead', 0) * price.get('cacheHit', 0)
            + agg.get('output', 0) * price.get('output', 0)) / 1_000_000.0


def _harden_stdio() -> None:
    """★ 中文 Windows 控制台是 GBK(cp936)，本报表正文含 '¥'(U+00A5) 等 GBK 装不下的字符，
    直接 print 会抛 UnicodeEncodeError（2026-09-19 实测：本脚本一直是这个毛病，
    在 GBK 控制台里跑到「实际计费」那一行就崩，等于成本报表根本用不了）。

    与 scripts/drama.py 的 _harden_stdio() 同一套做法：按 isatty() 分流
    （控制台用控制台代码页、管道/重定向用 UTF-8），并把 errors 放宽成 replace 兜底。
    """
    try:
        enc = 'utf-8'
        if sys.stdout.isatty() and os.name == 'nt':
            import ctypes
            cp = ctypes.windll.kernel32.GetConsoleOutputCP()
            if cp:
                enc = 'cp%d' % cp
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding=enc, errors='replace')   # type: ignore[union-attr]
            except Exception:                                    # noqa: BLE001
                pass
    except Exception:                                            # noqa: BLE001
        pass


def main() -> int:
    _harden_stdio()
    ap = argparse.ArgumentParser(description='完整生成成本报表')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    ap.add_argument('--rh-rate', type=float, default=None,
                    help='RH币→人民币折算率（1 RH币 = ? CNY）。不给则不折算。')
    ap.add_argument('--all-days', action='store_true', help='含本项目各日明细')
    args = ap.parse_args()

    if not LEDGER.is_file():
        print(f'[FAIL] 找不到 DSH 成本账本：{LEDGER}')
        return 1
    led = json.loads(LEDGER.read_text(encoding='utf-8'))
    prices = (led.get('config') or {}).get('prices') or {}
    cur_sym = (led.get('config') or {}).get('symbol', '¥')
    days = led.get('days') or {}
    ids = project_session_ids()
    cur_sid = os.environ.get('DSH_SESSION_ID', '')

    # ---------- 本地 Agent token ----------
    per_model: dict[str, dict] = defaultdict(lambda: {k: 0 for k in MODEL_TOKEN_KEYS})
    per_day: dict[str, dict] = defaultdict(lambda: {k: 0 for k in MODEL_TOKEN_KEYS})
    actual_total = 0.0
    current_session = {k: 0 for k in MODEL_TOKEN_KEYS}
    current_by_model: dict[str, dict] = defaultdict(lambda: {k: 0 for k in MODEL_TOKEN_KEYS})
    current_actual = 0.0
    missing_days = 0

    for d in sorted(days):
        for s in (days[d].get('sessions') or []):
            sid = str(s.get('id') or '')
            if ids and sid not in ids:
                continue
            for pm, u in (s.get('byProviderModel') or {}).items():
                for k in MODEL_TOKEN_KEYS:
                    per_model[pm][k] += u.get(k) or 0
                    per_day[d][k] += u.get(k) or 0
            actual_total += (s.get('apiCost') or 0)
            per_day[d].setdefault('apiCost', 0)
            per_day[d]['apiCost'] += (s.get('apiCost') or 0)
            if sid == cur_sid:
                for k in MODEL_TOKEN_KEYS:
                    current_session[k] += (s.get(k) or 0)
                for pm, u in (s.get('byProviderModel') or {}).items():
                    for k in MODEL_TOKEN_KEYS:
                        current_by_model[pm][k] += u.get(k) or 0
                current_actual += (s.get('apiCost') or 0)

    grand = {k: sum(m.get(k, 0) for m in per_model.values()) for k in MODEL_TOKEN_KEYS}
    notional_by_model = {}
    notional_total = 0.0
    for pm, agg in per_model.items():
        pr, matched = price_for(pm, prices)
        val = notional_cny(agg, pr)
        notional_by_model[pm] = {'agg': agg, 'price': pr, 'matched': matched, 'cny': val}
        notional_total += val

    # ---------- RunningHub ----------
    rh = json.loads(RUNS.read_text(encoding='utf-8')) if RUNS.is_file() else {}
    rh_runs = rh.get('runs') or []
    rh_billed = [r for r in rh_runs if r.get('consume_coins') and r['consume_coins'] != '0']
    rh_coins = sum(int(r.get('consume_coins') or 0) for r in rh_runs)
    rh_money = sum(float(r.get('consume_money') or 0) for r in rh_runs)
    rh_rate = args.rh_rate
    if rh_rate is None:
        rh_rate = (rh.get('rh_rate') or {}).get('cny_per_rh_coin')
    rh_cny = (rh_coins * rh_rate) if rh_rate else None

    if args.json:
        print(json.dumps({
            'agent': {'sessions': len(ids), 'totals': grand,
                      'actual_cny': round(actual_total, 4),
                      'notional_cny': round(notional_total, 4),
                      'by_model': {k: {'tokens': v['agg'], 'matched_price': v['matched'],
                                       'notional_cny': round(v['cny'], 4)}
                                   for k, v in notional_by_model.items()}},
            'runninghub': {'runs': len(rh_runs), 'billed_runs': len(rh_billed),
                           'rh_coins': rh_coins, 'consume_money_usd': rh_money,
                           'cny': round(rh_cny, 4) if rh_cny is not None else None,
                           'rh_rate': rh_rate},
            'total': {'cny': round((rh_cny or 0) + notional_total, 4) if rh_cny is not None else None},
        }, ensure_ascii=False, indent=2))
        return 0

    # ---------- 打印 ----------
    W = 78
    print('=' * W)
    print('完整生成成本报表 —— 《七大姑催婚》AI 短剧流水线')
    print('=' * W)
    print('项目会话数 %d | 账本覆盖日期 %s' % (len(ids), ', '.join(sorted(days)) or '(空)'))

    print()
    print('【一】本地 Agent token 用量（来源：DSH cost-meter 账本）')
    print('-' * W)
    print('  %-46s %10s %10s' % ('模型', '调用', 'output'))
    for pm, v in sorted(notional_by_model.items(), key=lambda x: -x[1]['agg']['input']):
        a = v['agg']
        print('  %-46s %10d %10d' % (pm[:46], a['calls'], a['output']))
    print()
    print('  %-26s %14s' % ('input（未命中缓存）', f"{grand['input']:,}"))
    print('  %-26s %14s' % ('cacheRead（命中缓存）', f"{grand['cacheRead']:,}"))
    print('  %-26s %14s' % ('output', f"{grand['output']:,}"))
    print('  %-26s %14s' % ('reasoning', f"{grand['reasoning']:,}"))
    print('  %-26s %14s' % ('调用次数', f"{grand['calls']:,}"))
    total_tok = grand['input'] + grand['cacheRead'] + grand['output']
    print('  %-26s %14s' % ('合计（in+cache+out）', f'{total_tok:,}'))

    print()
    print('  计价口径（务必分清）：')
    print('    ● 实际计费  %s%.4f   ← 账本 apiCost 合计' % (cur_sym, actual_total))
    print('        当前 agent 走 Command Code GOAT 订阅（包月），边际成本为零。')
    print('    ● 名义计费  %s%.4f   ← 同样 token 按官方 API 挂牌价折算' % (cur_sym, notional_total))
    for pm, v in notional_by_model.items():
        print('        %-44s %s%.4f  [价表:%s]' % (pm[:44], cur_sym, v['cny'], v['matched']))

    if current_session['calls']:
        cur_notional = sum(notional_cny(agg, price_for(pm, prices)[0])
                           for pm, agg in current_by_model.items())
        print()
        print('  ⤷ 本次会话（%s…）单独口径：' % cur_sid[:20])
        print('      input=%s  cacheRead=%s  output=%s  calls=%s' % (
            f"{current_session['input']:,}", f"{current_session['cacheRead']:,}",
            f"{current_session['output']:,}", f"{current_session['calls']:,}"))
        print('      实际计费 %s%.4f | 名义计费 %s%.4f'
              % (cur_sym, current_actual, cur_sym, cur_notional))

    print()
    print('【二】RunningHub 任务台账 · 自动记录（来源：<工作区>/ledger/runs/）')
    print('-' * W)
    auto = load_auto_ledger()
    auto_rh = 0.0
    auto_wallet_usd = 0.0
    if not auto:
        print('  （空）引擎还没跑过真实任务。首次跑完会自动落盘，无需手工登记。')
    else:
        rows = []
        for rec in auto:
            for a in (rec.get('attempts') or []):
                rows.append((rec, a))
        done = [(r, a) for r, a in rows
                if a.get('status') in ('SUCCESS', 'FAILED', 'SKIPPED', 'CANCELLED')]
        ok = [(r, a) for r, a in done if a.get('status') == 'SUCCESS']
        bad = [(r, a) for r, a in done if a.get('status') == 'FAILED']

        print('  %-16s %-8s %-8s %7s %7s %9s %9s' % (
            '分镜', '状态', '时长', '帧数', 'RH币', '墙钟s', '帧/分钟'))
        for r, a in rows:
            n = a.get('normalized') or {}
            print('  %-16s %-8s %-8s %7s %7s %9s %9s' % (
                str(r.get('shot_id'))[:16], a.get('status'),
                a.get('duration_s') if a.get('duration_s') is not None else '-',
                n.get('frames') if n.get('frames') is not None else '-',
                n.get('rh_coins') if n.get('rh_coins') is not None else '-',
                n.get('wall_elapsed_s') if n.get('wall_elapsed_s') is not None else '-',
                round(n['frames_per_min'], 1) if n.get('frames_per_min') else '-'))

        total_rh = sum((a.get('normalized') or {}).get('rh_coins') or 0 for _r, a in rows)
        wasted = sum((a.get('normalized') or {}).get('rh_coins') or 0 for _r, a in bad)
        # ★ 美元钱包口径：模型 API（场景图）的成功记录里带 wallet_usd，必须单独累加 ——
        #   它既不在 consumeCoins 里，也不在 rh_coins 里，以前整份报表都看不见这笔钱。
        #   同日同一条记录再算一遍 wallet_usd（normalized 里没有这个字段，只认 attempt 顶层）。
        auto_wallet_usd = sum(float(a.get('wallet_usd') or 0) for _r, a in rows)
        auto_rh = total_rh
        print('  ' + '-' * (W - 4))
        print('  尝试 %d 次 | 成功 %d | 失败 %d | 成功率 %s' % (
            len(done), len(ok), len(bad),
            ('%.0f%%' % (100 * len(ok) / len(done))) if done else '-'))
        print('  合计消耗 %.0f RH币 ≈ %s%.4f' % (total_rh, cur_sym, total_rh * (rh_rate or 0.0025)))
        if auto_wallet_usd:
            wallet_as_rh = auto_wallet_usd * RH_PER_USD
            print('  美元钱包消耗 $%.2f → 折 %.0f RH币 → 合计 %.0f RH币'
                  % (auto_wallet_usd, wallet_as_rh, total_rh + wallet_as_rh))
            print('    （按 1 USD = %.0f RH币；两个池已并成一个数字）' % RH_PER_USD)
        if bad:
            print('  ★ 失败中已计费的：%.0f RH币 ≈ %s%.4f（这才是"白花的钱"）' % (
                wasted, cur_sym, wasted * (rh_rate or 0.0025)))
            free_fail = [a for _r, a in bad if not a.get('billed')]
            if free_fail:
                print('    另有 %d 次失败**未计费**（提交前就被拒）—— 与"失败=浪费"要分开看'
                      % len(free_fail))
            cls = Counter(a.get('error_class') or '未知' for _r, a in bad)
            print('    失败归类：%s' % '  '.join('%s×%d' % (k, v) for k, v in cls.most_common()))

        # ---- ★ 换算层：单位经济指标，用于优化成本与运行结构 ----
        if ok:
            ns = [a.get('normalized') or {} for _r, a in ok]
            print()
            print('  ── 换算层（成功的任务，用于横向比较与优化） ──')
            print('     平均时长        %s 秒/段' % _avg([n.get('video_seconds') for n in ns]))
            print('     平均帧数        %s 帧/段' % _avg([n.get('frames') for n in ns]))
            print('     平均消耗        %s RH币/段  (%s%.4f/段)' % (
                _avg([n.get('rh_coins') for n in ns]), cur_sym,
                _avg([n.get('cny') for n in ns]) or 0))
            print('     单位成本        %s RH币/帧  |  %s%s 元/秒成片  |  %s%s 元/MB' % (
                _avg([n.get('rh_per_frame') for n in ns]), cur_sym,
                _avg([n.get('cny_per_video_second') for n in ns]), cur_sym,
                _avg([n.get('cny_per_mb') for n in ns])))
            print('     运行效率        %s 秒墙钟/帧  |  %s 帧/分钟  |  墙钟是视频长度的 %s 倍' % (
                _avg([n.get('wall_s_per_frame') for n in ns]),
                _avg([n.get('frames_per_min') for n in ns]),
                _avg([n.get('ratio_wall_over_video') for n in ns])))

            # 按时长分档对比 —— 这才是"优化运行结构"的依据
            buckets = defaultdict(list)
            for n in ns:
                d = n.get('video_seconds')
                if d:
                    buckets[round(float(d))].append(n)
            if len(buckets) > 1:
                print()
                print('     ── 按时长分档对比（判断哪个档位最划算） ──')
                print('     %6s %6s %10s %14s %14s %12s' % (
                    '时长', '样本', 'RH币/帧', '元/秒成片', '墙钟秒/帧', '帧/分钟'))
                for d in sorted(buckets):
                    g = buckets[d]
                    print('     %5ss %6d %10s %14s %14s %12s' % (
                        d, len(g),
                        _avg([x.get('rh_per_frame') for x in g]),
                        _avg([x.get('cny_per_video_second') for x in g]),
                        _avg([x.get('wall_s_per_frame') for x in g]),
                        _avg([x.get('frames_per_min') for x in g])))
                print('     ⇒ RH币/帧 随时长下降说明有固定开销被摊薄；')
                print('       元/秒成片 才是最终该看的单价。')

            # 与该任务并发的 Agent token 换算
            print()
            print('     ── Agent token 换算到「每段」 ──')
            tok = grand.get('input', 0) + grand.get('cacheRead', 0) + grand.get('output', 0)
            print('       本项目 token 合计 %s' % f'{tok:,}')
            print('       若按已完成 %d 段摊：%s token/段；' % (len(ok), f'{round(tok / len(ok)):,}'))
            print('       名义 %s%.4f/段（★ 注意：token 成本随交互轮次增长，'
                  % (cur_sym, notional_total / len(ok)))
            print('         不随段数线性增长，别外推到 101 段 —— 见第十九节说明）')

    print()
    print('【三】RunningHub 历史台账（来源：docs/runninghub-runs.json）')
    print('-' * W)
    print('  %-28s %8s %8s %10s %10s' % ('用途', '时长', '帧数', '耗时s', 'RH币'))
    for r in rh_runs:
        print('  %-28s %8s %8s %10s %10s' % (
            str(r.get('purpose'))[:28], r.get('duration_s') or '-',
            r.get('frames') or '-', r.get('elapsed_s') or '-',
            r.get('consume_coins') or '0'))
    print('  ' + '-' * (W - 4))
    print('  已计费任务 %d 个 | 未计费（失败）%d 个' % (len(rh_billed), len(rh_runs) - len(rh_billed)))
    print('  合计消耗：%s RH币' % f'{rh_coins:,}')
    print('  美元钱包消耗：$%.2f  ← ★本表只覆盖历史人工登记这两条；'
          '模型 API（场景图）的钱包消耗在【二】自动台账里，见上' % rh_money)
    if rh_cny is not None:
        print('  折算人民币：%s%.2f（按 1 RH币 = %s CNY）' % (cur_sym, rh_cny, rh_rate))
    else:
        print('  折算人民币：**未知** —— 缺 RH币→CNY 折算率')
        print('                （在 docs/runninghub-runs.json 的 rh_rate.cny_per_rh_coin 填上，')
        print('                  或运行时加 --rh-rate 0.01）')
        print('                ★ 不猜：没给折算率就不折算法币。')

    print()
    print('【四】合计')
    print('-' * W)
    # ★ 2026-09-19 修：以前这里只累加【三】的历史台账（200 RH币），
    #   【二】自动台账（今天已 6 千多 RH币的真实花费）和美元钱包**全都没进合计** ——
    #   "完整成本"因此严重偏低。现在三部分都进来，并用 RH_PER_USD 把钱包并成 RH币。
    auto_cny = (auto_rh * rh_rate) if rh_rate else None
    wallet_cny = (auto_wallet_usd * RH_PER_USD * rh_rate) if rh_rate else None
    parts = [('本地 Agent（名义）', notional_total, True),
             ('RunningHub 历史台账', rh_cny, False),
             ('RunningHub 自动台账（RH币池）', auto_cny, False),
             ('RunningHub 自动台账（美元钱包折算）', wallet_cny, False)]
    missing = [n for n, v, _ in parts if v is None]
    if missing:
        print('  ★ 无法合成单一数字：缺 RH币→CNY 折算率（以下各项无法折算：%s）。' % '、'.join(missing))
        print('    目前两半分别是：')
        print('      本地 Agent（名义）  %s%.4f' % (cur_sym, notional_total))
        print('      RunningHub         %s RH币（实际扣费单位）+ $%.2f 钱包'
              % (f'{rh_coins + auto_rh:,}', auto_wallet_usd))
        print('    折算率来源：docs/runninghub-runs.json 的 rh_rate.cny_per_rh_coin，'
              '或运行时 --rh-rate 0.01')
        print('                ★ 不猜：没给折算率就不折算法币。')
    else:
        total_all = sum(v for _, v, _ in parts)
        for n, v, is_notional in parts:
            if v:
                print('  %-34s %s%10.4f' % (n, cur_sym, v))
        print('  ' + '-' * (W - 4))
        print('  完整成本                           %s%10.4f' % (cur_sym, total_all))
        plat = total_all - notional_total
        if total_all > 0:
            print('  其中 RunningHub 占  %.2f%%' % (100 * plat / total_all))
            print('  其中 Agent token 占 %.2f%%（名义计费；走包月订阅，实际扣费请见【一】）'
                  % (100 * notional_total / total_all))
        print('  折算口径：1 RH币 = %s CNY；1 USD = %.0f RH币（= %s%.4f CNY）'
              % (rh_rate, RH_PER_USD, cur_sym, RH_PER_USD * rh_rate))

    if args.all_days and per_day:
        print()
        print('【附】本项目逐日 token')
        print('-' * W)
        for d in sorted(per_day):
            v = per_day[d]
            print('  %s  input=%-9s output=%-8s cacheRead=%-10s calls=%s' % (
                d, f"{v['input']:,}", f"{v['output']:,}", f"{v['cacheRead']:,}", v['calls']))

    print()
    print('=' * W)
    print('口径说明：token 数据来自 DSH 自带 cost-meter 账本（真实计量，非估算）；')
    print('          RunningHub 数据来自平台返回的 usage（实际消耗，非预估）；')
    print('          名义计费按价表折算，与实际扣费是两回事。')
    print('=' * W)
    return 0


if __name__ == '__main__':
    sys.exit(main())
