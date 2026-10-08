#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""gen_char_assets.py —— 用 RunningHub AI 应用生成「人物定妆图」资产（默认 dry-run）

背景与实测事实（2026-09-19）
----------------------------
`video-pipeline/config.assets.json` 的 `character_app` 指定：
缺人物资产 → RH AI 应用（消费级 Key），应用 ID 用环境变量 `RH_CHAR_APP_ID` 注入。

用只读接口实测（`scripts/probe_char_app.py`，未计费）该 AI 应用的输入表：

    node 33  fieldName=text  fieldType=STRING  multiline=true   ← **唯一**可填节点

⇒ 这个应用是「**一段提示词 → 一张定妆图**」，**不需要上传任何参考图**
  （所以 §「上传接口只有 .ai 站可用」那个坑在这条链路上不出现）。

提示词真源
----------
`DRAMA_ASSET_MD` 环境变量指向的「资产提示词 md」（每部剧一份）
- 「一、角色资产库」下每个 `## N. 角色名（…）` 一节 = 一个角色，节内 ``` 代码块 = 一条提示词
- 第 1-9 节已有成图（`spec/人物/1. 陆鸣.png` … `9. 陆大伯.png` → `char_01`…`char_09`）
- 第 10 节起为**缺口**：10 大伯母 / 11 陆浩 / 12 沈清瑶 / 13 沈家老管家 / 14 沈二叔 /
  15 沈天宇 / 16 张法务 / 17 张警官 / 18 沈老太爷 / 19 周神医 / 20 黑狐 / 21 抬棺四人组

★ 编号口径（重要，避免撞号）
  本工具**一律用 md 节号**命名产物 `char_<节号>_<名字>.png`，与既有 char_01..char_09 同口径。
  注意《第04-10集_资产补充清单.md》用的是另一套 C 号（C10=沈清瑶），**两套不通用**：
  补充清单 C10 沈清瑶 ↔ md 第 12 节；md 第 10 节是「大伯母」。产物清单里两者都记，见 manifest。

计费纪律
--------
默认 dry-run，只打印将提交的内容与目标文件；**必须显式 --submit 才真的提交（会计费）**。
中断后用 `--resume-task <taskId>` 只轮询、不重复提交（不重复扣费）。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pathlib
import re
import sys
import time

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]   # .../Drama-agent
ENGINE = pathlib.Path(os.environ.get('AI_VIDEO_PIPELINE_SKILL')
                      or _PROJECT_ROOT / 'stages' / 'video' / 'workflow') / 'scripts' / 'engine'
sys.path.insert(0, str(ENGINE))
# ★ 工作区解析（env DRAMA_WORKSPACE > 脚本位置推断 > 报错）；用 append 以免抢在 ENGINE 之前
sys.path.append(str(pathlib.Path(__file__).resolve().parent))
os.environ.setdefault('PYTHONUTF8', '1')

import config as C                                                   # noqa: E402
from runninghub_client import RunningHubClient, RunningHubError      # noqa: E402
from run_ledger import RunLedger                                     # noqa: E402

from _ws import WS                                                   # noqa: E402
VP = WS / 'video-pipeline'
ASSET_MD = pathlib.Path(os.environ.get('DRAMA_ASSET_MD') or '')
if not str(ASSET_MD):
    raise SystemExit('[FAIL] 未设置环境变量 DRAMA_ASSET_MD（指向「资产提示词 md」：按项目组织的角色/场景提示词真源）')

# 人物定妆图 AI 应用 ID：账号绑定资源，从环境变量注入（占位历史值见 VERSIONS.md 说明）
CHAR_APP_ID = os.environ.get('RH_CHAR_APP_ID') or ''
NODE_TEXT = '33'                             # 实测唯一可填节点（fieldName=text）
OUT_DIR = VP / 'output' / 'assets' / 'characters'
MANIFEST = VP / 'data' / 'char_assets_manifest.json'
PROMPTS_JSON = VP / 'data' / 'char_prompts.json'
# ★ 2026-09-20：批次 ID 加时间戳，让「每一次执行」在成本台账里各占一行。
#   原先是固定名 'char_assets'，跨多次调用会共用同一个批次，台账里只能看到「累计跨度」，
#   分不清哪次执行花了多少（用户 2026-09-20 要求每次执行都要能单独核算）。
#   历史记录不受影响：老文件仍叫 char_assets__<key>.json，新文件叫 char_assets_<时间戳>__<key>.json。
BATCH_ID = 'char_assets_' + time.strftime('%Y%m%d_%H%M%S')

# 已有成图的角色（md 节号）→ 不再生成；可用 RH_CHAR_HAVE_SECTIONS=1,2,3 覆盖
HAVE = {int(x) for x in os.environ.get('RH_CHAR_HAVE_SECTIONS', '1,2,3,4,5,6,7,8,9').split(',') if x.strip()}

# 补充清单的 C 号（两套编号并存时用于交叉引用；缺失的按名单补）
SUPPLEMENT_CID = {
    '陆族长': 'C08', '陆大伯': 'C09', '沈清瑶': 'C10', '沈二叔': 'C11', '沈老太爷': 'C12',
    '大伯母': 'C13', '陆浩': 'C14', '沈家老管家': 'C15', '周神医': 'C16', '黑狐': 'C17',
}

TERMINAL_OK = {'SUCCESS'}
TERMINAL_BAD = {'FAILED', 'CANCEL', 'CANCELLED'}


def supplement_cid(name: str) -> str:
    """按补充清单反查 C 号（名字容错：'堂弟陆浩' / '黑狐/暗影刺客' 也要能命中）。"""
    if name in SUPPLEMENT_CID:
        return SUPPLEMENT_CID[name]
    for k, v in SUPPLEMENT_CID.items():
        if k in name:
            return v
    return ''


SEC_RE = re.compile(r'^##\s+(\d+)\.\s*(.+?)\s*$', re.M)
FENCE_RE = re.compile(r'```(.*?)```', re.S)


def sanitize(name: str) -> str:
    """文件名安全化：md 里存在 '黑狐 / 暗影刺客' 这类带斜杠的名字，直接用会写出非法路径。"""
    s = re.sub(r'[/\\:*?"<>|]', '_', name).strip()
    return re.sub(r'_+', '_', s).strip('_') or 'unnamed'


def base_url() -> str:
    """站点：env > <工作区>/config.local.json > 引擎默认。

    AI 站账号必须用 .ai；本应用的输入是纯文本、无上传，但站点仍保持与 config.local.json 一致，
    免得以后加「图生图」时踩同一个坑。
    """
    env = os.getenv('RUNNINGHUB_BASE_URL')
    if env:
        return env.rstrip('/')
    try:
        local = json.loads((VP / 'config.local.json').read_text(encoding='utf-8'))
        if local.get('runninghub_base_url'):
            return str(local['runninghub_base_url']).rstrip('/')
    except (OSError, json.JSONDecodeError):
        pass
    return C.RH_BASE_URL.rstrip('/')


def _size(p: pathlib.Path) -> tuple:
    """读图片像素尺寸；读不出来返回 (0, 0)，不阻断流程。"""
    try:
        from PIL import Image
        with Image.open(p) as im:
            return im.size
    except Exception:                                        # noqa: BLE001
        return (0, 0)


def _pixels(p: pathlib.Path) -> int:
    w, h = _size(p)
    return w * h


def _make_preview(src: pathlib.Path, long_edge: int = 1600) -> None:
    """生成一张小图便于快速查看（23 MB 的 6000px PNG 直接打开很慢）。失败不影响主流程。"""
    try:
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert('RGB')
            w, h = im.size
            if max(w, h) > long_edge:
                s = long_edge / max(w, h)
                im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
            dest = src.with_name(src.stem + '_preview.jpg')
            im.save(dest, 'JPEG', quality=88)
        print('      预览图  : %s' % dest)
    except Exception as e:                                   # noqa: BLE001
        print('      （预览图生成失败，忽略：%s）' % e)


def parse_characters(md_path: pathlib.Path) -> list:
    """解析资产提示词 md 的「一、角色资产库」一节的每个角色。"""
    if not md_path.is_file():
        raise SystemExit('[FAIL] 资产提示词 md 不存在: %s' % md_path)
    text = md_path.read_text(encoding='utf-8')
    # 只取「一、角色资产库」到「二、场景资产」之间
    start = text.find('# 一、角色资产库')
    end = text.find('# 二、场景资产')
    block = text[start:end if end > 0 else len(text)]

    sections = []
    matches = list(SEC_RE.finditer(block))
    for i, m in enumerate(matches):
        no = int(m.group(1))
        title = m.group(2)
        body = block[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(block)]
        # 每个 ``` 代码块 = 一条提示词；其上方最近的加粗行是变体名
        variants = []
        cursor = 0
        for fm in FENCE_RE.finditer(body):
            prompt = fm.group(1).strip()
            head = body[cursor:fm.start()]
            cursor = fm.end()
            label = '基础造型'
            label_m = re.findall(r'\*\*(.+?)\*\*', head)
            if label_m:
                label = label_m[-1].strip()
            variants.append({'label': label, 'prompt': prompt})
        name = re.split(r'[（(]', title)[0].strip().replace(' ', '')
        sections.append({'section': no, 'title': title, 'name': name, 'variants': variants})
    return sections


def pick(chars: list, only: list | None, include_have: bool) -> list:
    sel = chars
    if only:
        want = {o.strip() for o in only if o.strip()}
        sel = [c for c in sel if c['name'] in want or str(c['section']) in want]
        missing = want - {c['name'] for c in sel} - {str(c['section']) for c in sel}
        if missing:
            raise SystemExit('[FAIL] 找不到角色: %s' % ', '.join(sorted(missing)))
    if not include_have:
        sel = [c for c in sel if c['section'] not in HAVE]
    return sel


def load_manifest() -> dict:
    if MANIFEST.is_file():
        try:
            return json.loads(MANIFEST.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            pass
    return {'_note': '人物定妆图产物清单（本工具自动维护）', 'items': []}


def save_manifest(man: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding='utf-8')


def upsert_manifest(man: dict, rec: dict) -> None:
    items = man.setdefault('items', [])
    for i, it in enumerate(items):
        if it.get('section') == rec.get('section'):
            items[i] = {**it, **rec}
            return
    items.append(rec)
    items.sort(key=lambda x: x.get('section') or 0)


def emit_prompts_json(chars: list) -> None:
    PROMPTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROMPTS_JSON.write_text(json.dumps({
        '_note': '从《…第01-10集_资产提示词.md》「一、角色资产库」解析出的提示词（自动生成，勿手改）',
        '_source': str(ASSET_MD),
        'characters': [
            {'section': c['section'], 'name': c['name'], 'title': c['title'],
             'have_image': c['section'] in HAVE,
             'variants': c['variants']}
            for c in chars
        ],
    }, ensure_ascii=False, indent=2), encoding='utf-8')


async def generate(sel: list, variant_label: str, submit: bool,
                   timeout_min: float, resume_task: str | None, force: bool = False) -> int:
    ledger = RunLedger(VP, cny_per_rh_coin=C.RH_CNY_PER_COIN)
    man = load_manifest()
    done = {}
    for it in man.get('items', []):
        files = [f.get('file') if isinstance(f, dict) else f for f in (it.get('files') or [])]
        if any(f and pathlib.Path(f).is_file() for f in files):
            done[it.get('section')] = files[0]

    print('站点      : %s' % base_url())
    print('AI 应用   : %s（人物定妆图）' % CHAR_APP_ID)
    print('节点      : node %s / fieldName=text' % NODE_TEXT)
    print('提示词真源: %s' % ASSET_MD.name)
    print()

    rc = 0
    ok = fail = 0
    async with RunningHubClient(C.RUNNINGHUB_API_KEY, base_url(), C.HTTP_TIMEOUT) as client:
        for c in sel:
            no, name = c['section'], c['name']
            var = next((v for v in c['variants'] if variant_label in v['label']),
                       c['variants'][0] if c['variants'] else None)
            if not var:
                print('[SKIP] 第 %d 节 %s：md 里没有提示词代码块' % (no, name))
                continue

            # ★ 省钱闸门：已生成且产物仍在 → 默认不重复生成（重跑用 --force）
            if not force and not resume_task and no in done:
                print('[SKIP] 第 %d 节 %s：已生成过（%s）。要重跑请加 --force。'
                      % (no, name, pathlib.Path(done[no]).name))
                continue

            prompt = var['prompt']
            key = 'char_%02d' % no
            out = OUT_DIR / ('%s_%s.png' % (key, sanitize(name)))
            print('=' * 84)
            print('第 %d 节 %s   （%s）' % (no, name, var['label']))
            print('  提示词长度: %d 字' % len(prompt))
            print('  提示词摘要: %s…' % prompt[:70])
            print('  目标文件  : %s' % out)

            node_list = [{'nodeId': NODE_TEXT, 'fieldName': 'text', 'fieldValue': prompt}]
            snap = WS / 'docs' / ('char-nodeinfo-%s.json' % key)
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps({
                'webapp_id': CHAR_APP_ID, 'section': no, 'name': name,
                'variant': var['label'], 'nodeInfoList': node_list,
            }, ensure_ascii=False, indent=2), encoding='utf-8')

            if not submit and not resume_task:
                print('  ★ DRY-RUN：未提交，未计费。加 --submit 才真的生成。')
                continue

            t0 = time.time()
            task_id = resume_task
            if not task_id:
                try:
                    task_id = await client.create_ai_app(CHAR_APP_ID, node_list)
                except RunningHubError as e:
                    print('  [FAIL] 提交失败: %s' % e.short)
                    ledger.record(batch_id=BATCH_ID, shot_id=key, stage='image', status='FAILED',
                                  route='ai-app', kind='ai-app', target_id=CHAR_APP_ID,
                                  error=str(e.short))
                    continue
                print('  taskId = %s' % task_id)
                ledger.record(batch_id=BATCH_ID, shot_id=key, stage='image', status='RUNNING',
                              route='ai-app', kind='ai-app', target_id=CHAR_APP_ID,
                              task_id=task_id, extra={'name': name, 'variant': var['label'],
                                                      'prompt_sha256': hashlib.sha256(
                                                          prompt.encode('utf-8')).hexdigest()[:16]})
                print('  轮询中（中断后：--resume-task %s，只轮询不重复扣费）' % task_id)

            waited = 0
            while True:
                await asyncio.sleep(10)
                waited += 10
                try:
                    payload = await client.query_v2(task_id)
                except RunningHubError as e:
                    print('    [%4ds] 查询失败: %s' % (waited, e.short))
                    continue
                d = payload.get('data') if isinstance(payload.get('data'), dict) else payload
                status = str(d.get('status') or d.get('taskStatus') or '').upper()
                print('    [%4ds] %s' % (waited, status or '(空)'))
                if status in TERMINAL_OK:
                    usage = d.get('usage') or {}
                    results = d.get('results') or []
                    wall = round(time.time() - t0, 1)
                    print('  [SUCCESS] 墙钟 %.1fs | usage=%s'
                          % (wall, json.dumps(usage, ensure_ascii=False)))
                    saved = []
                    for i, r in enumerate(results):
                        url = r.get('url') if isinstance(r, dict) else None
                        if not url:
                            continue
                        tmp = out.with_name('%s__r%d%s'
                                            % (out.stem, i + 1, pathlib.Path(url).suffix or '.png'))
                        await client.download(url, str(tmp))
                        saved.append(tmp)

                    # ★ 命名规范化：一次调用回两张（实测 1920x1080 + 6000x3376）。
                    #   分辨率最高的那张 = 与既有 9 张定妆图同规格的资产图 → 用干净名；
                    #   其余按 r2/r3… 保留，不丢产物。
                    saved.sort(key=_pixels, reverse=True)
                    finals = []
                    for k, p in enumerate(saved):
                        dest = out if k == 0 else out.with_name('%s_r%d%s' % (out.stem, k + 1, p.suffix))
                        if p != dest:
                            if dest.exists():
                                dest.unlink()
                            p.replace(dest)
                        w, h = _size(dest)
                        finals.append({'file': str(dest), 'role': 'asset' if k == 0 else 'extra',
                                       'width': w, 'height': h,
                                       'mb': round(dest.stat().st_size / 1024 / 1024, 2)})
                        print('      已保存: %s  (%sx%s, %.2f MB)'
                              % (dest, w, h, dest.stat().st_size / 1024 / 1024))
                    _make_preview(pathlib.Path(finals[0]['file']))
                    saved = [f['file'] for f in finals]
                    if saved:
                        upsert_manifest(man, {
                            'section': no, 'name': name, 'key': key,
                            'supplement_cid': supplement_cid(name),
                            'variant': var['label'], 'files': finals,
                            'task_id': task_id,
                            'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:16],
                            'usage': usage, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                            '_note': 'files[0] 为资产图（与既有 char_01..09 同规格）；其余为同次调用的附带产物',
                        })
                        save_manifest(man)
                    ledger.record(batch_id=BATCH_ID, shot_id=key, stage='image', status='SUCCESS',
                                  route='ai-app', kind='ai-app', target_id=CHAR_APP_ID,
                                  task_id=task_id, usage=usage, wall_elapsed_s=wall,
                                  out_bytes=sum(pathlib.Path(s).stat().st_size for s in saved) or None,
                                  artifact=';'.join(saved) or None,
                                  output_url=(results[0].get('url') if results else None),
                                  extra={'name': name, 'variant': var['label']})
                    ok += 1
                    break
                if status in TERMINAL_BAD:
                    print('  [FAILED] %s' % json.dumps(d, ensure_ascii=False)[:500])
                    ledger.record(batch_id=BATCH_ID, shot_id=key, stage='image', status='FAILED',
                                  route='ai-app', kind='ai-app', target_id=CHAR_APP_ID,
                                  task_id=task_id, usage=d.get('usage') or {},
                                  wall_elapsed_s=round(time.time() - t0, 1),
                                  error=json.dumps(d, ensure_ascii=False)[:200])
                    fail += 1
                    rc = rc or 1
                    break
                if waited > timeout_min * 60:
                    print('  [超时] 已等 %.0f 分钟。任务可能仍在云端 —— 下一条命令只轮询：'
                          '--resume-task %s' % (timeout_min, task_id))
                    fail += 1
                    rc = rc or 2
                    break
    print()
    print('本轮完成：成功 %d / 失败 %d（共选中 %d 个角色）' % (ok, fail, len(sel)))
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description='人物定妆图生成（RunningHub AI 应用，默认 dry-run；应用 ID 用 RH_CHAR_APP_ID 注入）')
    ap.add_argument('--list', action='store_true', help='列出 md 里的角色与提示词条数，然后退出')
    ap.add_argument('--only', default=None, help='只处理这些角色（逗号分隔的名字或 md 节号），如 沈清瑶 或 12')
    ap.add_argument('--variant', default='基础造型', help="变体名关键字，默认「基础造型」（取第一个匹配，无则取该角色第一条）")
    ap.add_argument('--include-have', action='store_true', help='连第 1-9 节（已有成图）也一起处理')
    ap.add_argument('--force', action='store_true', help='已生成过的角色也重新生成（会重复计费）')
    ap.add_argument('--submit', action='store_true', help='★ 真的提交（每张计费一次）')
    ap.add_argument('--resume-task', default=None, help='只轮询这个 taskId（不重复提交，不重复扣费）')
    ap.add_argument('--timeout-min', type=float, default=15.0, help='单张轮询超时（分钟），默认 15')
    args = ap.parse_args()

    chars = parse_characters(ASSET_MD)
    emit_prompts_json(chars)

    if args.list:
        man = load_manifest()
        gen = {it.get('section') for it in man.get('items', [])}
        todo = [c for c in chars if c['section'] not in HAVE and c['section'] not in gen]
        print('%-4s %-12s %-8s %-8s %s' % ('节号', '角色', '有原图', '已生成', '提示词变体'))
        print('-' * 92)
        for c in chars:
            labels = ' / '.join(v['label'] for v in c['variants']) or '(无)'
            print('%-4d %-12s %-8s %-8s %s' % (c['section'], c['name'],
                                               '是' if c['section'] in HAVE else '—',
                                               '是' if c['section'] in gen else '—', labels))
        print()
        print('缺口（未生成）：%s' % ('、'.join('%d %s' % (c['section'], c['name']) for c in todo) or '无'))
        print('提示词清单：%s' % PROMPTS_JSON)
        return 0

    if not C.RUNNINGHUB_API_KEY:
        print('[FAIL] 未设置 RUNNINGHUB_API_KEY')
        return 2

    if not CHAR_APP_ID:
        print('[FAIL] 未设置环境变量 RH_CHAR_APP_ID（人物定妆图 AI 应用 ID）')
        return 2

    only = args.only.split(',') if args.only else None
    sel = pick(chars, only, args.include_have)
    if not sel:
        print('[FAIL] 没有选中任何角色')
        return 1
    return asyncio.run(generate(sel, args.variant, args.submit, args.timeout_min,
                                args.resume_task, args.force))


if __name__ == '__main__':
    sys.exit(main())
