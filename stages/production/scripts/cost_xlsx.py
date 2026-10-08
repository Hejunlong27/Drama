# -*- coding: utf-8 -*-
"""cost_xlsx.py —— 把成本台账写成 Excel（.xlsx），供后期核算

用户需求（2026-09-20）：
    「输出的格式用 excel 表格，按单部短剧做统计……可以归类核算，
      比如角色、场景资产类（rh币、钱包余额、token消耗），剧集生产类（rh币、钱包余额、token消耗）。」

用户已确认的五条设计决策：
    ① 钱包口径：只要「花掉多少」（美元钱包**消耗**），不要余额快照；
    ② token 归类：尽量归到两大类并标「近似」，其余进「未归属」；
    ③ 资产批次：给资产脚本加时间戳 batch_id，使「每一次执行」各占一行；
    ④ 更新方式：**只刷数据区**，保留其它 sheet 与批注；
    ⑤ 汇总格：用 Excel 公式（SUMIF）引用明细，方便人工核账。

★ 口径（用户 2026-09-19 铁律）：消耗只取平台按任务返回的 usage，**不用余额差值倒推消耗**。

工作簿结构（7 个表）：
    总览 / 任务台账 / 资产明细 / 剧集明细 / 逐段明细 / token 归属 / 口径说明
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# 大类（与 cost_ledger.big_class 保持一致；总览的 SUMIF 靠这几个字面量匹配）
CLASS_ASSET = '角色/场景资产类'
CLASS_EPISODE = '剧集生产类'
CLASS_OTHER = '其他'
CLASS_UNATTR = '未归属（无法归类）'

# 「只刷数据区」的状态：记录每个表上次写了多少行数据，好在下次精确清空
STATE = None  # 由 cost_ledger 注入

HEAD_FILL = PatternFill('solid', fgColor='1F4E79')
HEAD_FONT = Font(bold=True, color='FFFFFF', size=10)
TITLE_FONT = Font(bold=True, size=14)
NOTE_FONT = Font(size=9, color='808080')
TOTAL_FONT = Font(bold=True)
THIN = Side(style='thin', color='BFBFBF')
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _sheet(wb, name: str, titles: list[str], headers: list[str],
           widths: list[int], col_keep: int):
    """取（或新建）一个受管工作表，写好标题行与表头，并**清空旧数据区**。

    titles = [大标题, 副标题/说明]；headers = 表头各列。
    返回 (ws, data_start_row)。清空只动 `.value`，所以**单元格批注、格式、条件格式都保留**；
    数据区之外（例如你自己在下面加的备注行）完全不动。
    """
    if name in wb.sheetnames:
        ws = wb[name]
    else:
        ws = wb.create_sheet(name)
    # 标题
    if not ws.cell(row=1, column=1).value:
        ws.cell(row=1, column=1, value=titles[0]).font = TITLE_FONT
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=col_keep)
        ws.cell(row=3, column=1, value=titles[1]).font = NOTE_FONT
    # 表头
    header_row = 4
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=c, value=h)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = BOX
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    data_start = header_row + 1

    # ---- 清空上次的数据区（行数由 sidecar 状态记录，避免把用户备注行也清掉）----
    prev = int((STATE or {}).get(name, 0))
    for r in range(data_start, data_start + prev):
        for c in range(1, col_keep + 1):
            ws.cell(row=r, column=c).value = None
    return ws, data_start


def _write_row(ws, r: int, values: list, *, bold=False, money_cols=(), num_cols=()):
    for c, v in enumerate(values, start=1):
        cell = ws.cell(row=r, column=c, value=v)
        cell.border = BOX
        if bold:
            cell.font = TOTAL_FONT
        if c in money_cols:
            cell.number_format = '#,##0.00'
        elif c in num_cols:
            cell.number_format = '#,##0'
    return r + 1


def build(path: pathlib.Path, ctx: dict) -> dict:
    """写工作簿。ctx 由 cost_ledger 组装；返回「各表数据行数」供下次清空使用。"""
    global STATE
    drama = ctx['drama']
    state = {}
    wb = load_workbook(path) if path.is_file() else Workbook()
    if 'Sheet' in wb.sheetnames and len(wb.sheetnames) == 1:
        del wb['Sheet']

    # ===================== 1. 任务台账（其它表靠它做 SUMIF） =====================
    cols = ['序号', '执行时间', '任务ID', '归属大类', '任务内容', '数量',
            '成功(次)', '失败(次)', 'RH币', '美元钱包(USD)', '折合RH币', '折合人民币',
            'token合计', 'token名义¥', '任务时长', '备注']
    ws, st = _sheet(wb, '任务台账',
                    ['%s · 任务台账（每次执行一行）' % drama,
                     '口径：消耗只取平台按任务返回的 usage；token 为按结账区间增量的**近似**归属'
                     '（历史任务无逐批次时间戳，故为空白）。本表由脚本自动生成，数据区会被覆盖，'
                     '请在数据区之外加备注。'],
                    cols, [6, 17, 22, 16, 26, 10, 9, 9, 10, 13, 12, 12, 12, 12, 11, 22], len(cols))
    r = st
    for i, t in enumerate(ctx['tasks'], start=1):
        tok = ctx['tok_by_obj'].get(t['batch_id'], {})
        row = [i, t['at'], t['batch_id'], t['cls'], t['label'], t['qty'],
               t['ok'], t['fail'], t['rh'], t['usd'], t['as_rh'], t['cny'],
               tok.get('tokens'), tok.get('notional_cny'), t['elapsed'], t['note']]
        r = _write_row(ws, r, row, money_cols=(10, 12, 14), num_cols=(9, 11, 13))
        # 折合人民币 = 折合RH币 × 汇率（写公式，改汇率能重算）
        ws.cell(row=r - 1, column=12).value = '=K%d*$B$101' % (r - 1)
        # token 名义¥ 用公式引用「token 归属」表，保证与归属表一致
        ws.cell(row=r - 1, column=14).value = (
            "=SUMIF('token 归属'!$A:$A,$C%d,'token 归属'!$F:$F)" % (r - 1))
        ws.cell(row=r - 1, column=13).value = (
            "=SUMIF('token 归属'!$A:$A,$C%d,'token 归属'!$E:$E)" % (r - 1))
    state['任务台账'] = r - st
    ws.freeze_panes = 'A5'

    # ===================== 2. 资产明细 =====================
    cols = ['资产键', '名称', '类型', '批次', 'taskId', 'RH币', '美元钱包(USD)',
            '折合人民币', '图片规格', '产物文件']
    ws2, st2 = _sheet(wb, '资产明细',
                      ['%s · 资产明细（角色 / 场景）' % drama,
                       '角色与场景的定妆图/空镜逐项成本。这些属于「%s」。' % CLASS_ASSET],
                      cols, [10, 16, 8, 24, 22, 9, 14, 12, 14, 52], len(cols))
    r = st2
    for a in ctx['assets']:
        r = _write_row(ws2, r, [a['key'], a['name'], a['type'], a['batch'], a['task_id'],
                                a['rh'], a['usd'], a['cny'], a['spec'], a['file']],
                       money_cols=(8,), num_cols=(6,))
        ws2.cell(row=r - 1, column=8).value = '=(F%d+G%d*$B$102)*$B$101' % (r - 1, r - 1)
    state['资产明细'] = r - st2
    ws2.freeze_panes = 'A5'

    # ===================== 3. 剧集明细 =====================
    cols = ['集', '分镜', '成功(次)', '失败(次)', 'RH币', '折合人民币', '已完成时长(s)', '产物目录']
    ws3, st3 = _sheet(wb, '剧集明细',
                      ['%s · 剧集明细（逐集）' % drama,
                       '这些属于「%s」。成功/失败按提交次数计（失败重跑各计一次）。' % CLASS_EPISODE],
                      cols, [10, 9, 11, 11, 11, 13, 14, 56], len(cols))
    r = st3
    for e in ctx['episodes']:
        r = _write_row(ws3, r, [e['ep'], e['shots'], e['ok'], e['fail'],
                                e['rh'], None, e['dur'], e['outdir']], num_cols=(5, 7))
        ws3.cell(row=r - 1, column=6).value = '=E%d*$B$101' % (r - 1)
    state['剧集明细'] = r - st3
    ws3.freeze_panes = 'A5'

    # ===================== 4. 逐段明细 =====================
    cols = ['shot_id', '集', '时长(s)', '帧数', '状态', 'RH币', '墙钟(s)', 'taskId', '产物文件']
    ws4, st4 = _sheet(wb, '逐段明细',
                      ['%s · 逐段明细（每段一行）' % drama,
                       '按分镜逐条，用于日后按段追溯成本。状态=该分镜最后一次提交结果。'],
                      cols, [16, 8, 10, 9, 10, 9, 11, 22, 56], len(cols))
    r = st4
    for s in ctx['shots']:
        r = _write_row(ws4, r, [s['shot'], s['ep'], s['dur'], s['frames'], s['status'],
                                s['rh'], s['wall'], s['task_id'], s['file']], num_cols=(6, 7))
    state['逐段明细'] = r - st4
    ws4.freeze_panes = 'A5'

    # ===================== 5. token 归属 =====================
    cols = ['归属对象', '范围', '归属时间', 'token调用', 'token合计', 'token名义¥', '说明']
    ws5, st5 = _sheet(wb, 'token 归属',
                      ['%s · Agent token 归属（★ 近似）' % drama,
                       'DSH 账本只有「按天/按会话」粒度、没有逐批次时间戳 ⇒ 单次 token 是按结账区间增量'
                       '近似归属的，会混入同区间内我做的其它事。历史任务无法回溯拆分，故为空。'],
                      cols, [30, 12, 20, 12, 16, 12, 60], len(cols))
    r = st5
    for o in ctx['tok_rows']:
        r = _write_row(ws5, r, [o['obj'], o['scope'], o['at'], o['calls'], o['tokens'],
                                o['notional'], o['why']], num_cols=(4, 5), money_cols=(6,))
    state['token 归属'] = r - st5
    ws5.freeze_panes = 'A5'

    # ===================== 6. 总览（公式引用上面几张表） =====================
    cols = ['成本大类', '条目', 'RH币', '美元钱包(USD)', '折合RH币', '折合人民币',
            'token合计', 'token名义¥', '合计人民币']
    ws6, st6 = _sheet(wb, '总览',
                      ['%s · 成本台账总览' % drama,
                       '汇总格全部是 Excel 公式，引用「任务台账」「token 归属」；'
                       '改了明细，这里会自动重算。'],
                      cols, [22, 9, 11, 14, 12, 13, 14, 13, 13], len(cols))
    r = st6
    for cls in (CLASS_ASSET, CLASS_EPISODE, CLASS_OTHER):
        r = _write_row(ws6, r, [
            cls,
            '=COUNTIF(任务台账!$D:$D,$A%d)' % r,
            '=SUMIF(任务台账!$D:$D,$A%d,任务台账!$I:$I)' % r,
            '=SUMIF(任务台账!$D:$D,$A%d,任务台账!$J:$J)' % r,
            '=SUMIF(任务台账!$D:$D,$A%d,任务台账!$K:$K)' % r,
            '=SUMIF(任务台账!$D:$D,$A%d,任务台账!$L:$L)' % r,
            "=SUMIF('token 归属'!$A:$A,$A%d,'token 归属'!$E:$E)" % r,
            "=SUMIF('token 归属'!$A:$A,$A%d,'token 归属'!$F:$F)" % r,
            '=F%d+H%d' % (r, r),
        ], money_cols=(4, 9), num_cols=(3, 5, 6, 7, 8))
    r_ua = r
    r = _write_row(ws6, r, [
        CLASS_UNATTR, '—', None, None, None, None,
        "=SUMIF('token 归属'!$A:$A,$A%d,'token 归属'!$E:$E)" % r,
        "=SUMIF('token 归属'!$A:$A,$A%d,'token 归属'!$F:$F)" % r,
        '=F%d+H%d' % (r, r),
    ], money_cols=(4, 9))
    r_tot = r
    r = _write_row(ws6, r, [
        '合计',
        '=SUM(B%d:B%d)' % (st6, r_tot - 1),
        '=SUM(C%d:C%d)' % (st6, r_tot - 1),
        '=SUM(D%d:D%d)' % (st6, r_tot - 1),
        '=SUM(E%d:E%d)' % (st6, r_tot - 1),
        '=SUM(F%d:F%d)' % (st6, r_tot - 1),
        '=SUM(G%d:G%d)' % (st6, r_tot - 1),
        '=SUM(H%d:H%d)' % (st6, r_tot - 1),
        '=SUM(I%d:I%d)' % (st6, r_tot - 1),
    ], bold=True, money_cols=(4, 9), num_cols=(3, 5, 6, 7, 8))
    # 未归属行的条目数不该参与「条目」合计，把 B 列合计改成前三行的和
    ws6.cell(row=r_tot, column=2).value = '=SUM(B%d:B%d)' % (st6, r_ua - 1)
    state['总览'] = r - st6

    # ---- 总览下方的口径/预估区（同样只刷自己那几行）----
    base = 100          # 参数区起始行（放在总览表下方，公式引用）
    prev = int((STATE or {}).get('总览参数', 0))
    for rr in range(base, base + prev):
        for c in range(1, 5):
            ws6.cell(row=rr, column=c).value = None
    lines = [
        ('【折算参数】', None, None, None),
        ('1 RH币 = ¥', ctx['rates']['cny_per_rh'], '元', '改这里，全表人民币列会重算'),
        ('1 美元 =', ctx['rates']['rh_per_usd'], 'RH币', '用户 2026-09-19 确认'),
        ('1 美元 = ¥', ctx['rates']['usd_cny'], '元', '由上两行推算'),
        ('【备注】', None, None, None),
    ]
    for i, (a, b, c, d) in enumerate(lines):
        ws6.cell(row=base + i, column=1, value=a).font = TOTAL_FONT
        ws6.cell(row=base + i, column=2, value=b)
        ws6.cell(row=base + i, column=3, value=c)
        ws6.cell(row=base + i, column=4, value=d).font = NOTE_FONT
    state['总览参数'] = len(lines)

    # ===================== 7. 口径说明 =====================
    cols = ['项', '内容']
    ws7, st7 = _sheet(wb, '口径说明',
                      ['%s · 口径与数据源' % drama,
                       '这一页写死口径，将来别人看表就知道数字是怎么来的。'],
                      cols, [22, 110], len(cols))
    r = st7
    for k, v in ctx['notes']:
        r = _write_row(ws7, r, [k, v])
        ws7.cell(row=r - 1, column=2).alignment = Alignment(wrap_text=True, vertical='top')
    state['口径说明'] = r - st7

    # 收尾：固定工作表顺序（总览在最前，便于打开就看结论）
    order = ['总览', '任务台账', '资产明细', '剧集明细', '逐段明细', 'token 归属', '口径说明']
    wb._sheets = [wb[n] for n in order if n in wb.sheetnames] + \
                 [s for s in wb._sheets if s.title not in order]
    wb.save(path)
    STATE = state
    return state
