#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""gen_scene_assets.py —— 生成「场景资产图（空镜）」资产（默认 dry-run）

路线（用户 2026-09-18 拍板，见 video-pipeline/config.assets.json 的 `scene_model`）
--------------------------------------------------------------------------------
闭源模型 API，不是工作流、也不是 AI 应用：
    endpoint  rhart-image-n-g31-flash-lite/text-to-image   （全能图片V2-lite-文生图-低价渠道版，¥0.07/次）
    base      {base}/openapi/v2/<endpoint>
    auth      Authorization: Bearer <key>
    body      {"prompt": "...", "aspectRatio": "16:9"}

★ 两个必须记住的硬约束
1. **Key 分级**：官方口径「模型 API / LLM API **只支持企业级-共享 Key**」，
   消费级 Key 调用**一定失败** —— 所以本脚本默认**只认** `RUNNINGHUB_ENT_API_KEY`；
   要用消费级 Key 试探这条链路是否真的被拦（验证 Key 分级结论，失败不计费），
   必须显式加 `--allow-consumer-key`。
2. **画幅默认值是竖屏**：低价渠道版 `aspectRatio` 默认 `9:16`，而本剧全部是 **16:9**。
   脚本**永远显式发送 `16:9`**，不给"用默认值"的机会（config.assets.json 里记过这个坑）。

其它实测到的 schema 事实（来自官方 developer-kit `model-registry.public.json`）
    lite/text-to-image      : prompt(必填) + aspectRatio  —— **无 negativePrompt、无 resolution 字段**
    官方稳定版 / 非 lite 版   : 多一个 resolution(必填 1k/2k/4k)，单价随分辨率 ¥0.49/0.74/0.99
⇒ 因为 lite 版**没有负向提示词字段**，md 里那句「负面提示词：人物，人，人脸」默认会被**剥掉**
  （"空镜无人"本身已写在正文里），要用 `--keep-negative` 才原样发送。

提示词真源
----------
`DRAMA_ASSET_MD` 环境变量指向的「资产提示词 md」（每部剧一份）
「二、场景资产」下每个 `## 场景 N · 名称…` 一节 = 一个场景（含胶片/摄影风格标注），节内 ``` 块 = 提示词。
场景 1（金碧辉煌酒楼大包厢）已有成图（= `characters.json` 的 `scene_01`，2560×1440，即梦出品）；
缺口 = 场景 2-7，与 `config.assets.json` 的 missing_assets.scenes（S2-S7）一一对应。

★ 计费纪律：默认 dry-run；必须显式 --submit 才提交（¥0.07/次）。
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

import config as C                                                    # noqa: E402
from runninghub_client import RunningHubClient, RunningHubError       # noqa: E402
from run_ledger import RunLedger                                      # noqa: E402

from _ws import WS                                                    # noqa: E402
VP = WS / 'video-pipeline'
ASSET_MD = pathlib.Path(os.environ.get('DRAMA_ASSET_MD') or '')
if not str(ASSET_MD):
    raise SystemExit('[FAIL] 未设置环境变量 DRAMA_ASSET_MD（指向「资产提示词 md」：按项目组织的角色/场景提示词真源）')

OUT_DIR = VP / 'output' / 'assets' / 'scenes'
MANIFEST = VP / 'data' / 'scene_assets_manifest.json'
PROMPTS_JSON = VP / 'data' / 'scene_prompts.json'
# ★ 2026-09-20：批次 ID 加时间戳，让「每一次执行」在成本台账里各占一行。
#   原先是固定名 'scene_assets'，跨多次调用共用同一个批次（实测 09-18 与 09-19 两次混在一起），
#   台账只能看到「累计跨度」，分不清哪次执行花了多少（用户 2026-09-20 要求每次执行单独核算）。
BATCH_ID = 'scene_assets_' + time.strftime('%Y%m%d_%H%M%S')

# 已有成图的场景（md 节号）→ 不再生成；可用 RH_SCENE_HAVE_SECTIONS=1 覆盖
HAVE = {int(x) for x in os.environ.get('RH_SCENE_HAVE_SECTIONS', '1').split(',') if x.strip()}

# 已实测 / 官方 registry 的 endpoint 与单价（用于 dry-run 报价；单价来源 pricing.public.json）
ENDPOINTS = {
    'lite-t2i':     ('rhart-image-n-g31-flash-lite/text-to-image', '¥0.07/次', '低价渠道版·文生图（无负向词字段）'),
    'lite-i2i':     ('rhart-image-n-g31-flash-lite/image-to-image', '¥0.07/次', '低价渠道版·图生图（要 --ref 参考图）'),
    'official-t2i': ('rhart-image-n-g31-flash-official/text-to-image', '¥0.49/0.74/0.99（按 1k/2k/4k）', '官方稳定版·文生图'),
    'official-i2i': ('rhart-image-n-g31-flash-official/image-to-image', '¥0.49/0.74/0.99（按 1k/2k/4k）', '官方稳定版·图生图'),
}

TERMINAL_OK = {'SUCCESS'}
TERMINAL_BAD = {'FAILED', 'CANCEL', 'CANCELLED'}


def base_url() -> str:
    """站点：env > <工作区>/config.local.json > 引擎默认。模型 API 的 host（.cn/.ai）官方未明说，
    config.assets.json 里记着「host 未确认」。本账号在 .ai，且 .ai 是唯一上传可用的站，故默认取 .ai。"""
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


def _num(v):
    """宽松取数：拿不到就返回 None（不臆造 0）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def sanitize(name: str) -> str:
    s = re.sub(r'[/\\:*?"<>|]', '_', name).strip()
    return re.sub(r'_+', '_', s).strip('_') or 'unnamed'


def strip_negative(prompt: str) -> tuple:
    """剥掉 md 里那句「负面提示词：…」—— lite 版没有负向词字段。
    返回 (清理后的提示词, 被剥掉的原文 或 '')。"""
    m = re.search(r'[，,。\s]*负面提示词[：:]\s*(.+?)\s*[。.]?\s*$', prompt)
    if not m:
        return prompt.strip(), ''
    return prompt[:m.start()].strip().rstrip('，,。.'), m.group(1).strip()


SEC_RE = re.compile(r'^##\s+场景\s*(\d+)\s*[·.]\s*(.+?)\s*$', re.M)
FENCE_RE = re.compile(r'```(.*?)```', re.S)
STYLE_RE = re.compile(r'\*\*胶片[：:]\s*(.+?)\*\*')


def parse_scenes(md_path: pathlib.Path) -> list:
    if not md_path.is_file():
        raise SystemExit('[FAIL] 资产提示词 md 不存在: %s' % md_path)
    text = md_path.read_text(encoding='utf-8')
    start = text.find('# 二、场景资产')
    end = text.find('# 三、道具资产')
    block = text[start:end if end > 0 else len(text)]

    out = []
    ms = list(SEC_RE.finditer(block))
    for i, m in enumerate(ms):
        no = int(m.group(1))
        title = m.group(2)
        body = block[m.end(): ms[i + 1].start() if i + 1 < len(ms) else len(block)]
        fm = FENCE_RE.search(body)
        if not fm:
            continue
        style = STYLE_RE.search(body)
        out.append({
            'section': no,
            'title': title,
            'name': sanitize(re.split(r'[（(｜|]', title)[0].strip()),
            'style': style.group(1).strip() if style else '',
            'prompt': fm.group(1).strip(),
        })
    return out


def pick(scenes: list, only: list | None, include_have: bool) -> list:
    sel = scenes
    if only:
        want = {o.strip() for o in only if o.strip()}
        sel = [s for s in sel if s['name'] in want or str(s['section']) in want]
        missing = want - {s['name'] for s in sel} - {str(s['section']) for s in sel}
        if missing:
            raise SystemExit('[FAIL] 找不到场景: %s' % ', '.join(sorted(missing)))
    if not include_have:
        sel = [s for s in sel if s['section'] not in HAVE]
    return sel


def load_manifest() -> dict:
    if MANIFEST.is_file():
        try:
            return json.loads(MANIFEST.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            pass
    return {'_note': '场景资产（空镜）产物清单（本工具自动维护）', 'items': []}


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


def emit_prompts_json(scenes: list) -> None:
    PROMPTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROMPTS_JSON.write_text(json.dumps({
        '_note': '从《…第01-10集_资产提示词.md》「二、场景资产」解析出的提示词（自动生成，勿手改）',
        '_source': str(ASSET_MD),
        'scenes': [{**s, 'have_image': s['section'] in HAVE} for s in scenes],
    }, ensure_ascii=False, indent=2), encoding='utf-8')


def _size(p: pathlib.Path) -> tuple:
    try:
        from PIL import Image
        with Image.open(p) as im:
            return im.size
    except Exception:                                                 # noqa: BLE001
        return (0, 0)


def _make_preview(src: pathlib.Path, long_edge: int = 1600) -> None:
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
    except Exception as e:                                            # noqa: BLE001
        print('      （预览图生成失败，忽略：%s）' % e)


async def generate(sel: list, endpoint_key: str, aspect: str, keep_negative: bool,
                   ref_image: str | None, submit: bool, timeout_min: float,
                   allow_consumer_key: bool, force: bool) -> int:
    endpoint, price_hint, desc = ENDPOINTS[endpoint_key]
    is_i2i = 'image-to-image' in endpoint
    key = os.getenv('RUNNINGHUB_ENT_API_KEY', '').strip()
    key_src = 'RUNNINGHUB_ENT_API_KEY（企业级-共享）'
    if not key:
        if not allow_consumer_key:
            print('[BLOCK] 未设置 RUNNINGHUB_ENT_API_KEY。')
            print('        官方口径：模型 API / LLM API **只支持企业级-共享 Key**，消费级 Key 调用一定失败。')
            print('        想验证这条结论（失败不计费）或确实要跑，请二选一：')
            print('          ① 配好企业级 Key：setx RUNNINGHUB_ENT_API_KEY "你的key"')
            print('          ② 用消费级 Key 试探：加 --allow-consumer-key（预期被拒，被拒不计费）')
            return 2
        key = C.RUNNINGHUB_API_KEY
        key_src = 'RUNNINGHUB_API_KEY（消费级-会员）★ 预期被拒'
    if is_i2i and not ref_image:
        print('[BLOCK] 图生图端点必须给参考图：--ref <本地图片路径>')
        return 2

    ledger = RunLedger(VP, cny_per_rh_coin=C.RH_CNY_PER_COIN)
    man = load_manifest()
    done = {}
    for it in man.get('items', []):
        files = [f.get('file') if isinstance(f, dict) else f for f in (it.get('files') or [])]
        if any(f and pathlib.Path(f).is_file() for f in files):
            done[it.get('section')] = files[0]

    print('站点      : %s' % base_url())
    print('端点      : %s   （%s，%s）' % (endpoint, desc, price_hint))
    print('Key       : %s' % key_src)
    print('画幅      : %s（★ 显式发送，低价渠道版默认是竖屏 9:16）' % aspect)
    print('提示词真源: %s' % ASSET_MD.name)
    print()

    async with RunningHubClient(key, base_url(), C.HTTP_TIMEOUT) as client:
        ref_file_name = None
        if is_i2i and submit:
            rp = pathlib.Path(ref_image)
            if not rp.is_file():
                print('[FAIL] 参考图不存在: %s' % rp)
                return 1
            print('上传参考图: %s' % rp.name)
            up = await client.upload_file(str(rp))
            ref_file_name = up.get('fileName') or up.get('download_url')
            print('  [OK] %s' % ref_file_name)
            print()

        rc = 0
        ok = fail = 0
        for s in sel:
            no, name = s['section'], s['name']
            key_id = 'scene_%02d' % no
            out = OUT_DIR / ('%s_%s.png' % (key_id, name))
            prompt_raw = s['prompt']
            if keep_negative:
                prompt = prompt_raw
                dropped = ''
            else:
                prompt, dropped = strip_negative(prompt_raw)

            print('=' * 84)
            print('场景 %d  %s' % (no, s['title']))
            if s['style']:
                print('  风格标注  : %s' % s['style'])
            print('  提示词长度: %d 字' % len(prompt))
            print('  提示词摘要: %s…' % prompt[:70])
            if dropped:
                print('  已剥负面词: %s（lite 版无负向词字段；--keep-negative 可保留）' % dropped)
            print('  目标文件  : %s' % out)

            if not force and no in done:
                print('[SKIP] 已生成过（%s）。要重跑请加 --force。' % pathlib.Path(done[no]).name)
                continue

            body = {'prompt': prompt, 'aspectRatio': aspect}
            if is_i2i and ref_file_name:
                body['imageUrls'] = [ref_file_name]

            snap = WS / 'docs' / ('scene-nodeinfo-%s.json' % key_id)
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps({
                'endpoint': endpoint, 'base_url': base_url(), 'section': no, 'name': name,
                'body': body, 'body_note': 'model API 的请求体（Authorization: Bearer <key>）',
            }, ensure_ascii=False, indent=2), encoding='utf-8')

            if not submit:
                print('  ★ DRY-RUN：未提交，未计费。加 --submit 才真的生成。')
                continue

            t0 = time.time()
            try:
                payload = await client._request(              # noqa: SLF001 协议层
                    'POST', '/openapi/v2/%s' % endpoint, json_body=body, context='提交模型任务')
                client._raise_business(payload, '提交模型任务')  # noqa: SLF001
            except RunningHubError as e:
                print('  [FAIL] 提交被拒: %s' % e.short)
                ledger.record(batch_id=BATCH_ID, shot_id=key_id, stage='image', status='FAILED',
                              route='model-api', kind='model', target_id=endpoint,
                              error=str(e.short))
                print()
                print('  ⇒ 这一次**未计费**（提交前即失败）。若错误信息指向 Key 类型/权限，')
                print('    即坐实「模型 API 只认企业级-共享 Key」这条官方口径。')
                return 1

            task_id = str((payload.get('data') or {}).get('taskId')
                          or payload.get('taskId') or (payload.get('data') or {}).get('task_id') or '')
            if not task_id:
                # 业务级拒绝（code 字段为空、错误放在 errorCode/errorMessage）—— 例如 1014 Key 分级拒绝。
                code = payload.get('errorCode') or payload.get('code')
                msg = (payload.get('errorMessage') or payload.get('msg')
                       or payload.get('message') or '')
                print('  [FAIL] 提交被拒（未计费）：errorCode=%s' % code)
                print('         %s' % str(msg).split('|')[0].strip())
                if '|' in str(msg):
                    print('         %s' % str(msg).split('|', 1)[1].strip())
                ledger.record(batch_id=BATCH_ID, shot_id=key_id, stage='image', status='FAILED',
                              route='model-api', kind='model', target_id=endpoint,
                              error='errorCode=%s %s' % (code, msg),
                              extra={'endpoint': endpoint, 'rejected_before_submit': True})
                print()
                print('  ⇒ **未计费**（提交前即被拒，无 taskId、无 usage）。台账已记，便于日后核算。')
                return 1
            print('  taskId = %s' % task_id)
            ledger.record(batch_id=BATCH_ID, shot_id=key_id, stage='image', status='RUNNING',
                          route='model-api', kind='model', target_id=endpoint, task_id=task_id,
                          extra={'name': name, 'endpoint': endpoint, 'aspect': aspect,
                                 'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:16]})

            waited = 0
            while True:
                await asyncio.sleep(10)
                waited += 10
                try:
                    q = await client.query_v2(task_id)
                except RunningHubError as e:
                    print('    [%4ds] 查询失败: %s' % (waited, e.short))
                    continue
                d = q.get('data') if isinstance(q.get('data'), dict) else q
                status = str(d.get('status') or d.get('taskStatus') or '').upper()
                print('    [%4ds] %s' % (waited, status or '(空)'))
                if status in TERMINAL_OK:
                    usage = d.get('usage') or {}
                    results = d.get('results') or []
                    wall = round(time.time() - t0, 1)
                    # ★ 计费口径（本次实测）：模型 API 扣的是**按量美元钱包**，不是 RH币池 ——
                    #   usage.consumeCoins = null、consumeMoney = null，钱在 thirdPartyConsumeMoney。
                    #   而引擎台账的 billed 只看 consumeCoins ⇒ 会把这次成功记成"未计费"。这里显式纠正。
                    wallet_usd = _num(usage.get('thirdPartyConsumeMoney'))
                    rh = _num(usage.get('consumeCoins'))
                    billed = bool(rh) or bool(wallet_usd) or bool(_num(usage.get('consumeMoney')))
                    print('  [SUCCESS] 墙钟 %.1fs | usage=%s'
                          % (wall, json.dumps(usage, ensure_ascii=False)))
                    if wallet_usd:
                        print('      计费池  : 美元钱包 -$%s（约 ¥%.2f，与官方牌价 ¥0.07/次 吻合）'
                              % (wallet_usd, wallet_usd * 7.0))
                    files = []
                    for i, r in enumerate(results):
                        url = r.get('url') if isinstance(r, dict) else None
                        if not url:
                            continue
                        dest = out if (len(results) == 1 or i == 0) else \
                            out.with_name('%s_r%d%s' % (out.stem, i + 1,
                                                        pathlib.Path(url).suffix or '.png'))
                        await client.download(url, str(dest))
                        w, h = _size(dest)
                        files.append({'file': str(dest), 'role': 'asset' if i == 0 else 'extra',
                                      'width': w, 'height': h,
                                      'mb': round(dest.stat().st_size / 1024 / 1024, 2)})
                        print('      已保存: %s  (%sx%s, %.2f MB)'
                              % (dest, w, h, dest.stat().st_size / 1024 / 1024))
                    if files:
                        _make_preview(pathlib.Path(files[0]['file']))
                        upsert_manifest(man, {
                            'section': no, 'name': name, 'key': key_id, 'title': s['title'],
                            'style': s['style'], 'endpoint': endpoint, 'aspect': aspect,
                            'files': files, 'task_id': task_id,
                            'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:16],
                            'usage': usage, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                        })
                        save_manifest(man)
                    ledger.record(batch_id=BATCH_ID, shot_id=key_id, stage='image', status='SUCCESS',
                                  route='model-api', kind='model', target_id=endpoint,
                                  task_id=task_id, usage=usage, wall_elapsed_s=wall,
                                  out_bytes=sum(pathlib.Path(f['file']).stat().st_size for f in files) or None,
                                  artifact=';'.join(f['file'] for f in files) or None,
                                  output_url=(results[0].get('url') if results else None),
                                  extra={'name': name, 'endpoint': endpoint, 'aspect': aspect,
                                         'billed': billed,            # ★ 覆盖引擎只看 consumeCoins 的口径
                                         'billed_pool': 'wallet_usd' if wallet_usd else 'rh_coins',
                                         'wallet_usd': wallet_usd})
                    ok += 1
                    break
                if status in TERMINAL_BAD:
                    print('  [FAILED] %s' % json.dumps(d, ensure_ascii=False)[:500])
                    ledger.record(batch_id=BATCH_ID, shot_id=key_id, stage='image', status='FAILED',
                                  route='model-api', kind='model', target_id=endpoint,
                                  task_id=task_id, usage=d.get('usage') or {},
                                  wall_elapsed_s=round(time.time() - t0, 1),
                                  error=json.dumps(d, ensure_ascii=False)[:200])
                    fail += 1
                    rc = rc or 1
                    break
                if waited > timeout_min * 60:
                    print('  [超时] 已等 %.0f 分钟。任务可能仍在云端，下一条命令不要再提交：'
                          ' --resume-task %s' % (timeout_min, task_id))
                    fail += 1
                    rc = rc or 2
                    break
    print()
    print('本轮完成：成功 %d / 失败 %d（共选中 %d 个场景）' % (ok, fail, len(sel)))
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description='场景资产（空镜）生成：闭源模型 API，默认 dry-run')
    ap.add_argument('--list', action='store_true', help='列出 md 里的场景与提示词，然后退出')
    ap.add_argument('--only', default=None, help='只处理这些场景（逗号分隔的名字或序号），如 陆家宗祠天井 或 2')
    ap.add_argument('--model', choices=sorted(ENDPOINTS), default='lite-t2i',
                    help='端点：默认 lite-t2i（¥0.07/次）；i2i 版需 --ref')
    ap.add_argument('--aspect', default='16:9', help='画幅，默认 16:9（低价渠道版默认竖屏，必须显式传）')
    ap.add_argument('--ref', default=None, help='图生图参考图（本地路径）')
    ap.add_argument('--keep-negative', action='store_true', help='保留 md 里的「负面提示词：…」原文')
    ap.add_argument('--include-have', action='store_true', help='连已有成图的场景 1 一起处理')
    ap.add_argument('--force', action='store_true', help='已生成过的场景也重新生成（会重复计费）')
    ap.add_argument('--allow-consumer-key', action='store_true',
                    help='★ 允许用消费级 Key 试探模型 API（官方口径说一定失败；被拒不计费）')
    ap.add_argument('--submit', action='store_true', help='★ 真的提交（¥0.07/次，低价渠道版）')
    ap.add_argument('--timeout-min', type=float, default=15.0, help='单张轮询超时（分钟）')
    args = ap.parse_args()

    scenes = parse_scenes(ASSET_MD)
    emit_prompts_json(scenes)

    if args.list:
        man = load_manifest()
        gen = {it.get('section') for it in man.get('items', [])}
        print('%-4s %-30s %-8s %-8s %s' % ('序号', '场景', '有原图', '已生成', '风格标注'))
        print('-' * 96)
        for s in scenes:
            print('%-4d %-30s %-8s %-8s %s' % (s['section'], s['title'][:28],
                                               '是' if s['section'] in HAVE else '—',
                                               '是' if s['section'] in gen else '—', s['style'][:34]))
        todo = [s for s in scenes if s['section'] not in HAVE and s['section'] not in gen]
        print()
        print('缺口（未生成）：%s' % ('、'.join('%d %s' % (s['section'], s['name']) for s in todo) or '无'))
        print('提示词清单：%s' % PROMPTS_JSON)
        return 0

    only = args.only.split(',') if args.only else None
    sel = pick(scenes, only, args.include_have)
    if not sel:
        print('[FAIL] 没有选中任何场景')
        return 1
    return asyncio.run(generate(sel, args.model, args.aspect, args.keep_negative, args.ref,
                                args.submit, args.timeout_min, args.allow_consumer_key, args.force))


if __name__ == '__main__':
    sys.exit(main())
