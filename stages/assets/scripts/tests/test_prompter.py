# -*- coding: utf-8 -*-
"""`asset-card-image-prompter` 离线测试 —— **零网络、零计费**。

覆盖：净化器边界（含全部历史回归）· 净化器幂等 · 模板段位/省略/守卫注入 · 三项自检器自身有效性。

```bash
python tests/test_prompter.py            # 全部
python tests/test_prompter.py --group A  # 只跑某组
```
"""
import argparse
import io
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)                 # …/scripts
SKILL = os.path.dirname(SCRIPTS)                # …/<skill>
sys.path.insert(0, SCRIPTS)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import sanitize as S              # noqa: E402
import prompt_templates as T      # noqa: E402

CASES = []


def reg(group, name):
    def deco(fn):
        CASES.append((group, name, fn))
        return fn
    return deco


def A(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "断言失败")


def item(iid="PR99", kind="prop", title="测试道具", **fields):
    f = {"风格": "写实", "材料": "布", "材质": "布", "状态": "崭新",
         "形象定位": "一名中年男性", "面部特征": "国字脸", "表情": "平静", "服装细节": "灰色西装",
         "时代": "现代都市", "时段": "日", "空间": "室内",
         "场景描述": "一间铺木地板的客厅", "光照氛围": "午后侧窗光", "视觉锚点": "墙角立着一个旧木柜"}
    f.update(fields)
    return {"kind": kind, "id": iid, "title": title, "fields": f, "raw": ""}


# =========================================================================== A 净化器边界
@reg("A", "A01 空输入 / None → 空串")
def _():
    A(S.sanitize("") == "", "空串应返回空串")
    A(S.sanitize(None) == "", "None 应返回空串")
    return "ok"


@reg("A", "A02 纯视觉括号必须保留")
def _():
    out = S.sanitize("浅灰色（哑光）")
    A("哑光" in out, "视觉括号被误删 → %r" % out)
    return out


@reg("A", "A03 含镜号的括号整组删除")
def _():
    out = S.sanitize("袖口磨毛起球（SC20 第 20 镜）")
    A("SC" not in out, "镜号残留 → %r" % out)
    A("袖口磨毛起球" in out, "视觉信息被误删 → %r" % out)
    return out


@reg("A", "A04 嵌套括号：内层含元信息")
def _():
    out = S.sanitize("外搭（简洁（SC45 第 11 镜））")
    A("SC" not in out and "外搭" in out, "处理错 → %r" % out)
    return out


@reg("A", "A05 【推断：…】整句丢弃（纪律 4 回归）")
def _():
    out = S.sanitize("玉质半透【推断：剧本只写金线细细地箍着】；断过一次")
    A("推断" not in out, "推断依据残留 → %r" % out)
    A("断过一次" in out, "正常句被误删 → %r" % out)
    return out


@reg("A", "A06 §章节号剥离")
def _():
    out = S.sanitize("断口以金线箍好（§③3.1「玉」行）。")
    A("§" not in out and "断口以金线箍好" in out, "处理错 → %r" % out)
    return out


@reg("A", "A07 → 箭头引用剥离")
def _():
    out = S.sanitize("行政部职员→星锐董事长")
    A("→" not in out and "董事长" not in out and "行政部职员" in out, "处理错 → %r" % out)
    return out


@reg("A", "A08 ⚠️ 标记剥离 + 规矩句丢弃")
def _():
    A(S.sanitize("⚠️ 本卡禁用崩口二字") == "", "应整句丢弃 → %r" % S.sanitize("⚠️ 本卡禁用崩口二字"))
    return "ok"


@reg("A", "A09 Markdown 粗体标记剥离")
def _():
    out = S.sanitize("**素圈**无雕工")
    A("**" not in out and "素圈无雕工" in out, "处理错 → %r" % out)
    return out


@reg("A", "A10 规矩句丢弃、视觉句保留")
def _():
    out = S.sanitize("哑光包浆；剧本未写；内壁温润")
    A("剧本" not in out and "哑光包浆" in out and "内壁温润" in out, "处理错 → %r" % out)
    return out


@reg("A", "A11 软化：器物内壁极小号 → 不可辨识刻痕")
def _():
    out = S.sanitize("内壁刻一串极小编号")
    A("编号" not in out, "『编号』残留会被画成伪文字 → %r" % out)
    A("刻痕" in out, "未落到替代文案 → %r" % out)
    return out


@reg("A", "A12 未配对左括号：只删括号字符，不得截断正文")
def _():
    out = S.sanitize("外搭（肩线与腰带")
    A("（" not in out and "肩线与腰带" in out, "正文被截断 → %r" % out)
    return out


@reg("A", "A13 孤立右括号删除")
def _():
    out = S.sanitize("外搭）肩线")
    A("）" not in out and "外搭" in out, "处理错 → %r" % out)
    return out


@reg("A", "A14 二次净化幂等（纪律 9 回归）")
def _():
    for txt in ("内壁刻一串极小编号", "浅灰色（哑光）", "哑光包浆；剧本未写；内壁温润",
                "主光（约 3200K，落在风衣上；合记忆点纪律的说明）；顶灯作环境光（约 4000K，均匀）"):
        s1 = S.sanitize(txt)
        A(S.sanitize(s1) == s1, "非幂等：%r → %r" % (s1, S.sanitize(s1)))
    return "ok"


@reg("A", "A15 回归：整句丢弃后括号仍平衡（纪律 5/7）")
def _():
    t = S.sanitize('现代都市 · 当下与多年前（做旧场；闪回层按"现代都市 · 多年前（做旧）"执行）')
    A(t.count("（") == t.count("）"), "不平衡 → %r" % t)
    A("做旧" in t, "正常描述被误删 → %r" % t)
    return t


@reg("A", "A16 回归：正文里的视觉同名词不得触发整句丢弃（纪律 2/3）")
def _():
    bad = []
    for txt, must in (("椅腿刮出的浅白痕是本卡固定标记。", "浅白痕"),
                      ("七个小方块排成一排、红线一层层连下去。", "红线"),
                      ("全卡唯一的方位与跨镜辨认依据。", "依据"),
                      ("合记忆点纪律的领带歪着。", "领带")):
        out = S.sanitize(txt)
        if must not in out:
            bad.append((txt, out))
    A(not bad, "正常视觉描述被整句删光：%s" % bad)
    return "4 例均保留"


@reg("A", "A17 `按…执行` 类正常描述不得误删")
def _():
    t = S.sanitize("闪回层按现代都市多年前做旧执行，暖褐降饱和")
    A("闪回层" in t and "暖褐" in t, "正常句被误删 → %r" % t)
    t2 = S.sanitize("色值按资产卡第 12 行执行")
    A("资产卡" not in t2, "文档引用句未被丢弃 → %r" % t2)
    return "窄化生效"


@reg("A", "A18 中文前缀内部文档名必须剥离（纪律 10）")
def _():
    out = S.sanitize("领带为原著表述——出自 01-人物档案-正文版.md")
    A(".md" not in out, "内部文档名残留 → %r" % out)
    return out


@reg("A", "A19 内置自测样例全过")
def _():
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bad = S.selftest()
    A(bad == 0, "内置自测失败 %d 例：%s" % (bad, buf.getvalue()[-300:]))
    return "内置样例通过"


# =========================================================================== B 模板
@reg("B", "B01 build_char 段位完整")
def _():
    t = T.build_char(item("CH01", "char"))
    for seg in ("【统一风格", "【主视觉大全身立绘】", "【面部特征】", "【服装与配饰细节】",
                "【画面构图", "【一致性要求】", "【禁止项】", "三视图"):
        A(seg in t, "缺段位 %s" % seg)
    return "%d 字" % len(t)


@reg("B", "B02 build_scene 段位完整 + 空场纪律")
def _():
    t = T.build_scene(item("EN01", "scene"))
    for seg in ("【统一风格", "【时代与空间】", "【场景描述", "【光照氛围", "【视觉锚点",
                "【纸面与界面纪律】", "【禁止项】", "空场"):
        A(seg in t, "缺段位 %s" % seg)
    A("本场景唯一光源纪律" in t, "缺光源纪律")
    A("全卡" not in t, "模板泄漏内部术语『全卡』（纪律 11）")
    return "%d 字" % len(t)


@reg("B", "B03 build_prop 必含唯一主体段 + 固定风格锚")
def _():
    t = T.build_prop(item("PR03", "prop", 风格="东方国风美学、东方古典色彩"))
    A("【唯一主体｜必须准确，先读这一条】" in t, "缺唯一主体段")
    A(T.PROP_STYLE_DEFAULT in t, "缺固定风格锚")
    A("东方国风美学" not in t, "回喂了资产卡「风格」字段（古装污染根因）")
    return "ok"


@reg("B", "B04 道具身份守卫注入生效")
def _():
    guards = {"PR03": "一件女式米白色斜纹卡其长风衣（**单件外套**：不画裤子、不画鞋）。"}
    t = T.build_prop(item("PR03", "prop"), guards)
    A("单件外套" in t, "守卫文案未进提示词")
    t2 = T.build_prop(item("PR09", "prop"))          # 无守卫 → 通用兜底
    A("当代都市的日常物件" in t2, "无守卫时缺兜底文案")
    return "ok"


@reg("B", "B05 字段为空 → 整行省略，不留空段位（纪律 12）")
def _():
    it = item("PR10", "prop", 材质="", 状态="")
    t = T.build_prop(it)
    for ln in t.split("\n"):
        A(ln.strip() != "【材质】" and ln.strip() != "【状态】", "出现空段位：%r" % ln)
    A("【材质】" not in t and "【状态】" not in t, "空字段仍渲染了标题")
    return "ok"


@reg("B", "B06 三类模板均无空段位（合成样本全字段/缺字段两轮）")
def _():
    bad = []
    for k in ("char", "scene", "prop"):
        for it in (item("X01", k), item("X02", k, 材质="", 状态="", 视觉锚点="", 表情="",
                                         面部特征="", 服装细节="", 形象定位="", 光照氛围="",
                                         场景描述="", 镜头角度="")):
            lines = T.BUILD[k](it).split("\n")
            for i, ln in enumerate(lines):
                if not re.fullmatch(r"【[^】]{2,}】", ln.strip()):
                    continue
                nxt = next((lines[j].strip() for j in range(i + 1, len(lines)) if lines[j].strip()), "")
                if nxt == "" or nxt.startswith("【"):
                    bad.append((k, it["id"], ln.strip()))
    A(not bad, "空段位：%s" % bad[:5])
    return "ok"


@reg("B", "B07 画幅与像素映射覆盖三类")
def _():
    A(set(T.RATIO) == {"char", "scene", "prop"} and set(T.MP) == {"char", "scene", "prop"}, "键不全")
    A(T.RATIO["prop"] == "1:1 (Square)", "道具应为方图")
    return str(T.RATIO)


# =========================================================================== C 三项自检器
@reg("C", "C01 自检器对干净目录报 0")
def _():
    d = tempfile.mkdtemp(prefix="pk_ok_")
    try:
        os.makedirs(os.path.join(d, "prop"))
        io.open(os.path.join(d, "prop", "PR01.txt"), "w", encoding="utf-8").write(
            "一张道具图。\n【材质】半透玉。\n【状态】半旧。\n")
        r = S.self_check(d)
        A(r["files"] == 1, "文件数错")
        A(not (r["meta"] or r["unbalanced"] or r["empty_seg"]), "误报：%s" % r)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return "ok"


@reg("C", "C02 自检器能检出元信息残留 / 括号不平衡 / 空段位")
def _():
    d = tempfile.mkdtemp(prefix="pk_bad_")
    try:
        os.makedirs(os.path.join(d, "scene"))
        io.open(os.path.join(d, "scene", "EN01.txt"), "w", encoding="utf-8").write(
            "出自 01-人物档案-正文版.md。\n【视觉锚点】\n【光照氛围（本场景唯一光源纪律）】开着的窗（\n")
        r = S.self_check(d)
        A(r["meta"], "未检出元信息残留")
        A(r["unbalanced"], "未检出括号不平衡")
        A(r["empty_seg"], "未检出空段位")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return "三类均检出"


# =========================================================================== 执行
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="")
    a = ap.parse_args()
    sel = [g.strip().upper() for g in a.group.split(",") if g.strip()] or ["A", "B", "C"]
    titles = {"A": "净化器边界（含历史回归）", "B": "提示词模板", "C": "三项自检器"}
    results = []
    for g in sel:
        cases = [(n, f) for gg, n, f in CASES if gg == g]
        if not cases:
            continue
        print("\n" + "=" * 84)
        print("【%s 组】%s（%d 例）" % (g, titles.get(g, g), len(cases)))
        print("=" * 84)
        for name, fn in cases:
            try:
                d = fn()
                ok, msg = True, ("" if d is None else str(d))
            except AssertionError as e:
                ok, msg = False, "断言失败：%s" % (e or "")
            except Exception as e:                      # noqa: BLE001
                ok, msg = False, "%s: %s" % (type(e).__name__, str(e)[:200])
            results.append((g, name, ok, msg))
            print("  [%s] %-50s %s" % ("PASS" if ok else "FAIL", name, msg[:90]))
    tot = len(results)
    passed = sum(1 for r in results if r[2])
    print("\n" + "=" * 84)
    print("合计 %d 例 ｜ 通过 %d ｜ 失败 %d" % (tot, passed, tot - passed))
    print("=" * 84)
    for g, n, ok, m in results:
        if not ok:
            print("  ✗ [%s] %s\n      %s" % (g, n, m))
    return 0 if passed == tot else 1


if __name__ == "__main__":
    sys.exit(main())
