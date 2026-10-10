# -*- coding: utf-8 -*-
"""`asset_md.py` 离线测试 —— **零网络、零计费、零第三方依赖**。

覆盖：章节作用域隔离 · 节内多变体解析 · 名字/文件名安全化 · id 与画幅口径 ·
提示词逐字保真 · manifest schema · 差异检测。

```bash
python tests/test_asset_md.py            # 全部
python tests/test_asset_md.py --group A  # 只跑某组
```
"""
import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)                 # …/scripts
sys.path.insert(0, SCRIPTS)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import asset_md as M              # noqa: E402

CASES = []


def reg(group, name):
    def deco(fn):
        CASES.append((group, name, fn))
        return fn
    return deco


def A(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "断言失败")


# ── 测试用资产提示词 md（结构与《…第01-10集_资产提示词.md》一致）──────────────
SAMPLE_MD = """# 一、角色资产库

## 1. 陆鸣（主角·28岁）
**基础造型**
```
都市写实电影感，陆鸣，黑色风衣，正面站姿。
```
**便装造型**
```
都市写实电影感，陆鸣，灰色卫衣，休闲站姿。
```

## 2. 黑狐 / 暗影刺客（反派）
**基础造型**
```
都市写实电影感，黑狐，黑色面具。
```

## 12. 沈清瑶（女主）
**基础造型**
```
都市写实电影感，沈清瑶，米白长裙。
```

# 二、场景资产

## 场景 1 · 金碧辉煌酒楼大包厢
**胶片：暖调柯达**
```
电影感场景，金碧辉煌的酒楼大包厢，圆桌，吊灯。负面提示词：人物，人，人脸。
```

## 场景 2 · 陆家宗祠天井
**胶片：冷调青灰**
```
电影感场景，陆家宗祠天井，青石地面，正午顶光。
```

# 三、道具资产

## 1. 旧木柜
```
实物产品摄影，一件当代都市的旧木柜，胡桃木，半旧包浆。
```

## 2. 婚书
```
实物产品摄影，一张纸质婚书，有折痕。负面提示词：人物。
```
"""


def _write_sample():
    d = tempfile.mkdtemp(prefix="assetmd_")
    p = os.path.join(d, "资产提示词.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(SAMPLE_MD)
    return p


def _parse():
    p = _write_sample()
    return p, M.parse_md(M.pathlib.Path(p))


# =========================================================================== A 解析
@reg("A", "A01 三个章节各自被识别，条目数正确")
def _():
    _, e = _parse()
    kinds = {}
    for x in e:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    A(kinds.get("char") == 3, "人物节应为 3 个（实际 %s）" % kinds.get("char"))
    A(kinds.get("scene") == 2, "场景节应为 2 个（实际 %s）" % kinds.get("scene"))
    A(kinds.get("prop") == 2, "道具节应为 2 个（实际 %s）" % kinds.get("prop"))
    return "char3/scene2/prop2"


@reg("A", "A02 章节作用域隔离：场景节号 1/2 不会被当成人物节")
def _():
    _, e = _parse()
    chars = sorted(x["section"] for x in e if x["kind"] == "char")
    scenes = sorted(x["section"] for x in e if x["kind"] == "scene")
    props = sorted(x["section"] for x in e if x["kind"] == "prop")
    A(chars == [1, 2, 12], "人物节号应为 1,2,12（实际 %s）" % chars)
    A(scenes == [1, 2], "场景节号应为 1,2（实际 %s）" % scenes)
    A(props == [1, 2], "道具节号应为 1,2（实际 %s）" % props)
    return "作用域正确"


@reg("A", "A03 节内多变体解析：标签取自加粗行")
def _():
    _, e = _parse()
    lu = next(x for x in e if x["kind"] == "char" and x["section"] == 1)
    A(len(lu["variants"]) == 2, "陆鸣应有 2 个变体（实际 %d）" % len(lu["variants"]))
    A(lu["variants"][0]["label"] == "基础造型", "第 1 变体标签应为「基础造型」")
    A(lu["variants"][1]["label"] == "便装造型", "第 2 变体标签应为「便装造型」")
    return "2 变体"


@reg("A", "A04 标题含斜杠的名字被安全化（黑狐 / 暗影刺客）")
def _():
    _, e = _parse()
    hh = next(x for x in e if x["kind"] == "char" and x["section"] == 2)
    A(hh["name"] == "黑狐_暗影刺客", "名字应安全化为「黑狐_暗影刺客」（实际 %s）" % hh["name"])
    A("/" not in hh["name"] and "\\" not in hh["name"], "名字不得含路径分隔符")
    return hh["name"]


@reg("A", "A05 name 去掉括号注释与空格")
def _():
    _, e = _parse()
    lu = next(x for x in e if x["kind"] == "char" and x["section"] == 1)
    A(lu["name"] == "陆鸣", "应为「陆鸣」（实际 %s）" % lu["name"])
    sy = next(x for x in e if x["kind"] == "char" and x["section"] == 12)
    A(sy["name"] == "沈清瑶", "应为「沈清瑶」（实际 %s）" % sy["name"])
    return "ok"


@reg("A", "A06 场景胶片风格标注被提取")
def _():
    _, e = _parse()
    s1 = next(x for x in e if x["kind"] == "scene" and x["section"] == 1)
    A(s1["style"] == "暖调柯达", "风格应为「暖调柯达」（实际 %s）" % s1["style"])
    s2 = next(x for x in e if x["kind"] == "scene" and x["section"] == 2)
    A(s2["style"] == "冷调青灰", "风格应为「冷调青灰」（实际 %s）" % s2["style"])
    return "ok"


@reg("A", "A07 提示词逐字保真（含「负面提示词」原样保留）")
def _():
    _, e = _parse()
    s1 = next(x for x in e if x["kind"] == "scene" and x["section"] == 1)
    raw = "电影感场景，金碧辉煌的酒楼大包厢，圆桌，吊灯。负面提示词：人物，人，人脸。"
    A(s1["variants"][0]["prompt"] == raw, "场景 1 提示词应与 md 原文一致")
    p2 = next(x for x in e if x["kind"] == "prop" and x["section"] == 2)
    A(p2["variants"][0]["prompt"].endswith("负面提示词：人物。"),
      "道具提示词应原样保留（转换器只搬运不发明）")
    return "逐字一致"


@reg("A", "A08 缺失章节 → 不报错，返回该类型 0 条")
def _():
    d = tempfile.mkdtemp(prefix="assetmd_")
    p = os.path.join(d, "only_char.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("# 一、角色资产库\n\n## 1. 甲\n**基础造型**\n```\n一个角色。\n```\n")
    e = M.parse_md(M.pathlib.Path(p), ("char", "scene", "prop"))
    A(len(e) == 1 and e[0]["kind"] == "char", "应只解析出 1 个角色")
    e2 = M.parse_md(M.pathlib.Path(p), ("prop",))
    A(e2 == [], "只要道具时应返回空列表而不是抛异常")
    return "容缺"


@reg("A", "A09 md 不存在 → 明确报错（不静默返回空）")
def _():
    try:
        M.parse_md(M.pathlib.Path(os.path.join(tempfile.gettempdir(), "no_such_资产提示词.md")))
    except SystemExit as ex:
        A("[FAIL]" in str(ex), "应以 [FAIL] 前缀报错")
        return "明确报错"
    raise AssertionError("应当 SystemExit")


# =========================================================================== B 清单
@reg("B", "B01 id 口径：char_01 / scene_01 / prop_01")
def _():
    _, e = _parse()
    assets = M.to_assets(e)
    ids = [a["id"] for a in assets]
    A("char_01" in ids and "char_12" in ids, "人物 id 应为 char_01 / char_12")
    A("scene_01" in ids and "scene_02" in ids, "场景 id 应为 scene_01 / scene_02")
    A("prop_01" in ids and "prop_02" in ids, "道具 id 应为 prop_01 / prop_02")
    A(len(ids) == len(set(ids)), "id 不得重复：%s" % ids)
    return "%d 项" % len(ids)


@reg("B", "B02 默认只取一个变体（「基础造型」优先）")
def _():
    _, e = _parse()
    assets = M.to_assets(e)
    lu = [a for a in assets if a["kind"] == "char" and a["section"] == 1]
    A(len(lu) == 1, "默认应只出 1 个变体（实际 %d）" % len(lu))
    A(lu[0]["variant"] == "基础造型", "应取「基础造型」")
    return "单变体"


@reg("B", "B03 --all-variants：多变体各占一项，id 加后缀")
def _():
    _, e = _parse()
    assets = M.to_assets(e, all_variants=True)
    lu = sorted(a["id"] for a in assets if a["kind"] == "char" and a["section"] == 1)
    A(lu == ["char_01_v1", "char_01_v2"], "应为 char_01_v1/v2（实际 %s）" % lu)
    A(len(assets) == 8, "全变体应为 8 项（4 人物 + 2 场景 + 2 道具，实际 %d）" % len(assets))
    return "char_01_v1/v2"


@reg("B", "B04 画幅口径：人物/场景 16:9，道具 1:1；--aspect 可全局覆盖")
def _():
    _, e = _parse()
    a = M.to_assets(e)
    A(next(x for x in a if x["kind"] == "char")["aspect"] == "16:9 (Widescreen)", "人物应 16:9")
    A(next(x for x in a if x["kind"] == "scene")["aspect"] == "16:9 (Widescreen)", "场景应 16:9")
    A(next(x for x in a if x["kind"] == "prop")["aspect"] == "1:1 (Square)", "道具应 1:1")
    b = M.to_assets(e, aspects={"char": "2:3 (Portrait Photo)", "scene": "2:3", "prop": "2:3"})
    A(all(x["aspect"] == "2:3" for x in b if x["kind"] != "char"), "覆盖应生效")
    A(next(x for x in b if x["kind"] == "char")["aspect"] == "2:3 (Portrait Photo)", "人物覆盖应生效")
    return "16:9/16:9/1:1"


@reg("B", "B05 prefix 含名字且文件名安全（决定落盘文件名）")
def _():
    _, e = _parse()
    assets = M.to_assets(e)
    lu = next(a for a in assets if a["id"] == "char_01")
    A(lu["prefix"] == "char_01_陆鸣", "prefix 应为 char_01_陆鸣（实际 %s）" % lu["prefix"])
    hh = next(a for a in assets if a["id"] == "char_02")
    A("/" not in hh["prefix"], "prefix 不得含路径分隔符：%s" % hh["prefix"])
    return lu["prefix"]


@reg("B", "B06 manifest schema：必备键齐全，assets 排序稳定")
def _():
    p, e = _parse()
    man = M.build_manifest(M.pathlib.Path(p), M.to_assets(e), style_lock="统一风格。")
    for k in ("_note", "_source", "_source_sha256_16", "_prompts_sha256_16",
              "style_lock", "aspect", "assets"):
        A(k in man, "manifest 缺键 %s" % k)
    A(man["style_lock"] == "统一风格。", "style_lock 应透传")
    order = [(a["kind"], a["section"]) for a in man["assets"]]
    A(order == sorted(order, key=lambda t: (M.KINDS.index(t[0]), t[1])), "assets 应按类型+节号排序")
    for a in man["assets"]:
        for k in ("id", "kind", "name", "prompt", "prefix", "aspect"):
            A(k in a, "asset 缺键 %s：%s" % (k, a.get("id")))
        A(a["kind"] in M.KINDS, "kind 非法：%s" % a["kind"])
    return "%d 项 schema ok" % len(man["assets"])


@reg("B", "B07 同 md 两次生成 → 指纹一致（确定性）")
def _():
    p, e = _parse()
    m1 = M.build_manifest(M.pathlib.Path(p), M.to_assets(e))
    m2 = M.build_manifest(M.pathlib.Path(p), M.to_assets(e))
    A(m1 == m2, "同一输入应产出完全相同的清单")
    A(m1["_prompts_sha256_16"] == m2["_prompts_sha256_16"], "提示词指纹应一致")
    return m1["_prompts_sha256_16"]


@reg("B", "B08 提示词变化 → 指纹变化 + diff 报「提示词」")
def _():
    p, e = _parse()
    old = M.build_manifest(M.pathlib.Path(p), M.to_assets(e))
    e2 = M.parse_md(M.pathlib.Path(p))
    for x in e2:
        if x["kind"] == "char" and x["section"] == 1:
            x["variants"][0]["prompt"] = "改过的提示词，加了新描述。"
    new = M.build_manifest(M.pathlib.Path(p), M.to_assets(e2))
    A(old["_prompts_sha256_16"] != new["_prompts_sha256_16"], "提示词变了指纹应随之变化")
    lines = M.diff_manifest(old, new)
    A(any("提示词" in ln and "char_01" in ln for ln in lines),
      "diff 应报 char_01 提示词变更：%s" % lines)
    return "指纹与 diff 均生效"


@reg("B", "B09 diff 检出新增与删除")
def _():
    p, e = _parse()
    old = M.build_manifest(M.pathlib.Path(p), M.to_assets(e))
    new = M.build_manifest(M.pathlib.Path(p),
                           [a for a in M.to_assets(e) if a["id"] != "char_01"] +
                           [{"id": "char_99", "kind": "char", "name": "新角色",
                             "prompt": "新提示词", "prefix": "char_99_新角色",
                             "aspect": "16:9 (Widescreen)", "section": 99, "variant": "基础造型"}])
    lines = M.diff_manifest(old, new)
    A(any("新增" in ln and "char_99" in ln for ln in lines), "应报新增 char_99：%s" % lines)
    A(any("删除" in ln and "char_01" in ln for ln in lines), "应报删除 char_01：%s" % lines)
    return "新增/删除均检出"


@reg("B", "B10 sanitize 幂等且不外泄非法字符")
def _():
    for s in ("黑狐 / 暗影刺客", "a<b>c:d", "…", "", None, "正常名字"):
        once = M.sanitize(s)
        A(M.sanitize(once) == once, "sanitize 应幂等：%r → %r" % (s, once))
        A(not any(c in once for c in '/\\:*?"<>|'), "不得含非法字符：%r" % once)
    A(M.sanitize("") == "unnamed" and M.sanitize(None) == "unnamed", "空值应兜底为 unnamed")
    return "幂等"


# =========================================================================== 执行
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="")
    a = ap.parse_args()
    sel = [g.strip().upper() for g in a.group.split(",") if g.strip()] or ["A", "B"]
    titles = {"A": "资产提示词 md 解析", "B": "清单生成与差异"}
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
            print("  [%s] %-52s %s" % ("PASS" if ok else "FAIL", name, msg[:90]))
    tot = len(results)
    passed = sum(1 for r in results if r[2])
    print("\n" + "=" * 84)
    print("合计 %d 例 ｜ 通过 %d ｜ 失败 %d" % (tot, passed, tot - passed))
    print("=" * 84)
    for g, n, ok, m in results:
        if not ok:
            print("  !! [%s] %s\n      %s" % (g, n, m))
    return 0 if passed == tot else 1


if __name__ == "__main__":
    sys.exit(main())
