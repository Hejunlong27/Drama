#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""gen_assets_local.py —— 用**本地 ComfyUI**生成短剧资产图（人物/场景/道具），￥0 成本

与另两条资产通道的关系
----------------------
| 通道 | 脚本 | 引擎 | 成本 |
|---|---|---|---|
| **本地 ComfyUI**（本脚本） | `gen_assets_local.py` | `stages/assets/comfyui/comfy_client.py` | **￥0**（只花本机电与时间） |
| RunningHub AI 应用 | `gen_char_assets.py` | 云端 webapp | 人物 17–27 RH币/张 |
| RunningHub 模型 API | `gen_scene_assets.py` | 云端模型端点 | 场景 ≈￥0.07/张 |

三者**并存**，由 `drama.py assets --engine {local,rh}` 切换（默认 local）。

本脚本做什么（薄桥，不做重活）
------------------------------
1. 读 `DRAMA_ASSET_MD` 指向的「资产提示词 md」（**与云端两脚本同一真源**）；
2. 调 `stages/assets/scripts/asset_md.py` 转成 ComfyUI 清单 `comfyui_manifest.json`；
3. 调 `stages/assets/comfyui/comfy_client.py` 出图到 `<工作区>/video-pipeline/output/assets/`；
4. 汇总产物写 `comfyui_assets_manifest.json`，并按**统一台账口径**补记 `ledger/runs/`（route=local、消耗为 0）。

计费纪律（本通道没有计费，但闸门照旧）
--------------------------------------
默认 **dry-run**：只打印将生成的清单，不调用 ComfyUI。必须显式 `--submit` 才真的出图。
理由不是省钱（本地不花钱），而是**别在没确认风格时批量跑掉十几分钟 GPU**。

依赖纪律：纯标准库 + `asset_md` + `run_ledger`（后者亦为纯标准库）。
**不需要 httpx、不需要 `RUNNINGHUB_*` 任何 Key。**
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]      # .../Drama-agent
ENGINE = _PROJECT_ROOT / 'stages' / 'video' / 'workflow' / 'scripts' / 'engine'
COMFY_DIR = _PROJECT_ROOT / 'stages' / 'assets' / 'comfyui'
COMFY_PY = COMFY_DIR / 'comfy_client.py'
ASSETS_SCRIPTS = _PROJECT_ROOT / 'stages' / 'assets' / 'scripts'

sys.path.insert(0, str(ENGINE))            # run_ledger（纯标准库）
sys.path.append(str(ASSETS_SCRIPTS))       # asset_md（纯标准库）
sys.path.append(str(pathlib.Path(__file__).resolve().parent))   # _ws
os.environ.setdefault('PYTHONUTF8', '1')

from run_ledger import RunLedger            # noqa: E402
import asset_md as AM                       # noqa: E402
from _ws import WS                          # noqa: E402

try:                                        # Windows 控制台 GBK → UTF-8
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')    # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')    # type: ignore[union-attr]
except Exception:                                                 # noqa: BLE001
    pass

VP = WS / 'video-pipeline'
_MD_ENV = (os.environ.get('DRAMA_ASSET_MD') or '').strip()
if not _MD_ENV:
    raise SystemExit('[FAIL] 未设置环境变量 DRAMA_ASSET_MD'
                     '（指向「资产提示词 md」：角色/场景/道具提示词真源）')
ASSET_MD = pathlib.Path(_MD_ENV)

MANIFEST = VP / 'data' / 'comfyui_manifest.json'
PROD_MANIFEST = VP / 'data' / 'comfyui_assets_manifest.json'
OUT_DIR = VP / 'output' / 'assets'
BATCH_ID = 'local_assets_' + time.strftime('%Y%m%d_%H%M%S')

SUBDIR = {'char': 'characters', 'scene': 'scenes', 'prop': 'props'}
KIND_CN = {'char': '人物', 'scene': '场景', 'prop': '道具'}
EXTS = ('.png', '.jpg', '.jpeg', '.webp')


def _size(p: pathlib.Path) -> tuple:
    """读图片像素尺寸；Pillow 不在或读不出来则返回 (0, 0)，不阻断流程。"""
    try:
        from PIL import Image
        with Image.open(p) as im:
            return im.size
    except Exception:                                            # noqa: BLE001
        return (0, 0)


def _scan_outputs(assets: list) -> list:
    """出图后扫描产物目录，按资产 id 归集实际落盘文件。"""
    items = []
    for a in assets:
        d = OUT_DIR / SUBDIR.get(a['kind'], '')
        files = []
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.suffix.lower() in EXTS and (f.name.startswith(a['prefix'] + '_')
                                                 or f.stem == a['prefix']):
                    w, h = _size(f)
                    files.append({'file': str(f), 'role': 'asset',
                                  'width': w, 'height': h,
                                  'mb': round(f.stat().st_size / 1024 / 1024, 2)})
        items.append({
            'section': a['section'], 'name': a['name'], 'key': a['id'], 'kind': a['kind'],
            'variant': a.get('variant', ''), 'channel': 'comfyui-local',
            'prompt_sha256': hashlib.sha256(a['prompt'].encode('utf-8')).hexdigest()[:16],
            'files': files, 'usage': {}, 'billed': False,
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            '_note': '本地 ComfyUI 出图产物（￥0）；files[0] 为资产图',
        })
    return items


def _write_prod_manifest(items: list, manifest: dict) -> None:
    PROD_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    ok = [it for it in items if it['files']]
    PROD_MANIFEST.write_text(json.dumps({
        '_note': '本地 ComfyUI 通道产物清单（本工具自动维护）；与云端 char_/scene_assets_manifest.json 并列',
        '_source': str(ASSET_MD),
        '_manifest': str(MANIFEST),
        'channel': 'comfyui-local',
        'produced_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'totals': {'items': len(items), 'produced': len(ok), 'cost_cny': 0.0},
        'items': items,
    }, ensure_ascii=False, indent=2), encoding='utf-8')


def cmd_check() -> int:
    print('【本地 ComfyUI 体检】%s' % COMFY_PY)
    rc = subprocess.call([sys.executable, str(COMFY_PY), '--check'])
    if rc == 0:
        print('  [OK] ComfyUI 在线，可以出图（命令：drama.py assets --engine local --submit）')
    else:
        print('  [FAIL] 连不上 ComfyUI。请先启动 ComfyUI，或设 COMFYUI_URL 指向它。')
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(
        description='本地 ComfyUI 资产出图（￥0；默认 dry-run，需 --submit 才真跑）')
    ap.add_argument('--kind', default='char,scene,prop',
                    help='生成哪些类型：char,scene,prop（逗号分隔），默认全部')
    ap.add_argument('--only', default=None, help='只生成这些资产 id（逗号分隔），如 char_01,scene_02')
    ap.add_argument('--variant', default='基础造型', help='人物取哪个变体（关键字），默认「基础造型」')
    ap.add_argument('--all-variants', action='store_true', help='每个变体各出一张')
    ap.add_argument('--aspect', default='', help='强制全部资产用这个画幅（覆盖各类型默认）')
    ap.add_argument('--aspect-char', default=None)
    ap.add_argument('--aspect-scene', default=None)
    ap.add_argument('--aspect-prop', default=None)
    ap.add_argument('--style-lock', default='', help='统一风格串，会拼进每条提示词')
    ap.add_argument('--submit', action='store_true', help='★ 真的调用 ComfyUI 出图（不花钱，但会占 GPU）')
    ap.add_argument('--force', action='store_true', help='已出图的重跑（默认跳过已有产物，断点续跑）')
    ap.add_argument('--check', action='store_true', help='只体检 ComfyUI 是否在线，然后退出')
    args = ap.parse_args()

    if args.check:
        return cmd_check()

    kinds = tuple(k.strip() for k in args.kind.split(',') if k.strip())
    unknown = [k for k in kinds if k not in AM.KINDS]
    if unknown:
        print('[FAIL] 未知类型：%s（可选 %s）' % (', '.join(unknown), '/'.join(AM.KINDS)))
        return 2

    entries = AM.parse_md(ASSET_MD, kinds)
    if not entries:
        print('[FAIL] 在 %s 里没解析到任何资产节（章节标题需为「一、角色资产库」等，节内含 ``` 提示词块）。'
              % ASSET_MD.name)
        return 2

    aspects = dict(AM.ASPECT_DEFAULT)
    for k in AM.KINDS:
        v = getattr(args, 'aspect_%s' % k)
        if v:
            aspects[k] = v
    if args.aspect:
        aspects = {k: args.aspect for k in AM.KINDS}

    assets = AM.to_assets(entries, args.variant, args.all_variants, aspects)
    if args.only:
        want = {x.strip() for x in args.only.split(',') if x.strip()}
        assets = [a for a in assets if a['id'] in want]
        missing = want - {a['id'] for a in assets}
        if missing:
            print('[FAIL] 找不到资产 id：%s' % ', '.join(sorted(missing)))
            return 2
    if not assets:
        print('[FAIL] 没有选中任何资产')
        return 1

    manifest = AM.build_manifest(ASSET_MD, assets, args.style_lock, args.aspect)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    print('=' * 84)
    print('本地 ComfyUI 资产出图（￥0 成本）')
    print('=' * 84)
    print('提示词真源: %s' % ASSET_MD)
    print('清单      : %s' % MANIFEST)
    print('输出目录  : %s' % OUT_DIR)
    print('资产数    : %d（%s）'
          % (len(assets), '、'.join('%s %d' % (KIND_CN[k], sum(1 for a in assets if a['kind'] == k))
                                   for k in AM.KINDS if any(a['kind'] == k for a in assets))))
    print('成本      : ￥0（本机 GPU 出图，不产生任何平台费用）')
    print('-' * 84)
    for a in assets:
        print('  %-18s %-4s %-14s %-24s %s'
              % (a['id'], KIND_CN[a['kind']], a['name'], a['aspect'] or '—',
                 a['prompt'][:26] + '…'))

    if not args.submit:
        print()
        print('★ DRY-RUN：未调用 ComfyUI，未占用 GPU。')
        print('  确认清单无误后加 --submit 出图：')
        print('    python stages/production/scripts/drama.py assets --engine local --submit')
        print('  建议先只出一张看风格：加 --only %s' % assets[0]['id'])
        return 0

    cmd = [sys.executable, str(COMFY_PY), '--spec', str(MANIFEST), '--out', str(OUT_DIR)]
    if not args.force:
        cmd.append('--skip-existing')
    print()
    print('  $ %s' % ' '.join(cmd[1:]))
    t0 = time.time()
    rc = subprocess.call(cmd)
    wall = round(time.time() - t0, 1)

    items = _scan_outputs(assets)
    _write_prod_manifest(items, manifest)
    produced = [it for it in items if it['files']]

    ledger = RunLedger(VP, enabled=True)
    for it in items:
        got = bool(it['files'])
        nbytes = sum(pathlib.Path(f['file']).stat().st_size for f in it['files']) or None
        ledger.record(batch_id=BATCH_ID, shot_id=it['key'], stage='image',
                      status='SUCCESS' if got else 'FAILED',
                      route='local', kind='comfyui', target_id='comfyui',
                      usage={}, wall_elapsed_s=None if got else wall,
                      out_bytes=nbytes,
                      artifact=';'.join(f['file'] for f in it['files']) or None,
                      error=None if got else 'no output produced',
                      extra={'channel': 'comfyui-local', 'name': it['name'],
                             'variant': it.get('variant', ''), 'billed': False,
                             'prompt_sha256': it['prompt_sha256']})

    print()
    print('=' * 84)
    print('完成：出图 %d / 共 %d（墙钟 %.1fs，费用 ￥0）' % (len(produced), len(items), wall))
    print('产物清单：%s' % PROD_MANIFEST)
    print('台账    ：%s' % (VP / 'ledger' / 'runs'))
    if len(produced) < len(items):
        print('★ 有 %d 项无产物（ComfyUI 未启动 / 节点报错 / 显存不足）。'
              '修好后重跑同一条命令即可断点续跑。' % (len(items) - len(produced)))
    return 0 if rc == 0 else rc


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n已中断。重跑同一命令即可断点续跑（默认跳过已有产物）。')
        sys.exit(130)
