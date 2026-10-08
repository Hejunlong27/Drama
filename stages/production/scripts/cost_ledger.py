# -*- coding: utf-8 -*-
"""cost_ledger.py —— 短剧成本台账（单次任务 + 全剧累计，一次跑完出表格）

用户需求（2026-09-19）：
    「每次工作完成之后让这个脚本计算单次任务的消耗，以及这一部短剧的累计消耗，
      包括 rh币、美元钱包余额分别花了多少、token 花了多少。最后给我看的可以是一份表格台账。」

★ 成本口径（用户 2026-09-19 明确的铁律，本脚本严格遵守）：
    1. 消耗**只取平台按任务返回的 usage**（consumeCoins / consumeMoney / thirdPartyConsumeMoney），
       逐条累加。**绝不用余额差值倒推消耗** —— 余额变动混入充值、订阅续费、其他产品与其他使用者。
    2. 余额只用于判断「够不够跑」，且**不做归因**（要看得显式加 --balance，且是只读接口）。
    3. token 名义计费 ≠ 实际扣费：走包月订阅时 apiCost 为 0，两个数都报，不混为一谈。

数据源
    * RH币 + 美元钱包：<工作区>/video-pipeline/ledger/runs/<batch>__<shot>.json（引擎自动落盘，逐 attempt）
    * Agent token    ：$DSH_HOME/storages/cost-meter/ledger.json（DSH 自带，本项目会话）
    * 结账基线       ：<工作区>/docs/cost-checkpoint.json（本脚本维护，用于算「自上次结账以来」的增量）

用法
    python scripts\\cost_ledger.py                 # 表格台账（打印 + 写 docs\\成本台账.md）
    python scripts\\cost_ledger.py --checkpoint    # ★ 任务做完后打一次基线，下次就能算单次增量
    python scripts\\cost_ledger.py --json          # 机器可读
    python scripts\\cost_ledger.py --balance       # 附带只读余额（仅判断够不够跑；不参与消耗统计）
    python scripts\\cost_ledger.py --md 路径       # 指定表格输出文件（默认 docs\\成本台账.md）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from collections import defaultdict
from datetime import datetime

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # noqa: E402
from _ws import WS  # noqa: E402  ★ 工作区解析（env DRAMA_WORKSPACE > 位置推断 > 报错），见 scripts/_ws.py
VP = WS / 'video-pipeline'
DATA = VP / 'data'
AUTO_RUNS = VP / 'ledger' / 'runs'
DSH_HOME = pathlib.Path(os.environ.get('DSH_HOME') or (pathlib.Path.home() / '.dsh'))
TOKEN_LEDGER = DSH_HOME / 'storages' / 'cost-meter' / 'ledger.json'
CHECKPOINT = WS / 'docs' / 'cost-checkpoint.json'
RH_RATE_JSON = WS / 'docs' / 'runninghub-runs.json'

# 1 美元 = 2746 RH币（用户 2026-09-19 确认）。用于把美元钱包并回 RH币口径。
RH_PER_USD_DEFAULT = 2746.0
# 1 RH币 ≈ ¥0.0025（用户 2026-09-18）。暂维持：与 2746 自洽（隐含 USD/CNY ≈ 6.865）。
CNY_PER_RH_DEFAULT = 0.0025

TOKEN_KEYS = ('input', 'output', 'cacheRead', 'cacheWrite', 'reasoning', 'calls')


def _harden_stdio() -> None:
    """中文 Windows 控制台是 GBK(cp936)，本报表含 ¥ 等 GBK 装不下的字符。
    与 scripts/drama.py 同一套做法：按 isatty() 分流 + errors=replace 兜底。"""
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


# --------------------------------------------------------------------------
# 折算率
# --------------------------------------------------------------------------
def load_rates() -> tuple[float, float, float]:
    """返回 (cny_per_rh, rh_per_usd, usd_per_cny)。率以台账 json 为准，缺则用默认值。"""
    cny_per_rh = CNY_PER_RH_DEFAULT
    rh_per_usd = RH_PER_USD_DEFAULT
    try:
        d = json.loads(RH_RATE_JSON.read_text(encoding='utf-8'))
        r = d.get('rh_rate') or {}
        cny_per_rh = float(r.get('cny_per_rh_coin') or cny_per_rh)
        rh_per_usd = float(r.get('rh_coins_per_usd') or rh_per_usd)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return cny_per_rh, rh_per_usd, rh_per_usd * cny_per_rh


# --------------------------------------------------------------------------
# 平台消耗（只用 usage）
# --------------------------------------------------------------------------
def save_auto_ledger() -> dict:
    """引擎自动落盘的逐 attempt 台账 → 按 batch_id 分组的「单次任务」。"""
    batches: dict[str, dict] = {}
    if not AUTO_RUNS.is_dir():
        return batches
    for p in sorted(AUTO_RUNS.glob('*.json')):
        try:
            rec = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        bid = rec.get('batch_id') or 'unknown'
        sid = rec.get('shot_id') or 'unknown'
        b = batches.setdefault(bid, {
            'batch_id': bid, 'stage': rec.get('stage') or '', 'route': rec.get('route') or '',
            'shots': set(), 'per_shot': {}, 'ok': 0, 'fail': 0, 'rh_coins': 0.0, 'wallet_usd': 0.0,
            'wall_s': 0.0, 'attempts': 0, 'first_at': None, 'last_at': None,
        })
        ps = b['per_shot'].setdefault(sid, {
            'ok': 0, 'fail': 0, 'rh_coins': 0.0, 'wallet_usd': 0.0,
            'task_id': None, 'duration_s': None, 'frames': None,
            'wall_s': 0.0, 'artifact': None, 'status': None, 'at': None,
        })
        b['shots'].add(sid)
        for a in (rec.get('attempts') or []):
            b['attempts'] += 1
            st = a.get('status')
            ps['status'] = st
            if a.get('task_id'):
                ps['task_id'] = a['task_id']
            if a.get('duration_s') is not None:
                ps['duration_s'] = a['duration_s']
            n = a.get('normalized') or {}
            if n.get('frames') is not None:
                ps['frames'] = n['frames']
            if a.get('artifact'):
                ps['artifact'] = a['artifact']
            if st == 'SUCCESS':
                rh = float(n.get('rh_coins') or 0)
                wu = float(a.get('wallet_usd') or 0)
                b['ok'] += 1
                b['rh_coins'] += rh
                b['wallet_usd'] += wu
                ps['ok'] += 1
                ps['rh_coins'] += rh
                ps['wallet_usd'] += wu
            elif st == 'FAILED':
                b['fail'] += 1
                ps['fail'] += 1
            w = a.get('wall_elapsed_s')
            if isinstance(w, (int, float)):
                b['wall_s'] += float(w)
                ps['wall_s'] += float(w)
            for k in ('started_at', 'created_at', 'finished_at', 'updated_at'):
                t = a.get(k)
                if t:
                    if not ps['at']:
                        ps['at'] = t
                    if not b['first_at'] or t < b['first_at']:
                        b['first_at'] = t
                    if not b['last_at'] or t > b['last_at']:
                        b['last_at'] = t
        # stage/route 可能只在第一条记录里有值，补一次
        if not b['stage']:
            b['stage'] = rec.get('stage') or ''
    for b in batches.values():
        b['shots'] = sorted(b['shots'])
        b['kind'] = classify(b)
    return batches


def classify(b: dict) -> str:
    """把批次归类成人话：视频出片 / 人物资产 / 场景资产。"""
    bid = b['batch_id']
    stage = (b['stage'] or '').lower()
    if 'char' in bid or 'char' in stage:
        return '人物资产'
    if 'scene' in bid or 'scene' in stage:
        return '场景资产'
    if re.match(r'^\d{8}_\d{6}$', bid) or 'h3' in stage or 'video' in stage:
        return '视频出片'
    return '其他'


def episodes_of(b: dict) -> list[str]:
    eps = sorted({s.split('_')[0] for s in b['shots'] if re.match(r'^ep\d+_', s)})
    return eps


# --------------------------------------------------------------------------
# Agent token（DSH cost-meter）
# --------------------------------------------------------------------------
def _mangle_project_dir(path: pathlib.Path) -> str:
    """把 <workspace 根目录> 还原成 DSH 的会话目录名 --D-~7F16~7A0B-~77ED...-- 形式。

    规则（实测反推）：分隔符与冒号丢弃后用 '-' 连接，非 ASCII 字符写成 ~XXXX（UTF-16 码元），
    整体前后各加 '--'。
    """
    parts = re.split(r'[\\/:]+', str(path))
    segs = []
    for seg in parts:
        if not seg:
            continue
        segs.append(''.join(ch if ch.isascii() and ch.isalnum() else '~%04X' % ord(ch)
                            for ch in seg))
    return '--' + '-'.join(segs) + '--'


def project_session_ids() -> set[str]:
    """本项目在 DSH 账本里的会话 id 集合。三条路依次兜底，确保换 shell 也能跑。"""
    ids: set[str] = set()
    # ① DSH 会话内：从 DSH_SESSION_JSONL 反推项目会话目录
    jsonl = os.environ.get('DSH_SESSION_JSONL', '')
    if jsonl:
        try:
            proj_dir = pathlib.Path(jsonl).resolve().parent.parent
            if proj_dir.is_dir():
                ids |= {p.name for p in proj_dir.iterdir() if p.is_dir()}
        except OSError:
            pass
    # ② 环境里的当前会话 id
    sid = os.environ.get('DSH_SESSION_ID', '')
    if sid:
        ids.add(sid)
    # ③ ★ 文件系统兜底（在普通 shell 里跑时靠这条）：按目录名匹配本工作区
    try:
        want = _mangle_project_dir(WS)
        sess_root = DSH_HOME / 'sessions'
        if sess_root.is_dir():
            for proj in sess_root.iterdir():
                if proj.is_dir() and proj.name == want:
                    ids |= {p.name for p in proj.iterdir() if p.is_dir()}
    except OSError:
        pass
    return ids


def price_for(model_key: str, prices: dict) -> dict:
    """把 'commandcode:deepseek/deepseek-v4.1-flash' 匹配到价表条目。

    匹配是**近似**的（去掉厂商前缀 → 精确命中 → 前缀互含 → 退到 default），
    并把实际命中的键写进 `_matched`，好在报表里如实标出用的是哪一档价 —— 不假装精确。
    """
    tbl = (prices or {}).get('models') or {}
    short = model_key.split(':')[-1].split('/')[-1]
    if short in tbl:
        return {**tbl[short], '_matched': short}
    for k in sorted(tbl, key=len, reverse=True):
        if short.startswith(k) or k.startswith(short):
            return {**tbl[k], '_matched': '%s（近似）' % k}
    return {**((prices or {}).get('default') or {}), '_matched': 'default（价表无此模型名）'}


def load_tokens() -> dict:
    """本项目累计 token（按会话目录归属，逐日累加）。返回 {totals, by_model, by_day, available}"""
    out = {'totals': {k: 0 for k in TOKEN_KEYS}, 'by_model': {}, 'by_day': {},
           'available': False, 'error': ''}
    if not TOKEN_LEDGER.is_file():
        out['error'] = '找不到 DSH 成本账本：%s' % TOKEN_LEDGER
        return out
    try:
        led = json.loads(TOKEN_LEDGER.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as e:
        out['error'] = '账本读取失败：%s' % e
        return out
    ids = project_session_ids()
    out['session_ids'] = sorted(ids)
    prices = ((led.get('config') or {}).get('prices') or {})
    for day, rec in sorted((led.get('days') or {}).items()):
        dsub = {k: 0 for k in TOKEN_KEYS}
        for s in (rec.get('sessions') or []):
            if ids and s.get('id') not in ids:
                continue
            if not ids:
                continue                      # 认不出项目会话就一律不认，宁可报 0 也不虚报
            for k in TOKEN_KEYS:
                v = s.get(k) or 0
                dsub[k] += v
                out['totals'][k] += v
            for mk, mv in (s.get('byProviderModel') or {}).items():
                agg = out['by_model'].setdefault(mk, {k: 0 for k in TOKEN_KEYS})
                for k in TOKEN_KEYS:
                    agg[k] += mv.get(k) or 0
        if any(dsub.values()):
            out['by_day'][day] = dsub
    out['available'] = bool(out['totals']['calls'])
    # 名义计费（按挂牌价折算）；实际扣费 apiCost 另算
    notional = 0.0
    matched: dict[str, str] = {}
    for mk, agg in out['by_model'].items():
        p = price_for(mk, prices)
        matched[mk] = p.get('_matched') or '（default）'
        notional += (agg['input'] * (p.get('cacheMiss') or 0)
                     + agg['cacheRead'] * (p.get('cacheHit') or 0)
                     + agg['output'] * (p.get('output') or 0)) / 1_000_000.0
    out['notional_cny'] = notional
    out['price_matched'] = matched
    # 实际扣费：账本里按项目会话过滤后的 apiCost 合计
    actual = 0.0
    for day, rec in (led.get('days') or {}).items():
        for s in (rec.get('sessions') or []):
            if ids and s.get('id') in ids:
                actual += float(s.get('apiCost') or 0)
    out['actual_cny'] = actual
    return out


# --------------------------------------------------------------------------
# 结账基线
# --------------------------------------------------------------------------
def load_checkpoint() -> dict:
    if CHECKPOINT.is_file():
        try:
            return json.loads(CHECKPOINT.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


# --------------------------------------------------------------------------
# token 归属（用户 2026-09-20 裁决：尽量归到两大类并标「近似」，其余进「未归属」）
# --------------------------------------------------------------------------
TOKEN_ATTR = WS / 'docs' / 'cost-token-attribution.json'


def load_token_attr() -> dict:
    if TOKEN_ATTR.is_file():
        try:
            d = json.loads(TOKEN_ATTR.read_text(encoding='utf-8'))
            d.setdefault('batches', {})
            d.setdefault('unattributed', {'calls': 0, 'tokens': 0, 'notional_cny': 0.0})
            return d
        except (OSError, json.JSONDecodeError):
            pass
    return {'_note': 'token 归属台账（本脚本自动维护）。token 账本只有按天/按会话粒度、'
                     '没有逐批次时间戳，所以「单次任务 token」是按结账区间的增量近似归属的。',
            'batches': {}, 'unattributed': {'calls': 0, 'tokens': 0, 'notional_cny': 0.0}}


def save_token_attr(d: dict) -> None:
    TOKEN_ATTR.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_ATTR.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')


def attribute_tokens(attr: dict, cp: dict, tokens: dict, batches: dict) -> dict:
    """把「自上次结账以来的 token 增量」归属到新批次 / 大类 / 未归属。

    归属规则（尽量归，归不了就不硬塞）：
      * 区间内**恰好 1 个新批次** ⇒ 归给该批次（标近似）
      * 区间内多个新批次，但**同属一个大类** ⇒ 归给该大类（标近似，不摊到批次）
      * 跨大类 ⇒ 全部进「未归属」（并记下列表，方便人工看）
    每个批次只归属一次（已记在 attr['batches'] 里的不再重复计入）。
    """
    if not cp:
        return attr
    d_calls = tokens['totals']['calls'] - (cp.get('tokens') or {}).get('calls', 0)
    d_all = sum(tokens['totals'][k] for k in ('input', 'cacheRead', 'output')) \
        - sum((cp.get('tokens') or {}).get(k, 0) for k in ('input', 'cacheRead', 'output'))
    d_notional = tokens.get('notional_cny', 0.0) - float(cp.get('notional_cny') or 0)
    if d_calls <= 0 and d_all <= 0:
        return attr

    known = set(attr['batches'].keys())
    cp_known = set(cp.get('batches_known') or [])
    new_b = [b for b in batches.values() if b['batch_id'] not in cp_known]
    fresh = [b for b in new_b if b['batch_id'] not in known]

    rec = {'calls': d_calls, 'tokens': d_all, 'notional_cny': round(d_notional, 6),
           'at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'), '_approx': True}

    classes = sorted({b['kind'] for b in fresh})
    if len(fresh) == 1:
        bid = fresh[0]['batch_id']
        attr['batches'][bid] = {**rec, 'scope': 'batch', 'batch_id': bid, 'kind': fresh[0]['kind']}
        rec['_how'] = '区间内仅 1 个新批次 → 归给该批次'
    elif len(classes) == 1 and fresh:
        for b in fresh:
            attr['batches'][b['batch_id']] = {
                **rec, 'scope': 'class', 'kind': b['kind'], 'batch_id': b['batch_id'],
                '_note': '区间内 %d 个同大类批次，无法拆到单批 ⇒ 按大类归属（合计只计一次）'
                         % len(fresh)}
        rec['_how'] = '区间内 %d 个新批次同属「%s」→ 归给该大类' % (len(fresh), classes[0])
    else:
        u = attr['unattributed']
        u['calls'] += d_calls
        u['tokens'] += d_all
        u['notional_cny'] = round(u['notional_cny'] + d_notional, 6)
        if not fresh:
            rec['_how'] = '区间内没有新任务 ⇒ 这段 token 是文档/排查等非任务工作产生的，记入「未归属」'
        else:
            rec['_how'] = ('区间内 %d 个新批次跨 %d 个大类 ⇒ 无法归属，记入「未归属」'
                           % (len(fresh), len(classes)))
        rec['_batches'] = [b['batch_id'] for b in fresh]
        attr['_last_unattributed'] = rec
    if fresh:
        attr['_last_attribution'] = rec
    return attr


def save_checkpoint(tokens: dict, batches: dict, rh_cny: float, rh_per_usd: float) -> dict:
    batches_rh = sum(b['rh_coins'] for b in batches.values())
    batches_wallet = sum(b['wallet_usd'] for b in batches.values())
    cp = {
        '_note': '成本台账的结账基线（本脚本自动维护）。--checkpoint 打一次基线，'
                 '下次运行即可算出「自上次结账以来」的单次任务增量。',
        'at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'rh_coins_cum': batches_rh,
        'wallet_usd_cum': batches_wallet,
        'wallet_as_rh_cum': batches_wallet * rh_per_usd,
        'tokens': dict(tokens.get('totals') or {}),
        'notional_cny': tokens.get('notional_cny', 0.0),
        'actual_cny': tokens.get('actual_cny', 0.0),
        'batches_known': sorted(batches.keys()),
    }
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding='utf-8')
    return cp


# --------------------------------------------------------------------------
# 表格
# --------------------------------------------------------------------------
def fmt_money(v: float, unit: str = '¥') -> str:
    return '%s%.2f' % (unit, v)


def _parse_ts(s: str):
    """台账里的时间戳是本地时间 ISO 串（'2026-09-19T00:20:19'），也兼容带时区的。"""
    if not s:
        return None
    t = str(s).strip().replace('Z', '').split('+')[0]
    for f in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M'):
        try:
            return datetime.strptime(t[:19], f)
        except ValueError:
            continue
    return None


def build_rows(batches: dict[dict], rh_per_usd: float, cny_per_rh: float) -> list[dict]:
    rows = []
    for b in batches.values():
        as_rh = b['rh_coins'] + b['wallet_usd'] * rh_per_usd
        t0, t1 = _parse_ts(b['first_at']), _parse_ts(b['last_at'])
        # ★ 批次真实耗时 = 末-首。**不能**用各段墙钟之和：并发 5 时那是「累计等待」，会虚高数倍。
        elapsed = (t1 - t0).total_seconds() if (t0 and t1) else None
        rows.append({
            'batch_id': b['batch_id'], 'kind': b['kind'],
            'episodes': episodes_of(b), 'shots': len(b['shots']),
            'ok': b['ok'], 'fail': b['fail'], 'attempts': b['attempts'],
            'rh_coins': round(b['rh_coins'], 1), 'wallet_usd': round(b['wallet_usd'], 4),
            'as_rh': round(as_rh, 1), 'cny': round(as_rh * cny_per_rh, 4),
            'elapsed_s': round(elapsed, 1) if elapsed else None,
            # ★ 资产类批次的 batch_id 是固定名（char_assets / scene_assets），
            #   跨多次调用会共用同一个批次 ⇒ 首末时间差是「累计跨度」而不是「单次耗时」，必须标出来。
            'span_long': bool(elapsed and elapsed > 3 * 3600),
            'wall_sum_s': round(b['wall_s'], 1),
            'per_shard_s': round(b['wall_s'] / b['ok'], 1) if b['ok'] else None,
            'at': (b['first_at'] or '')[:19],
        })
    rows.sort(key=lambda r: (r['at'] or '', r['batch_id']))
    return rows


def render_md(rows, by_ep, tot, tok, cp, rates, balance=None) -> str:
    cny_per_rh, rh_per_usd, usd_cny = rates
    L = []
    A = L.append
    A('# 短剧成本台账')
    A('')
    A('> 由 `scripts\\cost_ledger.py` 自动生成，请勿手改。生成时间：%s'
      % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    A('>')
    A('> **口径**：消耗只取平台按任务返回的 `usage`，逐条累加；'
      '**不用余额差值倒推消耗**（用户 2026-09-19 明确）。')
    A('> 折算：**1 RH币 = ¥%.4f** ｜ **1 美元 = %.0f RH币**（= ¥%.4f）'
      % (cny_per_rh, rh_per_usd, usd_cny))
    A('')
    A('## 一、单次任务台账（按批次）')
    A('')
    A('| 批次 | 类型 | 覆盖集 | 分镜 | 成功(次) | 失败(次) | 尝试 | RH币 | 美元钱包 | 折合RH币 | 折合人民币 | 批次时长 | 单段均 | 开始时间 |')
    A('|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|')
    for r in rows:
        A('| `%s` | %s | %s | %d | %d | %d | %d | %s | %s | %s | %s | %s | %s | %s |'
          % (r['batch_id'], r['kind'], ', '.join(r['episodes']) or '—', r['shots'],
             r['ok'], r['fail'], r['attempts'],
             ('%.0f' % r['rh_coins']) if r['rh_coins'] else '—',
             ('$%.4f' % r['wallet_usd']) if r['wallet_usd'] else '—',
             '%.0f' % r['as_rh'], fmt_money(r['cny']),
             ('%.0f 分%s' % (r['elapsed_s'] / 60, '（累计跨度）' if r['span_long'] else ''))
             if r['elapsed_s'] else '—',
             ('%.0fs' % r['per_shard_s']) if r['per_shard_s'] else '—',
             r['at'] or '—'))
    A('')
    A('> **批次时长** = 该批首末时间差（真实耗时）；标「累计跨度」的是资产批次 ——'
      '它们的 batch_id 是固定名（`char_assets`/`scene_assets`），跨多次调用共用一个批次，'
      '所以首末差是**跨度**而非单次耗时。**单段均** = 各段墙钟之和 ÷ 成功段数'
      '（并发 5，故各段墙钟之和会远大于批次时长，别混用）。')
    A('')
    A('## 二、按集累计')
    A('')
    A('> 成功/失败按**提交次数**计（失败重跑会各计一次）；金额按分镜精确归集。')
    A('')
    A('| 集 | 分镜 | 成功(次) | 失败(次) | 折合RH币 | 折合人民币 |')
    A('|---|---:|---:|---:|---:|---:|')
    for ep, v in sorted(by_ep.items()):
        if ep == '（资产）':
            continue
        A('| %s | %d | %d | %d | %.0f | %s |'
          % (ep, v['shots'], v['ok'], v['fail'], v['as_rh'], fmt_money(v['cny'])))
    if '（资产）' in by_ep:
        v = by_ep['（资产）']
        A('| **（资产）** | %d | %d | %d | %.0f | %s |'
          % (v['shots'], v['ok'], v['fail'], v['as_rh'], fmt_money(v['cny'])))
    A('| **合计** | **%d** | **%d** | **%d** | **%.0f** | **%s** |'
      % (sum(v['shots'] for v in by_ep.values()),
         sum(v['ok'] for v in by_ep.values()),
         sum(v['fail'] for v in by_ep.values()),
         sum(v['as_rh'] for v in by_ep.values()),
         fmt_money(sum(v['cny'] for v in by_ep.values()))))
    A('')
    A('## 三、三池总账（全剧累计）')
    A('')
    A('| 资金池 / 成本项 | 数值 | 折合 RH币 | 折合人民币 |')
    A('|---|---:|---:|---:|')
    A('| **RH币池**（视频 / AI 应用 / 人物图） | %.0f RH币 | %.0f | %s |'
      % (tot['rh_coins'], tot['rh_coins'], fmt_money(tot['rh_coins'] * cny_per_rh)))
    A('| **美元钱包**（闭源模型 API，即场景图） | $%.4f | %.0f | %s |'
      % (tot['wallet_usd'], tot['wallet_as_rh'], fmt_money(tot['wallet_as_rh'] * cny_per_rh)))
    A('| **平台费小计** | — | **%.0f** | **%s** |'
      % (tot['as_rh'], fmt_money(tot['cny'])))
    A('| Agent token 名义计费 | — | — | %s |' % fmt_money(tok.get('notional_cny', 0.0)))
    A('| Agent token 实际扣费 | — | — | %s（包月订阅，故为 0） |' % fmt_money(tok.get('actual_cny', 0.0)))
    A('| **完整成本（平台费 + token 名义）** | — | — | **%s** |'
      % fmt_money(tot['cny'] + tok.get('notional_cny', 0.0)))
    A('')
    A('**token 明细**：调用 %d 次 ｜ input %s ｜ cacheRead %s ｜ output %s ｜ 合计 %s tokens'
      % (tok['totals']['calls'], f"{tok['totals']['input']:,}", f"{tok['totals']['cacheRead']:,}",
         f"{tok['totals']['output']:,}",
         f"{tok['totals']['input'] + tok['totals']['cacheRead'] + tok['totals']['output']:,}"))
    pm = tok.get('price_matched') or {}
    if pm:
        A('')
        A('> ⚠️ **名义计费用的价表是近似匹配**（本项目实跑模型往往不在价表里）：')
        for mk, hit in pm.items():
            A('> - `%s` → 价表命中 `%s`' % (mk, hit))
        A('> **实际扣费以「Agent token 实际扣费」一行为准**（包月订阅时为 0）；名义值只用于横向比较。')
    A('')
    A('## 四、自上次结账以来（单次任务增量）')
    A('')
    if not cp:
        A('> 还没有基线。任务做完后跑一次 `python scripts\\cost_ledger.py --checkpoint` 建立基线，')
        A('> 之后每跑一次本脚本就能看到「单次任务」的增量。')
    else:
        d_rh = tot['rh_coins'] - float(cp.get('rh_coins_cum') or 0)
        d_w = tot['wallet_usd'] - float(cp.get('wallet_usd_cum') or 0)
        d_tk = {k: tok['totals'][k] - (cp.get('tokens') or {}).get(k, 0) for k in TOKEN_KEYS}
        d_notional = tok.get('notional_cny', 0.0) - float(cp.get('notional_cny') or 0)
        new_b = [b for b in sorted(batches_in_scope(rows, cp))]
        A('基线时间：%s' % cp.get('at'))
        A('')
        A('| 项 | 增量 |')
        A('|---|---:|')
        A('| RH币 | %+.0f |' % d_rh)
        A('| 美元钱包 | %+.4f USD |' % d_w)
        A('| token 名义计费 | %+.4f |' % d_notional)
        A('| token 调用次数 | %+d |' % d_tk['calls'])
        A('| token 合计 | %+d |' % (d_tk['input'] + d_tk['cacheRead'] + d_tk['output']))
        if new_b:
            A('')
            A('期间新增批次：%s' % ', '.join('`%s`' % b for b in new_b))
            if len(new_b) > 1:
                A('')
                A('> ⚠️ 本次区间含 %d 个批次，token 增量是**整段合计**、无法再细分到单个批次'
                  % len(new_b))
                A('> （DSH 账本只有「按天/按会话」粒度，没有逐批次的时间戳）。'
                  '想要精确的单次 token，就每做完一个任务打一次基线。')
    A('')
    if balance:
        A('## 五、余额可用性（只读；**不参与消耗统计、不做归因**）')
        A('')
        A('| 项 | 数值 |')
        A('|---|---:|')
        for k, v in balance.items():
            A('| %s | %s |' % (k, v))
        A('')
    A('---')
    A('')
    A('*本表由 `cost_ledger.py` 生成；消耗数据来自 `video-pipeline/ledger/runs/`（引擎自动落盘）'
      '与 DSH cost-meter 账本。*')
    return '\n'.join(L) + '\n'


def batches_in_scope(rows, cp) -> list[str]:
    known = set(cp.get('batches_known') or [])
    return [r['batch_id'] for r in rows if r['batch_id'] not in known]


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Excel（.xlsx）台账
# --------------------------------------------------------------------------
XLSX_STATE = WS / 'docs' / '.cost-xlsx-state.json'
DRAMA_DEFAULT = '七大姑催婚'


def big_class(kind: str) -> str:
    """引擎台账的 kind → 用户要的「大类」（成本台账按这两类核算）。"""
    if kind in ('人物资产', '场景资产'):
        return '角色/场景资产类'
    if kind == '视频出片':
        return '剧集生产类'
    return '其他'


def load_asset_specs() -> dict:
    """资产图片规格。★ 优先报「送 H3 的瘦身图」规格 —— 那才是真正进参考槽位的东西；
    原始 6000px 大图因为超 H3 上限（5760）根本不会被送出去，只作为附注。"""
    global DATA
    orig: dict[str, str] = {}
    for fn in ('char_assets_manifest.json', 'scene_assets_manifest.json'):
        f = DATA / fn
        if not f.is_file():
            continue
        try:
            man = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        for it in man.get('items', []):
            key = it.get('key')
            files = [x for x in (it.get('files') or []) if isinstance(x, dict)]
            main = next((x for x in files if x.get('role') == 'asset'), files[0] if files else None)
            if key and main:
                orig[key] = '%sx%s · %.2fMB' % (main.get('width'), main.get('height'),
                                                main.get('mb') or 0)

    specs: dict[str, str] = {}
    try:
        chars = json.loads((DATA / 'characters.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        chars = {}
    try:
        from PIL import Image
    except ImportError:
        Image = None
    for k, v in chars.items():
        if k.startswith('_') or not isinstance(v, str) or v.lower().startswith(('http', 'data:')):
            continue
        p = DATA / v.lstrip('./').replace('/', os.sep)
        if not p.is_file():
            continue
        dim = ''
        if Image is not None:
            try:
                with Image.open(p) as im:
                    w, h = im.size
                dim = '%dx%d · %.2fMB' % (w, h, p.stat().st_size / 1048576)
            except OSError:
                dim = ''
        specs[k] = ('送H3 %s' % dim) if dim else '送H3（已接入）'
        if k in orig:
            specs[k] += '（原始 %s）' % orig[k]
    for k, s in orig.items():
        specs.setdefault(k, '原始 %s（未接入 characters.json）' % s)
    return specs


def build_xlsx_ctx(drama, batches, by_ep, tokens, attr, rates, state_prev) -> tuple[dict, dict]:
    """把台账数据整理成 Excel 生成器要的形状。"""
    cny_per_rh, rh_per_usd, usd_cny = rates
    specs = load_asset_specs()

    # token 按归属对象索引：{批次ID: ...} 与 {大类名: ...}
    # scope='batch' → 归到具体批次（任务台账那一行的 token 列会引用它）
    # scope='class' → 只能归到大类（同区间多个同大类批次），**只计一次**，避免重复累加
    tok_by_obj: dict[str, dict] = {}
    tok_rows = []
    for bid, rec in (attr.get('batches') or {}).items():
        if rec.get('scope') == 'class':
            obj = big_class(rec.get('kind') or '')
            cur = tok_by_obj.setdefault(obj, {'tokens': 0, 'notional_cny': 0.0})
            cur['tokens'] += rec.get('tokens') or 0
            cur['notional_cny'] += rec.get('notional_cny') or 0
        else:
            tok_by_obj[bid] = {'tokens': rec.get('tokens'),
                               'notional_cny': rec.get('notional_cny')}
    ua = attr.get('unattributed') or {}
    if ua.get('tokens'):
        tok_by_obj['未归属（无法归类）'] = {'tokens': ua.get('tokens'),
                                           'notional_cny': ua.get('notional_cny')}

    for obj, rec in sorted(tok_by_obj.items()):
        if obj == '未归属（无法归类）':
            scope, why = '未归属', '区间内跨多个大类 ⇒ 无法归属'
        elif re.match(r'^(\d{8}_\d{6}|char_assets|scene_assets)', str(obj)):
            scope, why = '批次', '按结账区间增量近似归属（★ 会混入同区间内其它工作）'
        else:
            scope, why = '大类', '同区间多个同大类批次，拆不到单批 ⇒ 归到大类（只计一次）'
        tok_rows.append({
            'obj': obj, 'scope': scope, 'at': (attr.get('_last_attribution') or {}).get('at', ''),
            'calls': None, 'tokens': rec.get('tokens'), 'notional': rec.get('notional_cny'),
            'why': why,
        })

    # 任务台账
    tasks = []
    for b in sorted(batches.values(), key=lambda x: x['batch_id']):
        eps = episodes_of(b)
        if b['kind'] in ('人物资产', '场景资产'):
            label = '人物定妆图' if b['kind'] == '人物资产' else '场景空镜'
        elif eps:
            label = '%s 出片' % ','.join(eps)
        else:
            label = b['kind']
        t0, t1 = _parse_ts(b['first_at']), _parse_ts(b['last_at'])
        el = (t1 - t0).total_seconds() if (t0 and t1) else None
        as_rh = b['rh_coins'] + b['wallet_usd'] * rh_per_usd
        note = []
        if b['fail']:
            note.append('含 %d 次重跑' % b['fail'])
        if el and el > 3 * 3600:
            note.append('跨多次调用（时长为跨度）')
        tasks.append({
            'batch_id': b['batch_id'], 'at': (b['first_at'] or '')[:19], 'cls': big_class(b['kind']),
            'label': label, 'qty': len(b['shots']), 'ok': b['ok'], 'fail': b['fail'],
            'rh': round(b['rh_coins'], 1), 'usd': round(b['wallet_usd'], 4),
            'as_rh': round(as_rh, 1), 'cny': round(as_rh * cny_per_rh, 2),
            'elapsed': ('%.0f 分' % (el / 60)) if el else '', 'note': '；'.join(note),
        })

    # 资产明细
    assets = []
    for b in batches.values():
        for sid, ps in b['per_shot'].items():
            if not re.match(r'^(char|scene)_', sid):
                continue
            art = (ps.get('artifact') or '').split(';')[0]
            assets.append({
                'key': sid, 'name': (specs.get(sid) and '') or sid,
                'type': '角色' if sid.startswith('char') else '场景',
                'batch': b['batch_id'], 'task_id': ps.get('task_id') or '',
                'rh': round(ps['rh_coins'], 1), 'usd': round(ps['wallet_usd'], 4),
                'cny': round((ps['rh_coins'] + ps['wallet_usd'] * rh_per_usd) * cny_per_rh, 4),
                'spec': specs.get(sid, ''), 'file': art,
            })
    # 资产名从清单补（key 形如 char_10 → 用清单里的名字）
    name_map = {}
    for fn in ('char_assets_manifest.json', 'scene_assets_manifest.json'):
        f = VP / 'data' / fn
        if f.is_file():
            try:
                for it in json.loads(f.read_text(encoding='utf-8')).get('items', []):
                    if it.get('key'):
                        name_map[it['key']] = it.get('name') or ''
            except (OSError, json.JSONDecodeError):
                pass
    for a in assets:
        a['name'] = name_map.get(a['key']) or a['key']
    assets.sort(key=lambda x: (x['type'], x['key']))

    # 剧集明细
    episodes = []
    for ep, v in sorted(by_ep.items()):
        if not re.match(r'^ep\d+$', ep):
            continue
        outdir = ''
        for b in batches.values():
            if ep in episodes_of(b):
                outdir = str(VP / 'output' / b['batch_id'])
                break
        episodes.append({'ep': ep, 'shots': v['shots'], 'ok': v['ok'], 'fail': v['fail'],
                         'rh': round(v['as_rh'], 1), 'dur': None, 'outdir': outdir})

    # 逐段明细
    shots = []
    for b in batches.values():
        for sid, ps in b['per_shot'].items():
            if not re.match(r'^ep\d+_', sid):
                continue
            art = (ps.get('artifact') or '')
            shots.append({'shot': sid, 'ep': sid.split('_')[0], 'dur': ps.get('duration_s'),
                          'frames': ps.get('frames'), 'status': ps.get('status'),
                          'rh': round(ps['rh_coins'], 1) or None, 'wall': ps.get('wall_s') and round(ps['wall_s'], 0),
                          'task_id': ps.get('task_id') or '', 'file': art})
    shots.sort(key=lambda x: x['shot'])

    notes = [
        ('短剧', drama),
        ('生成时间', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
        ('生成脚本', 'scripts\\cost_ledger.py + scripts\\cost_xlsx.py'),
        ('★ 消耗口径', '只取平台按任务返回的 usage（consumeCoins / consumeMoney / '
                       'thirdPartyConsumeMoney），逐条累加。'),
        ('★ 铁律', '**不用余额差值倒推消耗**（用户 2026-09-19 明确）。余额变动混入充值、订阅续费、'
                   '其他产品与其他使用者，不属于本次消耗。'),
        ('折算率', '1 RH币 = ¥%.4f；1 美元 = %.0f RH币（= ¥%.4f）。来源：docs\\runninghub-runs.json 的 rh_rate'
                   % (cny_per_rh, rh_per_usd, usd_cny)),
        ('资金池归属', 'RH币池 = 视频出片 / AI 应用 / 人物定妆图；美元钱包 = 闭源模型 API（场景空镜）。'),
        ('token 名义 vs 实际', '名义计费按挂牌价折算，仅用于横向比较；实际扣费看「口径说明」下方与 '
                               'cost_report.py 的实际扣费行（包月订阅时 apiCost=0）。'),
        ('★ token 归类的限制', 'DSH 账本只有「按天/按会话」粒度，没有逐批次时间戳 ⇒ 单次 token 只能靠'
                               '「结账区间增量」近似归属；且会混入同区间内我做的其它事。'
                               '历史任务无法回溯拆分，故 token 列为空白。'),
        ('RH币/钱包 的精度', '可精确到分镜与资产项（逐 attempt 台账，来自平台 usage）。'),
        ('数据源', '平台消耗：video-pipeline\\ledger\\runs\\*.json（引擎自动落盘）；'
                   'token：$DSH_HOME\\storages\\cost-meter\\ledger.json（按项目会话归属）；'
                   '资产规格：data\\char_assets_manifest.json / scene_assets_manifest.json。'),
        ('更新方式', '本工作簿由脚本**只刷数据区**（表头下那几行），保留其它工作表与单元格批注；'
                     '数据区之外的备注行不会被清掉。'),
        ('本表自动生成', '请勿手改数据区；要加说明请写在数据区之外。'),
    ]
    return ({
        'drama': drama, 'tasks': tasks, 'assets': assets, 'episodes': episodes,
        'shots': shots, 'tok_rows': tok_rows, 'tok_by_obj': tok_by_obj,
        'rates': {'cny_per_rh': cny_per_rh, 'rh_per_usd': rh_per_usd, 'usd_cny': usd_cny},
        'notes': notes,
    }, {})


def write_xlsx(path, ctx, state_prev) -> dict:
    import cost_xlsx
    cost_xlsx.STATE = state_prev or {}
    st = cost_xlsx.build(path, ctx)
    try:
        XLSX_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        pass
    return st


def load_xlsx_state() -> dict:
    if XLSX_STATE.is_file():
        try:
            return json.loads(XLSX_STATE.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def main() -> int:
    _harden_stdio()
    ap = argparse.ArgumentParser(description='短剧成本台账（单次任务 + 全剧累计）')
    ap.add_argument('--checkpoint', action='store_true', help='把当前状态记为结账基线')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    ap.add_argument('--balance', action='store_true',
                    help='附带只读余额（仅判断够不够跑；绝不用于倒推消耗）')
    ap.add_argument('--md', default=None, help='表格输出路径（默认 docs\\成本台账.md）')
    ap.add_argument('--no-write', action='store_true', help='只打印，不写文件')
    ap.add_argument('--xlsx', action='store_true',
                    help='★ 生成 Excel 台账 docs\\成本台账_<剧名>.xlsx（归类核算，7 个表）')
    ap.add_argument('--drama', default=DRAMA_DEFAULT, help='短剧名（决定文件名与表标题）')
    ap.add_argument('--no-md', action='store_true', help='不写 markdown（只出 Excel 时用）')
    args = ap.parse_args()

    cny_per_rh, rh_per_usd, usd_cny = load_rates()
    batches = save_auto_ledger()
    tokens = load_tokens()
    attr = load_token_attr()

    # 三池合计（只来自 usage）
    tot = {
        'rh_coins': sum(b['rh_coins'] for b in batches.values()),
        'wallet_usd': sum(b['wallet_usd'] for b in batches.values()),
    }
    tot['wallet_as_rh'] = tot['wallet_usd'] * rh_per_usd
    tot['as_rh'] = tot['rh_coins'] + tot['wallet_as_rh']
    tot['cny'] = tot['as_rh'] * cny_per_rh

    # 按集：逐分镜归集（一个批次可能跨集；资产批次归到「（资产）」），精确到分镜级、不用分摊
    by_ep: dict[str, dict] = {}
    for b in batches.values():
        for sid, ps in b['per_shot'].items():
            m = re.match(r'^(ep\d+)_', sid)
            k = m.group(1) if m else '（资产）'
            v = by_ep.setdefault(k, {'shots': 0, 'ok': 0, 'fail': 0, 'as_rh': 0.0, 'cny': 0.0})
            v['shots'] += 1
            v['ok'] += ps['ok']
            v['fail'] += ps['fail']
            v['as_rh'] += ps['rh_coins'] + ps['wallet_usd'] * rh_per_usd
    for v in by_ep.values():
        v['cny'] = v['as_rh'] * cny_per_rh

    rows = build_rows(batches, rh_per_usd, cny_per_rh)
    cp = load_checkpoint()

    if args.checkpoint:
        # ★ 先做 token 归属（用「上一次基线」当区间起点），再落新基线 —— 顺序不能反
        attr = attribute_tokens(attr, cp, tokens, batches)
        save_token_attr(attr)
        cp = save_checkpoint(tokens, batches, cny_per_rh, rh_per_usd)
        print('已建立结账基线：%s' % CHECKPOINT)
        print('  累计 RH币 %.0f ｜ 钱包 $%.4f ｜ token 名义 ¥%.4f ｜ 已知批次 %d 个'
              % (cp['rh_coins_cum'], cp['wallet_usd_cum'], cp['notional_cny'],
                 len(cp['batches_known'])))
        la = attr.get('_last_attribution')
        if la:
            print('  token 归属：%s' % la.get('_how'))
            if la.get('_batches'):
                print('              跨大类批次：%s' % ', '.join(la['_batches']))

    balance = None
    if args.balance:
        balance = read_balance()

    if args.json:
        print(json.dumps({
            'rates': {'cny_per_rh': cny_per_rh, 'rh_per_usd': rh_per_usd, 'usd_cny': usd_cny},
            'batches': rows, 'by_episode': by_ep, 'totals': tot,
            'tokens': {k: tokens.get(k) for k in ('totals', 'notional_cny', 'actual_cny', 'available', 'error')},
            'checkpoint': cp, 'balance': balance,
        }, ensure_ascii=False, indent=2))
        return 0

    md = render_md(rows, by_ep, tot, tokens, cp, (cny_per_rh, rh_per_usd, usd_cny), balance)
    print(md)
    if not args.no_write and not args.no_md:
        out = pathlib.Path(args.md) if args.md else (WS / 'docs' / '成本台账.md')
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding='utf-8')
        print('→ 表格台账已写入：%s' % out)

    if args.xlsx:
        ctx, _ = build_xlsx_ctx(args.drama, batches, by_ep, tokens, attr,
                               (cny_per_rh, rh_per_usd, usd_cny), None)
        xp = WS / 'docs' / ('成本台账_%s.xlsx' % args.drama)
        st = write_xlsx(xp, ctx, load_xlsx_state())
        print('→ Excel 台账已写入：%s' % xp)
        print('   工作表：%s' % '、'.join('%s(%d 行)' % (k, v) for k, v in st.items()))
    return 0


def read_balance() -> dict:
    """只读余额。★ 仅用于判断「够不够跑」；绝不用于倒推消耗。"""
    import asyncio
    sys.path.insert(0, str(pathlib.Path(
        os.environ.get('AI_VIDEO_PIPELINE_SKILL')
        or pathlib.Path(__file__).resolve().parents[3] / 'stages' / 'video' / 'workflow')
        / 'scripts' / 'engine'))
    try:
        import config as C                                  # noqa: N812
        from runninghub_client import RunningHubClient
        ov = VP / 'config.local.json'
        if ov.is_file():
            C.apply_overlay(ov)
        import winreg
        key = os.environ.get('RUNNINGHUB_API_KEY')
        if not key:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
                key = winreg.QueryValueEx(k, 'RUNNINGHUB_API_KEY')[0]
        C.RUNNINGHUB_API_KEY = key

        async def go():
            async with RunningHubClient(key, C.RH_BASE_URL, C.HTTP_TIMEOUT) as c:
                return await c._request('POST', '/uc/openapi/accountStatus',   # noqa: SLF001
                                        json_body={'apikey': key}, context='账户信息')
        d = (asyncio.run(go()).get('data') or {})
        return {'RH币余额': d.get('remainCoins'),
                '美元钱包': '%s %s' % (d.get('remainMoney'), d.get('currency') or ''),
                '在跑任务数': d.get('currentTaskCounts'),
                '_口径': '只读快照；余额变动含充值与其他扣费，**不作为消耗依据**'}
    except Exception as e:                                   # noqa: BLE001
        return {'读取失败': '%s: %s' % (type(e).__name__, str(e)[:120])}


if __name__ == '__main__':
    sys.exit(main())
