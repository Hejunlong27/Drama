#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_char_app.py —— 探测「人物资产」AI 应用的输入节点（只读，不计费）

用途
----
`config.assets.json` 的 `character_app` 指定了人物定妆图要走的 RunningHub AI 应用
（id 用环境变量 `RH_CHAR_APP_ID` 注入），但它的**输入参数表从未实测过**（原 todo 第一条就是「probe 输入节点」）。

本脚本走官方只读接口拉取该 AI 应用的 `nodeInfoList`（= 页面上可填的参数表），
把每个可填节点的 nodeId / fieldName / 示例值 / 类型打出来，落盘成 JSON 供后续脚本引用。

契约
----
GET /api/webapp/apiCallDemo?apiKey=<key>&webappId=<id>
  -> data.nodeInfoList: [{nodeId, fieldName, fieldValue, description, ...}]

★ 计费纪律：本脚本**只读**，不创建任务、不计费。（与 accountStatus / probe 同类）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]   # .../Drama-agent
ENGINE = pathlib.Path(os.environ.get('AI_VIDEO_PIPELINE_SKILL')
                      or _PROJECT_ROOT / 'stages' / 'video' / 'workflow') / 'scripts' / 'engine'
sys.path.insert(0, str(ENGINE))
os.environ.setdefault('PYTHONUTF8', '1')

import config as C                                        # noqa: E402
from runninghub_client import RunningHubClient, RunningHubError   # noqa: E402

# 输出快照目录：优先 DRAMA_WORKSPACE，回退当前目录 ./docs
WS = pathlib.Path(os.environ.get('DRAMA_WORKSPACE') or pathlib.Path.cwd())

# 探测目标 AI 应用 id：账号绑定资源，从环境变量注入
DEFAULT_APP = os.environ.get('RH_CHAR_APP_ID', '')


def show(nodes: list) -> None:
    print('共 %d 个可填节点' % len(nodes))
    print('-' * 92)
    print('%-8s %-26s %-13s %s' % ('nodeId', 'fieldName', '类型线索', '示例值'))
    print('-' * 92)
    for n in nodes:
        v = n.get('fieldValue')
        s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        s = s.replace('\n', ' ')
        if len(s) > 46:
            s = s[:43] + '...'
        # 类型线索：示例值的形态（字符串/数字/bool/空）
        kind = {str: 'str', int: 'int', float: 'float', bool: 'bool',
                list: 'list', dict: 'dict', type(None): 'EMPTY'}.get(type(v), type(v).__name__)
        if isinstance(v, str) and v.strip() == '':
            kind = 'EMPTY(str)'
        print('%-8s %-26s %-13s %s' % (n.get('nodeId'), n.get('fieldName'), kind, s))


async def main() -> int:
    ap = argparse.ArgumentParser(description='探测 AI 应用输入节点（只读，不计费）')
    ap.add_argument('--app', default=DEFAULT_APP, help='AI 应用 id（默认=人物资产应用）')
    ap.add_argument('--out', default=str(WS / 'docs' / 'probe-char-app.json'))
    ap.add_argument('--raw', action='store_true', help='额外打印完整原始 JSON')
    args = ap.parse_args()

    if not C.RUNNINGHUB_API_KEY:
        print('[FAIL] 未设置 RUNNINGHUB_API_KEY')
        return 2

    print('站点      : %s' % C.RH_BASE_URL)
    print('AI 应用 id: %s' % args.app)
    print()

    async with RunningHubClient(C.RUNNINGHUB_API_KEY, C.RH_BASE_URL, C.HTTP_TIMEOUT) as client:
        try:
            nodes = await client.get_webapp_nodes(args.app)
        except RunningHubError as e:
            print('[FAIL] 探测失败: %s' % e.short)
            return 1

    show(nodes)
    if args.raw:
        print()
        print(json.dumps(nodes, ensure_ascii=False, indent=2)[:6000])

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        '_note': 'AI 应用输入节点探测结果（只读接口，未计费）',
        'base_url': C.RH_BASE_URL,
        'webapp_id': str(args.app),
        'node_count': len(nodes),
        'nodes': nodes,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print()
    print('已写入：%s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
