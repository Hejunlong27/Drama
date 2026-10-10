#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""asset_md.py —— 「资产提示词 md」→ 本地 ComfyUI 资产清单（manifest.json）

为什么有这个脚本
----------------
云端通道（`gen_char_assets.py` / `gen_scene_assets.py`）各自解析同一份资产提示词 md，
但它们的产物是「直接提交给 RunningHub 的请求体」。本地 ComfyUI 通道吃的是另一种输入
（`comfy_client.py --spec` 的 manifest），因此需要一个**确定性转换器**把创作层产物
转成引擎输入 —— 这正是本脚本的职责，属设计文档里的「转换器补断点」一类。

设计纪律
--------
* **纯标准库**（re / json / argparse / pathlib），**零第三方依赖、零网络、零计费**，
  也不需要 `RUNNINGHUB_*` 任何 Key —— 本地通道的用户不该被云端依赖拖累。
* **只搬运、不发明**：解析出的提示词逐字保留，不删改、不重排、不补写。
* **解析口径与既有脚本一致**：章节结构沿用 `gen_char_assets.py` / `gen_scene_assets.py`
  （`# 一、角色资产库` / `# 二、场景资产` / `# 三、道具资产`；`## N. 名字` 节内 ``` 块 = 提示词）。
  实现独立成文件而**不 import 既有引擎**，是为了避免把 httpx / RunningHub 客户端拖进本地通道
  （既有引擎在 import 期就会 fail-fast 校验 Key 与工作区）。

用法
----
```bash
# 预览（不落盘）：列出 md 里解析到的全部资产
python asset_md.py --md 资产提示词.md --list

# 生成清单（默认含 char+scene+prop）
python asset_md.py --md 资产提示词.md --out comfyui_manifest.json

# 只看变化，不落盘
python asset_md.py --md 资产提示词.md --out comfyui_manifest.json --diff

# 只要人物，并强制全 2:3 竖版
python asset_md.py --md 资产提示词.md --out comfyui_manifest.json --kind char --aspect "2:3 (Portrait Photo)"
```

输出格式见 `stages/assets/comfyui/references/本地ComfyUI出图.md` §五。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

try:                                      # Windows 控制台 GBK → 强制 UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
except Exception:                                                # noqa: BLE001
    pass

# ── 章节与条目正则（与既有云端脚本同口径）────────────────────────────────────
SEC_CHAR_RE = re.compile(r'^##\s+(\d+)\.\s*(.+?)\s*$', re.M)
SEC_SCENE_RE = re.compile(r'^##\s+场景\s*(\d+)\s*[·.、]\s*(.+?)\s*$', re.M)
# 道具节无既有约定，取宽松式：`## 1. 名字` / `## 道具 1 · 名字` / `## 道具1 名字`
SEC_PROP_RE = re.compile(r'^##\s*(?:道具\s*)?(\d+)\s*[·.、]?\s*(.+?)\s*$', re.M)
FENCE_RE = re.compile(r'```(.*?)```', re.S)
BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
STYLE_RE = re.compile(r'\*\*胶片[：:]\s*(.+?)\*\*')

KINDS = ('char', 'scene', 'prop')
KIND_CN = {'char': '角色资产库', 'scene': '场景资产', 'prop': '道具资产'}
# 各 kind 的默认画幅（与 stages/assets/scripts/prompt_templates.py 的 RATIO 同口径：人物/场景 16:9，道具 1:1）
ASPECT_DEFAULT = {'char': '16:9 (Widescreen)', 'scene': '16:9 (Widescreen)', 'prop': '1:1 (Square)'}


def sanitize(name: str) -> str:
    """文件名安全化：md 里存在 `黑狐 / 暗影刺客` 这类带斜杠的名字，直接用会写出非法路径。"""
    s = re.sub(r'[/\\:*?"<>|]', '_', name or '').strip()
    return re.sub(r'_+', '_', s).strip('_') or 'unnamed'


def _find_heading(text: str, keyword: str, start: int = 0) -> int:
    """找标题行（`#`~`####` 开头且含 keyword）的位置；找不到返回 -1。"""
    pat = re.compile(r'^#{1,4}[^\n]*' + re.escape(keyword), re.M)
    m = pat.search(text, start)
    return m.start() if m else -1


def _slice(text: str, keyword: str, end_keywords: tuple) -> str:
    """切出某章节的正文（到下一个 end_keywords 章节为止）。章节缺失返回空串。"""
    i = _find_heading(text, keyword)
    if i < 0:
        return ''
    j = len(text)
    for kw in end_keywords:
        k = _find_heading(text, kw, i + 1)
        if k >= 0:
            j = min(j, k)
    return text[i:j]


def _variants(body: str) -> list:
    """把一段正文里的每个 ``` 块解析成一条提示词；其上方最近的加粗行是变体名。"""
    out, cursor = [], 0
    for fm in FENCE_RE.finditer(body):
        prompt = fm.group(1).strip()
        head = body[cursor:fm.start()]
        cursor = fm.end()
        if not prompt:
            continue
        labels = BOLD_RE.findall(head)
        out.append({'label': labels[-1].strip() if labels else '基础造型', 'prompt': prompt})
    return out


def _items(block: str, sec_re: re.Pattern, kind: str) -> list:
    """通用章节解析：`## <号> 标题` 节内每个 ``` 块 = 一条提示词。"""
    items = []
    matches = list(sec_re.finditer(block))
    for i, m in enumerate(matches):
        no = int(m.group(1))
        title = m.group(2)
        body = block[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(block)]
        variants = _variants(body)
        if not variants:
            continue
        style = STYLE_RE.search(body)
        name = sanitize(re.split(r'[（(｜|]', title)[0].strip().replace(' ', ''))
        items.append({'kind': kind, 'section': no, 'title': title, 'name': name,
                      'style': style.group(1).strip() if style else '',
                      'variants': variants})
    return items


def parse_md(md_path: pathlib.Path, kinds=KINDS) -> list:
    """解析资产提示词 md → 资产条目列表（每个变体一条）。"""
    if not md_path.is_file():
        raise SystemExit('[FAIL] 资产提示词 md 不存在：%s' % md_path)
    text = md_path.read_text(encoding='utf-8')

    out = []
    if 'char' in kinds:
        block = _slice(text, KIND_CN['char'], (KIND_CN['scene'], KIND_CN['prop']))
        out += _items(block, SEC_CHAR_RE, 'char')
    if 'scene' in kinds:
        block = _slice(text, KIND_CN['scene'], (KIND_CN['prop'],))
        out += _items(block, SEC_SCENE_RE, 'scene')
    if 'prop' in kinds:
        block = _slice(text, KIND_CN['prop'], ())
        out += _items(block, SEC_PROP_RE, 'prop')
    return out


def to_assets(entries: list, variant_label: str = '基础造型',
              all_variants: bool = False, aspects: dict | None = None) -> list:
    """资产条目 → manifest 的 assets 数组。一条资产一个 id，决定文件名前缀与输出子目录。"""
    aspects = aspects or ASPECT_DEFAULT
    assets = []
    for e in entries:
        kind, no = e['kind'], e['section']
        base = '%s_%02d' % (kind, no)     # char_01 / scene_01 / prop_01（与云端通道同口径）
        picks = e['variants'] if all_variants else \
            [next((v for v in e['variants'] if variant_label in v['label']), e['variants'][0])]
        for k, v in enumerate(picks):
            suffix = '' if (len(picks) == 1) else '_v%d' % (k + 1)
            aid = base + suffix
            label = '' if (len(picks) == 1 and v['label'] == '基础造型') else '_' + sanitize(v['label'])
            assets.append({
                'id': aid,
                'kind': kind,
                'name': e['name'],
                'role': e['title'],
                'section': no,
                'variant': v['label'],
                'prefix': '%s_%s%s' % (aid, e['name'], label),
                'prompt': v['prompt'],
                'aspect': aspects.get(kind) or '',
                **({'style': e['style']} if e.get('style') else {}),
            })
    assets.sort(key=lambda a: (KINDS.index(a['kind']), a['section'], a['id']))
    return assets


def build_manifest(md_path: pathlib.Path, assets: list, style_lock: str = '',
                   aspect: str = '') -> dict:
    prompt_sha = hashlib.sha256(''.join(a['prompt'] for a in assets).encode('utf-8')).hexdigest()[:16]
    return {
        '_note': '由 asset_md.py 从「资产提示词 md」自动生成，供本地 ComfyUI 通道使用（勿手改）',
        '_source': str(md_path),
        '_source_sha256_16': hashlib.sha256(md_path.read_bytes()).hexdigest()[:16],
        '_prompts_sha256_16': prompt_sha,
        'style_lock': style_lock,
        'aspect': aspect,
        'assets': assets,
    }


def diff_manifest(old: dict | None, new: dict) -> list:
    """清单差异（新增 / 删除 / 提示词变更 / 画幅变更），用于 `--diff` 预览。"""
    old_map = {a.get('id'): a for a in (old or {}).get('assets', [])}
    new_map = {a.get('id'): a for a in new.get('assets', [])}
    lines = []
    for k in new_map:
        if k not in old_map:
            lines.append('  + 新增   %-18s %s' % (k, new_map[k].get('name', '')))
        else:
            o, n = old_map[k], new_map[k]
            if o.get('prompt') != n.get('prompt'):
                lines.append('  ~ 提示词 %-18s (%d → %d 字)'
                             % (k, len(o.get('prompt') or ''), len(n.get('prompt') or '')))
            if (o.get('aspect') or '') != (n.get('aspect') or ''):
                lines.append('  ~ 画幅   %-18s %s → %s' % (k, o.get('aspect'), n.get('aspect')))
    for k in old_map:
        if k not in new_map:
            lines.append('  - 删除   %-18s %s' % (k, old_map[k].get('name', '')))
    return lines


def cmd_list(md_path: pathlib.Path, entries: list, as_json: bool) -> int:
    if as_json:
        print(json.dumps([{k: v for k, v in e.items() if k != 'variants'} |
                          {'variants': [x['label'] for x in e['variants']]} for e in entries],
                         ensure_ascii=False, indent=2))
        return 0
    print('%-6s %-8s %-6s %-16s %-10s %s' % ('类型', '节号', '名字', '标题', '变体数', '变体'))
    print('-' * 92)
    for e in entries:
        print('%-6s %-8d %-6s %-16s %-10d %s'
              % ({'char': '人物', 'scene': '场景', 'prop': '道具'}[e['kind']], e['section'],
                 e['name'], e['title'][:14], len(e['variants']),
                 ' / '.join(v['label'] for v in e['variants'])))
    print()
    print('合计 %d 个资产节（%s）' % (
        len(entries),
        '、'.join('%s %d' % ({'char': '人物', 'scene': '场景', 'prop': '道具'}[k],
                            sum(1 for e in entries if e['kind'] == k)) for k in KINDS)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description='资产提示词 md → 本地 ComfyUI 资产清单（纯标准库、零网络、零计费）')
    ap.add_argument('--md', required=True, help='资产提示词 md 路径')
    ap.add_argument('--out', default=None, help='输出 manifest.json 路径（省略则只预览不落盘）')
    ap.add_argument('--kind', default='char,scene,prop',
                    help='只解析这些类型：char,scene,prop（逗号分隔），默认全部')
    ap.add_argument('--list', action='store_true', help='列出解析结果然后退出（不落盘）')
    ap.add_argument('--json', action='store_true', help='配合 --list：以 JSON 输出')
    ap.add_argument('--diff', action='store_true', help='只对比 --out 已有清单，打印差异，不落盘')
    ap.add_argument('--variant', default='基础造型', help='人物取哪个变体（关键字），默认「基础造型」')
    ap.add_argument('--all-variants', action='store_true',
                    help='每个变体各出一项（id 加 _v1/_v2 后缀）；默认每个资产只取一个变体')
    ap.add_argument('--style-lock', default='',
                    help='统一风格串，会重复拼进每条 prompt（默认空；本项目资产提示词通常自带风格）')
    ap.add_argument('--aspect', default='', help='强制全部资产用这个画幅（覆盖各类型默认）')
    ap.add_argument('--aspect-char', default=None, help='人物画幅（默认 16:9 (Widescreen)）')
    ap.add_argument('--aspect-scene', default=None, help='场景画幅（默认 16:9 (Widescreen)）')
    ap.add_argument('--aspect-prop', default=None, help='道具画幅（默认 1:1 (Square)）')
    args = ap.parse_args()

    kinds = tuple(k.strip() for k in args.kind.split(',') if k.strip())
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        print('[FAIL] 未知类型：%s（可选 %s）' % (', '.join(unknown), '/'.join(KINDS)))
        return 2

    md_path = pathlib.Path(args.md)
    entries = parse_md(md_path, kinds)
    if not entries:
        print('[FAIL] 在 %s 里没解析到任何资产节。'
              '请确认章节标题为「%s」等，且每节内有 ``` 代码块提示词。'
              % (md_path, ' / '.join(KIND_CN[k] for k in kinds)))
        return 2

    if args.list:
        return cmd_list(md_path, entries, args.json)

    aspects = dict(ASPECT_DEFAULT)
    for k in KINDS:
        v = getattr(args, 'aspect_%s' % k)
        if v:
            aspects[k] = v
    if args.aspect:
        aspects = {k: args.aspect for k in KINDS}

    assets = to_assets(entries, args.variant, args.all_variants, aspects)
    man = build_manifest(md_path, assets, args.style_lock, args.aspect)

    if not args.out:
        print('[预览] 解析到 %d 个资产（未落盘）。加 --out <路径> 生成清单。' % len(assets))
        for a in assets[:20]:
            print('  %-18s %-6s %-12s %s' % (a['id'], a['kind'], a['name'],
                                             (a['aspect'] or '—')))
        if len(assets) > 20:
            print('  …（其余 %d 个略）' % (len(assets) - 20))
        return 0

    out = pathlib.Path(args.out)
    if args.diff:
        old = None
        if out.is_file():
            try:
                old = json.loads(out.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                old = None
        lines = diff_manifest(old, man)
        print('[差异] 新清单 %d 项 vs 现有清单 %d 项'
              % (len(man['assets']), len((old or {}).get('assets', []))))
        for ln in lines:
            print(ln)
        if not lines:
            print('  （无变化）')
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[OK] 已生成清单：%s' % out)
    print('     %d 个资产（%s）；提示词指纹 %s'
          % (len(assets),
             '、'.join('%s %d' % ({'char': '人物', 'scene': '场景', 'prop': '道具'}[k],
                                 sum(1 for a in assets if a['kind'] == k)) for k in KINDS),
             man['_prompts_sha256_16']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
