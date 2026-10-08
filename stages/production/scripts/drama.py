#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""drama.py —— 短剧生产线的零思考入口（把已跑通的重复动作固化成命令）

为什么有这个脚本
----------------
《七大姑催婚》量产阶段每次要重复的动作，之前都靠「翻文档 + 现场拼命令 + 逐个读脚本」，
token 全花在重复探索上。这里把它们固化成**稳定命令**：
每个命令都自带闸门（默认不花钱）、自带进度、跑完只吐**一份紧凑战报**，
人和 agent 都只需要读一份摘要，不必再读脚本源码或长日志。

命令一览（`python scripts/drama.py guide` 也会打印同样内容）
-----------------------------------------------------------
    status     [--json]                  全盘状态面板：数据/参考图/资产缺口/批次/台账（零网络）
    preflight                            一次性体检：Key 分级 + 引擎 doctor + check + 预演（全免费）
    assets     [--submit] [--kind ...]   缺失资产出图（默认干跑；--submit 才计费）
    refs       [--apply]                 参考图瘦身（6000px 超 H3 上限 5760 必须做）
    video plan [--limit N] [--only ids]  出片计划与成本预估（免费，不提交）
    video run  --yes [...]               真跑出片（★计费；--yes 是闸门）
    video status [BATCH]                 某批次进度（读 summary.json）
    report     [--latest|--batch ID]     战报：批次汇总 + 台账聚合 + 成本
    balance                              两把 Key 的余额（只读，不计费）
    cost [--checkpoint] [--balance] [--json]  成本台账：单次任务 + 全剧累计（三池表格）
    guide                                打印这份速查

设计约定（照用户偏好来的）
--------------------------
* **默认不花钱**：所有会提交任务的命令都要显式 `--submit` / `--yes`，否则只干跑。
* **进度可见**：子进程输出实时落日志文件，同时打印进度行；失败才回显日志尾部。
* **战报优先**：成功路径只打印几行摘要 + 产物绝对路径；不贴长日志。
* **机密不进文件**：Key 只从环境变量/用户级环境变量读，永不写盘、永不打印全文。
* **留痕自动化**：产物清单、引擎台账、批次 summary 都由脚本落盘，不需要手工登记。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _ws import WS as _WS  # noqa: E402  ★ 工作区解析（env > 位置推断 > 报错），见 scripts/_ws.py

WS = _WS
VP = WS / 'video-pipeline'
SCRIPTS = WS / 'scripts'
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]   # .../Drama-agent
SKILL = pathlib.Path(os.environ.get('AI_VIDEO_PIPELINE_SKILL')
                     or _PROJECT_ROOT / 'stages' / 'video' / 'workflow')
RUN_PY = SKILL / 'scripts' / 'run.py'
CHECK_BILLING = SKILL / 'scripts' / 'check_billing.py'
LOG_DIR = VP / 'output' / 'logs'

CNY_PER_RH = 0.0025          # 1 RH币 ≈ ¥0.0025（用户 2026-09-18）；★ 2026-09-19 用户另给 RH币↔USD：
                             # 1 美元 = 2746 RH币（1 RH币 ≈ $0.0003642）。两者隐含 USD/CNY ≈ 6.865，该汇率未确认。
CHAR_RH_PER_IMAGE = 27       # 实测：人物定妆图 27 RH币/张
SCENE_USD_PER_IMAGE = 0.01   # 实测：场景图扣美元钱包 $0.01/张 ≈ ￥0.07
USD_CNY = 7.0


# ---------------------------------------------------------------------------
# 环境：进程环境里没有的 Key，从「用户级环境变量」补（agent 的 shell 常拿不到）
# ---------------------------------------------------------------------------
def _harden_stdio() -> None:
    """★ 中文 Windows 控制台是 GBK(cp936)，而管道/重定向通常按 UTF-8 读 —— 两种情况要求相反：

    * **在控制台里跑（双击 .bat）**：必须用控制台自己的代码页，否则中文全乱码；
    * **被管道/文件捕获（agent、日志）**：必须 UTF-8，否则读出来是乱码。

    所以按 `isatty()` 分流，并且一律把 errors 放宽成 replace（防 GBK 装不下的字符直接抛异常）。
    第二层保险：正文不使用 GBK 装不下的符号（用 [OK]/[NG]/-> 代替）。
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


def hydrate_env() -> None:
    names = ('RUNNINGHUB_API_KEY', 'RUNNINGHUB_ENT_API_KEY', 'RUNNINGHUB_BASE_URL',
             'VIDEO_PIPELINE_WORKSPACE', 'VIDEO_PIPELINE_ENGINE', 'PYTHONUTF8', 'PYTHONIOENCODING')
    os.environ.setdefault('PYTHONUTF8', '1')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    os.environ['VIDEO_PIPELINE_WORKSPACE'] = str(VP)
    missing = [n for n in names[:3] if not os.environ.get(n)]
    if not missing or os.name != 'nt':
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
            for n in missing:
                try:
                    v, _ = winreg.QueryValueEx(k, n)
                except FileNotFoundError:
                    continue
                if v:
                    os.environ[n] = str(v)
    except Exception:                                          # noqa: BLE001
        pass


def read_json(p: pathlib.Path, default=None):
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return default


def mask(v: str) -> str:
    v = (v or '').strip()
    return '(未设置)' if not v else (v[:6] + '****' + v[-4:] if len(v) > 10 else '****')


def has(key: str) -> str:
    return '有' if os.environ.get(key) else '缺'


# ---------------------------------------------------------------------------
# 子进程执行：实时进度 + 落日志；失败才回显尾部
# ---------------------------------------------------------------------------
def run_cmd(cmd: list, log_name: str, quiet_ok: bool = True) -> tuple:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / ('%s_%s.log' % (log_name, time.strftime('%Y%m%d_%H%M%S')))
    print('  $ %s' % ' '.join(str(c) for c in cmd[1:]))
    print('  日志: %s' % log)
    t0 = time.time()
    with open(log, 'w', encoding='utf-8', errors='replace') as fh:
        proc = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding='utf-8', errors='replace', bufsize=1,
                                cwd=str(WS))
        tail = []
        for line in proc.stdout:                                # type: ignore[union-attr]
            fh.write(line)
            fh.flush()
            tail.append(line.rstrip('\n'))
            if len(tail) > 40:
                tail.pop(0)
            if not quiet_ok:
                print('  | ' + line.rstrip('\n'))
        rc = proc.wait()
    dt = round(time.time() - t0, 1)
    if rc != 0:
        print('  [失败] 退出码 %s（耗时 %.1fs），日志尾部：' % (rc, dt))
        for line in tail[-18:]:
            print('  | ' + line)
    return rc, log, dt


# ---------------------------------------------------------------------------
# 状态面板
# ---------------------------------------------------------------------------
def _img_size(p: pathlib.Path):
    try:
        from PIL import Image
        with Image.open(p) as im:
            return im.size
    except Exception:                                          # noqa: BLE001
        return (0, 0)


def collect_status() -> dict:
    shots = read_json(VP / 'data' / 'shots.json', []) or []
    prompts = read_json(VP / 'data' / 'h3_prompts.json', {}) or {}
    chars = read_json(VP / 'data' / 'characters.json', {}) or {}
    assets_cfg = read_json(VP / 'config.assets.json', {}) or {}
    char_man = read_json(VP / 'data' / 'char_assets_manifest.json', {}) or {}
    scene_man = read_json(VP / 'data' / 'scene_assets_manifest.json', {}) or {}
    local = read_json(VP / 'config.local.json', {}) or {}

    # 参考图
    refs = []
    for k, v in chars.items():
        if k.startswith('_'):
            continue
        p = pathlib.Path(v)
        if not p.is_absolute():
            p = VP / 'data' / p
        w, h = _img_size(p) if p.is_file() else (0, 0)
        refs.append({'key': k, 'file': p.name, 'exists': p.is_file(),
                     'size': [w, h], 'h3_over_limit': max(w, h) > 5760})

    # 资产缺口：以「资产提示词 md 的节号」为准（与 gen_char_assets/gen_scene_assets 同一口径），
    # 不用 config.assets.json 里那套补充清单 C 号（两套编号不可混用，见人物资产文档）。
    char_prompts = read_json(VP / 'data' / 'char_prompts.json', {}) or {}
    scene_prompts = read_json(VP / 'data' / 'scene_prompts.json', {}) or {}
    gen_chars = {str(it.get('name')) for it in char_man.get('items', [])}
    gen_seqs = {it.get('section') for it in char_man.get('items', [])}
    gen_scene_seqs = {it.get('section') for it in scene_man.get('items', [])}

    miss_chars = []
    for c in char_prompts.get('characters', []):
        if c.get('have_image') or c.get('section') in gen_seqs:
            continue
        miss_chars.append('%d %s' % (c.get('section'), c.get('name')))
    miss_scenes = []
    for s in scene_prompts.get('scenes', []):
        if s.get('have_image') or s.get('section') in gen_scene_seqs:
            continue
        miss_scenes.append('%d %s' % (s.get('section'), s.get('name')))
    miss = assets_cfg.get('missing_assets', {}) or {}

    # 视频批次
    batches = []
    out_root = VP / 'output'
    if out_root.is_dir():
        for d in sorted(out_root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            s = read_json(d / 'summary.json')
            if s:
                batches.append({'batch': d.name, 'totals': s.get('totals', {}),
                                'usage': s.get('usage', {}),
                                'elapsed': s.get('elapsed_seconds'),
                                'fuse': s.get('fuse', {})})

    # 台账聚合
    led = {'files': 0, 'success': 0, 'failed': 0, 'rh_coins': 0.0, 'wallet_usd': 0.0}
    lr = VP / 'ledger' / 'runs'
    if lr.is_dir():
        for f in lr.glob('*.json'):
            d = read_json(f, {}) or {}
            for a in d.get('attempts', []):
                led['files'] += 0
                if a.get('status') == 'SUCCESS':
                    led['success'] += 1
                elif a.get('status') == 'FAILED':
                    led['failed'] += 1
                u = a.get('usage') or {}
                try:
                    led['rh_coins'] += float(u.get('consumeCoins') or 0)
                except (TypeError, ValueError):
                    pass
                try:
                    led['wallet_usd'] += float(u.get('thirdPartyConsumeMoney') or 0)
                except (TypeError, ValueError):
                    pass
        led['files'] = len(list(lr.glob('*.json')))

    # 节点映射就绪度（G2）
    h3n = (local.get('h3') or {}).get('nodes') or {}
    slots = (h3n.get('char_ref') or {}).get('slots') or []

    return {
        'data': {'shots': len(shots), 'prompts': len(prompts)},
        'refs': refs,
        'assets': {'char_generated': sorted(gen_chars), 'scene_generated': sorted(gen_seqs),
                   'missing_characters': miss_chars, 'missing_scenes': miss_scenes},
        'h3': {'kind': (local.get('h3') or {}).get('kind'),
               'id': (local.get('h3') or {}).get('id'),
               'prompt_node': bool((h3n.get('prompt') or {}).get('nodeId')),
               'ref_slots': len([s for s in slots if s.get('nodeId')]),
               'ratio_node': bool((h3n.get('ratio') or {}).get('nodeId')),
               'duration_node': bool((h3n.get('duration') or {}).get('nodeId')),
               'ready': bool((h3n.get('prompt') or {}).get('nodeId') and slots)},
        'batches': batches[:5],
        'ledger': led,
        'keys': {'consumer': has('RUNNINGHUB_API_KEY'), 'enterprise': has('RUNNINGHUB_ENT_API_KEY')},
    }


def estimate_video(limit=None, only=None) -> dict:
    """按 H3 工作流的帧数公式 + 两点拟合成本，估算将提交段数与花费。"""
    shots = read_json(VP / 'data' / 'shots.json', []) or []
    prompts = read_json(VP / 'data' / 'h3_prompts.json', {}) or {}
    sel = shots
    if only:
        want = {o.strip() for o in only}
        sel = [s for s in sel if s.get('shot_id') in want]
    if limit:
        sel = sel[:limit]
    total_rh = 0.0
    for s in sel:
        e = prompts.get(s.get('shot_id')) or {}
        d = e.get('duration') if isinstance(e, dict) else None
        try:
            d = float(d)
        except (TypeError, ValueError):
            d = 12.0
        n = max(5, round(d * 24))
        frames = n + (5 - (n % 17)) % 17
        total_rh += 29.24 + 0.3529 * frames
    return {'segments': len(sel), 'rh_coins': round(total_rh), 'cny': round(total_rh * CNY_PER_RH, 2),
            '_caveat': '成本公式为 2 点构造性拟合（未独立验证），仅供闸门参考；真实消耗以 usage 为准'}


def cmd_status(args) -> int:
    st = collect_status()
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0
    d, a, h3, led = st['data'], st['assets'], st['h3'], st['ledger']
    print('=' * 74)
    print('短剧生产线状态面板')
    print('=' * 74)
    print('数据        : 分镜 %d 段 | H3 提示词 %d 条' % (d['shots'], d['prompts']))
    ok = [r for r in st['refs'] if r['exists']]
    over = [r for r in st['refs'] if r['h3_over_limit']]
    print('参考图      : %d/%d 张在位%s' % (
        len(ok), len(st['refs']),
        '；★ %d 张长边 >5760px 超 H3 上限，需跑 refs' % len(over) if over else '；尺寸均在上限内'))
    print('H3 映射     : kind=%s id=%s | 提示词节点=%s 参考槽位=%d 画幅=%s 时长=%s -> %s'
          % (h3['kind'], h3['id'], '有' if h3['prompt_node'] else '缺', h3['ref_slots'],
             '有' if h3['ratio_node'] else '缺', '有' if h3['duration_node'] else '缺',
             'G2 就绪' if h3['ready'] else 'G2 未就绪'))
    print('人物资产    : 已生成 %d 个 | 缺口 %d 个 %s'
          % (len(a['char_generated']), len(a['missing_characters']),
             ('（%s）' % '、'.join(a['missing_characters'][:6]) + ('…' if len(a['missing_characters']) > 6 else '')) if a['missing_characters'] else ''))
    print('场景资产    : 已生成 %d 个 | 缺口 %d 个 %s'
          % (len(a['scene_generated']), len(a['missing_scenes']),
             ('（%s）' % '、'.join(a['missing_scenes'][:5]) + ('…' if len(a['missing_scenes']) > 5 else '')) if a['missing_scenes'] else ''))
    print('Key         : 消费级-%s 企业级-%s' % (st['keys']['consumer'], st['keys']['enterprise']))
    print('台账        : %d 个任务文件 | 成功 %d / 失败 %d | 累计 %d RH币 + $%.2f 钱包'
          % (led['files'], led['success'], led['failed'], round(led['rh_coins']), led['wallet_usd']))
    if st['batches']:
        for b in st['batches']:
            t = b['totals'] or {}
            empty = not t.get('success') and not t.get('failed')
            print('批次 %-16s: 总 %s 成功 %s 失败 %s 跳过 %s 待跑 %s%s%s'
                  % (b['batch'], t.get('total'), t.get('success'), t.get('failed'),
                     t.get('skipped'), t.get('pending'),
                     '  ★熔断' if (b['fuse'] or {}).get('blown') else '',
                     '  （预演/空批次）' if empty else ''))
    else:
        print('批次        : 还没有跑过出片批次')
    print('-' * 74)
    print('下一步提示  : 缺参考图尺寸 → `refs --apply`；缺资产 → `assets`（干跑）；'
          '出片 → `video plan` 看成本')
    return 0


# ---------------------------------------------------------------------------
# preflight：一把过体检（全免费）
# ---------------------------------------------------------------------------
def _is_noise(detail: str) -> bool:
    """本线只用 --video-only，图片工作流(banana)未配置属于**已知噪音**，不该当成失败。

    实测：医生的 FAIL 有两条都属这一类 —— `provider_id`（detail 写「图片模型 [banana] id = (未填写)」）
    与 `nodes`（detail 写 image_providers[...]）。所以两类关键词都要认。
    """
    s = str(detail)
    return any(k in s for k in ('image_providers', 'banana', '图片模型'))


def cmd_preflight(args) -> int:
    verdict = []

    print('【1/4】RunningHub Key 分级（只读，不计费）')
    rc1, _, _ = run_cmd([sys.executable, SCRIPTS / 'rh_doctor.py', '--check', 'model'],
                        'preflight_rhdoctor', quiet_ok=False)
    verdict.append(('Key 分级路由', rc1 == 0, ''))

    print('\n【2/4】引擎环境体检（不联网）')
    rc2, log2, _ = run_cmd([sys.executable, RUN_PY, 'doctor', '--json'], 'preflight_doctor')
    real_fail, noise = [], 0
    try:
        doc = json.loads(pathlib.Path(log2).read_text(encoding='utf-8', errors='replace'))
        for c in doc.get('checks', []):
            if c.get('level') in ('FAIL', 'WARN'):
                if _is_noise(c.get('detail')):
                    noise += 1
                else:
                    real_fail.append(c)
    except (OSError, json.JSONDecodeError):
        real_fail = [{'id': 'doctor', 'detail': '无法解析 doctor --json（请看日志）'}]
    if noise:
        print('  （%d 项属 image_providers 未配置 —— 本线只用 --video-only，已知噪音，忽略）' % noise)
    if real_fail:
        print('  ★真正需要处理的 %d 项：' % len(real_fail))
        for c in real_fail:
            print('    - %s: %s' % (c.get('id'), str(c.get('detail'))[:110]))
    verdict.append(('引擎体检', not real_fail, '%d 项真问题' % len(real_fail) if real_fail else ''))

    print('\n【3/4】必填配置检查')
    # ★ 必须带 --video-only：本线只跑 --video-only，图片工作流(banana)确实不需要。
    #   不带的话引擎会拿「图片工作流未配置」判失败，而这里以前**只取 rc 不进 verdict**，
    #   于是体检永远显示全绿、真实出片却被配置闸门挡下（2026-09-19 实测踩到）。
    rc3, _, _ = run_cmd([sys.executable, RUN_PY, 'check', '--video-only'], 'preflight_check')
    verdict.append(('必填配置', rc3 == 0, '图片工作流不参与 --video-only 校验'))

    print('\n【4/4】预演（零网络请求，不提交任何任务）')
    rc4, _, _ = run_cmd([sys.executable, RUN_PY, '--video-only', '--dry-run', '--strict'],
                        'preflight_dryrun')
    verdict.append(('引擎预演', rc4 == 0, ''))

    print('\n' + '=' * 74)
    print('体检结论')
    print('=' * 74)
    for name, ok, extra in verdict:
        print('  %-12s %s %s' % (name, '[OK] 通过' if ok else '[NG] 需处理', extra))
    print('  日志目录：%s' % LOG_DIR)
    return 0 if all(v[1] for v in verdict) else 1


# ---------------------------------------------------------------------------
# assets：缺失资产出图（人物 → AI 应用；场景 → 闭源模型）
# ---------------------------------------------------------------------------
def cmd_assets(args) -> int:
    kinds = [args.kind] if args.kind != 'all' else ['char', 'scene']
    total_rc = 0
    if 'char' in kinds:
        cmd = [sys.executable, SCRIPTS / 'gen_char_assets.py']
        if args.only:
            cmd += ['--only', args.only]
        if args.submit:
            cmd += ['--submit']
        if args.force:
            cmd += ['--force']
        print('【人物定妆图】%s（27 RH币/张）'
              % ('★真实提交' if args.submit else '干跑，不花钱'))
        rc, _, _ = run_cmd(cmd, 'assets_char', quiet_ok=False)
        total_rc |= rc
        if rc and args.submit:
            print('人物这一路失败，按「先止损」原则不再继续场景。修好再用 --kind scene 单独跑。')
            return rc
    if 'scene' in kinds:
        cmd = [sys.executable, SCRIPTS / 'gen_scene_assets.py']
        if args.only:
            cmd += ['--only', args.only]
        if args.submit:
            cmd += ['--submit']
        if args.force:
            cmd += ['--force']
        print('\n【场景空镜】%s（$0.01/张 ≈ ￥0.07）'
              % ('★真实提交' if args.submit else '干跑，不花钱'))
        rc, _, _ = run_cmd(cmd, 'assets_scene', quiet_ok=False)
        total_rc |= rc
    print('\n产物清单：%s' % (VP / 'data' / 'char_assets_manifest.json'))
    print('          %s' % (VP / 'data' / 'scene_assets_manifest.json'))
    return total_rc


# ---------------------------------------------------------------------------
# video：计划 / 出片 / 批次进度
# ---------------------------------------------------------------------------
def _latest_batch() -> str:
    out_root = VP / 'output'
    best, best_t = None, 0.0
    if out_root.is_dir():
        for d in out_root.iterdir():
            if d.is_dir() and (d / 'summary.json').is_file():
                t = (d / 'summary.json').stat().st_mtime
                if t > best_t:
                    best, best_t = d.name, t
    return best or ''


def cmd_video_plan(args) -> int:
    est = estimate_video(args.limit, args.only.split(',') if args.only else None)
    print('出片计划（免费，不提交任何任务）')
    print('-' * 74)
    print('  将提交      : %d 段' % est['segments'])
    print('  预估消耗    : 约 %d RH币 ≈ ￥%.2f' % (est['rh_coins'], est['cny']))
    print('  公式口径    : %s' % est['_caveat'])
    only = ['--only', args.only] if args.only else []
    lim = ['--limit', str(args.limit)] if args.limit else []
    print('\n  真实跑（★计费）：')
    print('    python scripts/drama.py video run --yes %s %s'
          % (' '.join(lim), ' '.join(only)))
    print('\n  先小样更稳（1 段，约 55 RH币）：')
    print('    python scripts/drama.py video run --yes --limit 1')
    print('\n  更精确的「将提交 N / 跳过 M」口径（引擎自带；本线走 --video-only，'
          '所以「图片」那部分不会真的发生）：')
    run_cmd([sys.executable, CHECK_BILLING, '--workspace', VP] + lim + only,
            'video_check_billing', quiet_ok=False)
    print('  续跑同一批次（不重复扣费）：python scripts/drama.py video run --yes --resume <批次>')
    return 0


def cmd_video_run(args) -> int:
    if not args.yes:
        print('[闸门] 这会真实提交任务并计费。确认无误后加 --yes 重跑：')
        print('       python scripts/drama.py video run --yes%s%s'
              % (' --limit %d' % args.limit if args.limit else '',
                 ' --only %s' % args.only if args.only else ''))
        cmd_video_plan(argparse.Namespace(limit=args.limit, only=args.only))
        return 2
    est = estimate_video(args.limit, args.only.split(',') if args.only else None)
    print('★ 开始真实出片：%d 段，预估约 %d RH币 ≈ ￥%.2f' % (est['segments'], est['rh_coins'], est['cny']))
    cmd = [sys.executable, RUN_PY, '--video-only']
    if args.resume:
        cmd += ['--resume', args.resume]
    if args.limit:
        cmd += ['--limit', str(args.limit)]
    if args.only:
        cmd += ['--only', args.only]
    if args.concurrency:
        cmd += ['--concurrency', str(args.concurrency)]
    rc, log, dt = run_cmd(cmd, 'video_run', quiet_ok=False)
    print('\n引擎退出码 %s（耗时 %.1fs）；日志：%s' % (rc, dt, log))
    batch = args.resume or _latest_batch()
    if batch:
        cmd_video_status(argparse.Namespace(batch=batch))
        print('\n单条重跑（不重复扣费）：python scripts/drama.py video run --yes --resume %s --only <shot_id>'
              % batch)
    return rc


def cmd_video_status(args) -> int:
    batch = args.batch or _latest_batch()
    if not batch:
        print('还没有任何批次（output/<批次>/summary.json 不存在）')
        return 1
    s = read_json(VP / 'output' / batch / 'summary.json', {}) or {}
    t = s.get('totals', {}) or {}
    u = s.get('usage', {}) or {}
    fuse = s.get('fuse', {}) or {}
    print('=' * 74)
    print('批次 %s' % batch)
    print('=' * 74)
    print('战报   : 总 %s | 成功 %s | 失败 %s | 跳过 %s | 待跑 %s | 用时 %ss'
          % (t.get('total'), t.get('success'), t.get('failed'), t.get('skipped'),
             t.get('pending'), s.get('elapsed_seconds')))
    print('花费   : %s' % json.dumps(u, ensure_ascii=False))
    if fuse.get('blown'):
        print('★熔断  : %s' % fuse.get('reason'))
    shots = s.get('shots') or []
    # ★ 只把真失败列进「失败清单」：PENDING/待跑不是失败（预演批次里全是 PENDING，别吓人）
    bad = [x for x in shots
           if any(k in str(x.get('video_status') or '').upper() for k in ('FAIL', 'CANCEL', 'ERROR'))]
    if not t.get('success') and not t.get('failed'):
        print('说明   : 这是预演/空批次（没有成功也没有失败），101 段都还没真跑。')
    if bad:
        print('失败清单：')
        for x in bad[:20]:
            print('  %-16s %-9s %s' % (x.get('shot_id'), x.get('video_status'),
                                       str(x.get('error'))[:70]))
    d = VP / 'output' / batch
    finals = list(d.glob('*/final.mp4'))
    print('产物   : %s（final.mp4 %d 个）' % (d, len(finals)))
    if finals:
        print('         例：%s' % finals[0])
    return 0


# ---------------------------------------------------------------------------
# report / balance / refs / guide
# ---------------------------------------------------------------------------
def cmd_report(args) -> int:
    batch = args.batch or _latest_batch()
    if batch:
        cmd_video_status(argparse.Namespace(batch=batch))
        print()
    print('RunningHub 任务台账（自动记录，失败也记）')
    print('-' * 74)
    lr = VP / 'ledger' / 'runs'
    rows = []
    if lr.is_dir():
        for f in sorted(lr.glob('*.json')):
            d = read_json(f, {}) or {}
            for a in d.get('attempts', []):
                rows.append((d.get('shot_id'), a.get('status'), a.get('usage') or {},
                             a.get('artifact'), a.get('error')))
    if not rows:
        print('（台账为空）')
    else:
        for sid, status, u, art, err in rows[-40:]:
            cost = u.get('consumeCoins') and ('%s RH币' % u['consumeCoins']) or \
                   (u.get('thirdPartyConsumeMoney') and ('$%s' % u['thirdPartyConsumeMoney'])) or '—'
            print('  %-12s %-8s %-12s %s' % (sid, status, cost,
                                             (art or err or '')[:60].replace('\n', ' ')))
        print('\n合计：%d 条 attempt' % len(rows))
    print('\n完整成本报表（含本地 agent token，DSH 账本）：')
    print('  python scripts/cost_report.py')
    return 0


def cmd_balance(args) -> int:
    rc, log, _ = run_cmd([sys.executable, SCRIPTS / 'rh_doctor.py', '--live'], 'balance')
    return rc


def cmd_refs(args) -> int:
    cmd = [sys.executable, SCRIPTS / 'prepare_refs.py']
    if args.apply:
        cmd += ['--apply']
    if args.write_config:
        cmd += ['--write-config']
    print('参考图瘦身（6000px 超 H3 上限 5760）：%s' % ('★真写盘' if args.apply else '只体检，不写盘'))
    rc, _, _ = run_cmd(cmd, 'refs', quiet_ok=False)
    if not args.apply:
        print('\n确认无误后：python scripts/drama.py refs --apply --write-config')
    return rc


GUIDE = """短剧生产线 · 速查（python scripts/drama.py <命令>）
════════════════════════════════════════════════════════════════════════
看状态   status              全盘面板：数据/参考图/资产缺口/批次/台账（零网络，先跑这个）
体检     preflight           一次过 Key 分级 + 引擎 doctor + check + 预演（全免费）
资产     assets              缺人物/场景就出图：默认干跑，--submit 才计费
         assets --kind char --submit --only 沈二叔,沈老太爷
参考图   refs                瘦身体检；refs --apply --write-config 才写盘+改配置
出片     video plan          计划与成本预估（免费）；带 --limit/--only 可缩小范围
         video run --yes     真跑出片（★计费）；--limit 1 先出小样
         video status [批次] 某批次进度与失败清单
         video run --yes --resume <批次> --only <shot_id>   单条重跑（不重复扣费）
战报     report              批次汇总 + 台账（含每笔消耗与产物路径）
余额     balance             两把 Key 的余额（只读，不计费）
成本台账 cost                单次任务 + 全剧累计（RH币/美元钱包/token 三池表格）
         cost --checkpoint   ★ 收工结账：打印台账 + 记基线，下次即可看「单次任务增量」
════════════════════════════════════════════════════════════════════════
铁律：默认不花钱（要 --submit / --yes）；中断后一律用 --resume 续跑，不重新提交。
成本   ：人物 17-23 RH币/张 | 场景 $0.01/张 | 视频实测 78-209 RH币/段（与时长无关）
口径   ：消耗只认平台按任务返回的 usage —— **不用余额差值倒推消耗**
日志   ：video-pipeline/output/logs/   台账：video-pipeline/ledger/runs/
成本表 ：docs/成本台账.md（由 cost 命令生成）
"""


def cmd_cost(args) -> int:
    """成本台账：转发给 cost_ledger.py（单次任务 + 全剧累计 + 三池表格 + Excel）。

    ★ 口径：消耗只取平台按任务返回的 usage；**不用余额差值倒推消耗**（用户 2026-09-19 明确）。
    ★ 默认同时产出 Excel（docs\\成本台账_<剧名>.xlsx）与 markdown（docs\\成本台账.md）。
    """
    cmd = [sys.executable, SCRIPTS / 'cost_ledger.py']
    for flag, on in (('--checkpoint', args.checkpoint), ('--json', args.json),
                     ('--balance', args.balance), ('--no-write', args.no_write)):
        if on:
            cmd.append(flag)
    if not args.no_xlsx:
        cmd.append('--xlsx')
    if args.xlsx_only:
        cmd.append('--no-md')
    if args.md:
        cmd += ['--md', args.md]
    if args.drama:
        cmd += ['--drama', args.drama]
    print('【成本台账】单次任务 + 全剧累计（口径：只认平台 usage，不用余额倒推）')
    rc, _, _ = run_cmd(cmd, 'cost_ledger', quiet_ok=False)
    return rc


def cmd_guide(args) -> int:
    print(GUIDE)
    return 0


def main() -> int:
    _harden_stdio()
    hydrate_env()
    ap = argparse.ArgumentParser(description='短剧生产线的零思考入口（默认不花钱）')
    sub = ap.add_subparsers(dest='cmd')

    p = sub.add_parser('status', help='全盘状态面板')
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser('preflight', help='一次过体检（免费）')
    p.set_defaults(fn=cmd_preflight)

    p = sub.add_parser('assets', help='缺失资产出图（默认干跑）')
    p.add_argument('--submit', action='store_true', help='★真的生成（人物 27 RH币/张，场景 $0.01/张）')
    p.add_argument('--kind', choices=('char', 'scene', 'all'), default='all')
    p.add_argument('--only', default=None, help='只做这些（名字或序号，逗号分隔）')
    p.add_argument('--force', action='store_true', help='已生成的也重跑（会重复计费）')
    p.set_defaults(fn=cmd_assets)

    p = sub.add_parser('refs', help='参考图瘦身')
    p.add_argument('--apply', action='store_true', help='真的写盘')
    p.add_argument('--write-config', action='store_true', help='顺带把 characters.json 指到新目录')
    p.set_defaults(fn=cmd_refs)

    pv = sub.add_parser('video', help='出片')
    vs = pv.add_subparsers(dest='vcmd')
    q = vs.add_parser('plan', help='计划与成本预估（免费）')
    q.add_argument('--limit', type=int)
    q.add_argument('--only')
    q.set_defaults(fn=cmd_video_plan)
    q = vs.add_parser('run', help='真跑出片（★计费，需 --yes）')
    q.add_argument('--yes', action='store_true', help='确认计费闸门')
    q.add_argument('--limit', type=int)
    q.add_argument('--only')
    q.add_argument('--resume', metavar='BATCH_ID')
    q.add_argument('--concurrency', type=int)
    q.set_defaults(fn=cmd_video_run)
    q = vs.add_parser('status', help='批次进度')
    q.add_argument('batch', nargs='?', default=None)
    q.set_defaults(fn=cmd_video_status)

    p = sub.add_parser('report', help='战报')
    p.add_argument('--batch', default=None)
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser('balance', help='Key 余额（只读）')
    p.set_defaults(fn=cmd_balance)

    p = sub.add_parser('cost', help='成本台账：单次任务 + 全剧累计（Excel + markdown）')
    p.add_argument('--checkpoint', action='store_true',
                   help='★ 收工结账：打印台账 + 记基线（下次即可看单次增量与 token 归属）')
    p.add_argument('--json', action='store_true', help='机器可读')
    p.add_argument('--balance', action='store_true',
                   help='附带只读余额（仅判断够不够跑，不参与消耗统计）')
    p.add_argument('--md', default=None, help='markdown 输出路径（默认 docs\\成本台账.md）')
    p.add_argument('--no-write', action='store_true', help='只打印，不写任何文件')
    p.add_argument('--no-xlsx', action='store_true', help='不生成 Excel（默认生成）')
    p.add_argument('--xlsx-only', action='store_true', help='只出 Excel，不出 markdown')
    p.add_argument('--drama', default=None, help='短剧名（决定 Excel 文件名与表标题）')
    p.set_defaults(fn=cmd_cost)

    p = sub.add_parser('guide', help='打印速查')
    p.set_defaults(fn=cmd_guide)

    args = ap.parse_args()
    if not getattr(args, 'fn', None):
        print(GUIDE)
        return 0
    return args.fn(args)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n已中断。出片用 --resume 续跑；资产脚本已完成的不会重跑。')
        sys.exit(130)
