# -*- coding: utf-8 -*-
r"""资产卡字段净化器 —— 把"给人看的内部文档"变成"能喂生图模型的纯视觉描述"。

## 为什么需要它（一句话）

资产卡里混着大量**不该进生图模型**的东西：`SC##/EP##` 镜号集号引用、`§x.y` 章节号、内部文档名、
⚠️ 阻断标记、【推断】考据、"禁用/不得"这类**给执行人的规矩**、"待裁/互斥"这类**未决冲突**。
直接塞进提示词，轻则污染画面，重则**被模型画成乱码伪文字**。

## 14 条纪律（都是从真缺陷里换来的，别删）

| # | 纪律 |
|---|---|
| 1 | **删除规则宁窄勿宽**。凡"命中某词即整句丢弃"，先问"这个词会不会出现在正常视觉描述里"。 |
| 2 | 词分两级：**强词**（`待裁/阻断/禁用/剧本/原文/原著/口径不一…`）命中即丢整句；<br>**弱词**（`本卡/全卡/红线/依据/纪律/说明/分析/建议/理由/推断/逐字/档案/口径`）**只擦词或限定位**（句首 / 紧跟冒号）。 |
| 3 | 弱词若整句丢，会把「椅腿的浅白痕是**本卡**固定标记」这种正常描述整句删光（实测把 4 个场景的「视觉锚点」清空）。 |
| 4 | `_STRIP_INFER` 必须吃掉**带内容**的 `【推断：…】`，不能只认裸 `【推断】`（否则"推断："会触发第 2 条，把字段整段清空）。 |
| 5 | 括号兜底必须**非破坏性**：只删无配对的 `）`/`（`，**绝不截断正文**。 |
| 6 | "计数器 + 从右截断"的括号修法是错的：未配对的是**最左**那个 `（` 时它修不干净，且会吃掉正文。 |
| 7 | 净化后**必须再平衡一次括号**——整句丢弃会带走 `）`。 |
| 8 | 软化规则**不做全局替换**：`编号→刻痕` 这种会把「车位编号（乙区十二）」「凭证编号 X-0719」这类**本就要求可见**的位置改坏。只软化"刻在器物内壁的极小号"这种必然出伪字的写法。 |
| 9 | 软化替换文本**禁止自带 `**`**；且 `_STRIP_FMT` 必须放在软化**之后**，否则二次净化不幂等。 |
| 10 | 文件名正则要**认中文前缀**（`[\w\-]{1,40}\.md`），`\b[A-Za-z0-9_\-]+\.md\b` 匹配不到 `01-人物档案-正文版.md`。 |
| 11 | 模板里也**不许出现内部术语**（如「全卡唯一光源纪律」的"全卡"）；模板文本不走净化，只能人工审。 |
| 12 | **字段净化后为空 → 整行省略**（见 `prompt_templates.seg()`），空段位等于把该维度交给模型自由发挥。 |
| 13 | 用法固定为：**先 dry-run 看提示词 → 跑三项自检 → 再提交任务**。 |
| 14 | 模板一改就要重跑 dry-run；`prompts/` 是派生件，审计追溯一律以提交台账为准。 |

## 三项自检（`--check <prompts 目录>`）

1. **元信息残留** = 0（`SC##/EP##/§/.md/【推断/剧本/原文/原著/本卡/全卡/禁用/待裁/阻断/比例 N:M`）
2. **括号不平衡** = 0
3. **空段位** = 0（形如 `【材质】` 后面什么都没有）

## CLI

```bash
python sanitize.py --selftest                  # 内置样例自测
python sanitize.py --check ../prompts          # 全量提示词三项自检
python sanitize.py --file ../prompts/prop/PR01.txt
```
"""
import argparse
import glob
import io
import os
import re
import sys

# ---------------------------------------------------------------------------
# 元信息识别
# ---------------------------------------------------------------------------
# 括号内出现这些 → 整组括号连内容一起删
_META_IN_PAREN = re.compile(
    r"SC\s*\d|EP\s*\d|§|\.md|推断|待裁|待回改|存疑|登记|风格圣经|资产卡|人物档案|立项|"
    r"剧本|原文|原著|档案|口径|镜|集|R2 订正")

# 内部文档名 / 编号串 / 章节号（**必须认中文前缀**）
_CODE = re.compile(r"[A-Z]{2,}-\d{2,}|[\w\-]{1,40}\.(?:md|json|py|csv|xlsx|docx|txt)\b|"
                   r"第\s*\d+\s*章\s*[:：]\s*\d+", re.UNICODE)
_STRIP_REF = re.compile(r"\b(?:EP|SC)\s*\d+(?:\s*[–\-—]\s*\d+)?\b")
_RATIO_WORD = re.compile(r"比例\s*\d+\s*[:：]\s*\d+\s*(?:竖屏|横屏)?")
_SECTION_REF = re.compile(r"§[^\s，；。]*")
_ARROW = re.compile(r"→[^/／|]*")
_LABEL = re.compile(r"（[甲乙丙丁戊己庚辛][^（）]{0,12}）")
# 带内容的推断块也要吃（纪律 4）
_STRIP_INFER = re.compile(r"【(?:推断|R2|剧本|原文|原著|分析)[^】]*】")

# 内部文档自称 → **只擦词、不丢句**（纪律 2、3）
_SCRUB = (("本卡", ""), ("全卡", ""))

# 强词：命中即丢整句（出现即基本可判定是"给执行人的规矩 / 未决冲突"，不是画面描述）
_DROP_STRONG = re.compile(
    r"待裁|待回改|存疑|互斥|口径不一|自相矛盾|未解|阻断|禁用|不得|不许|未擅改|详见|"
    r"以.{0,8}为准|登记册|风格圣经|指定本件|写死|原著表述|剧本明写|正文版|R2 订正|"
    r"剧本|原文|原著|原文给的是|"
    r"本卡(?:禁用|口径|登记|未|要求|纪律)|"
    r"按(?:§|第\s*\d+\s*行|剧本|原文|档案|资产卡|人物档案|分镜)")

# 弱词：**仅当在句首、或紧跟冒号时**才丢整句
_DROP_WEAK_HEAD = re.compile(
    r"^\s*[（(【\[]*\s*(?:本卡|红线|依据|纪律|说明|分析|建议|理由|推断|逐字|"
    r"档案|口径|剧本|原文|原著)")
_DROP_WEAK_COLON = re.compile(
    r"(?:本卡|红线|依据|纪律|说明|分析|建议|理由|推断|逐字|档案|口径|剧本|原文|原著)[：:]")

# 标记 / 格式
_STRIP_MARK = re.compile(r"[⚠⛔🔴🟡✅📌★🔺☆※]")
_STRIP_FMT = re.compile(r"\*\*|__|`|~~")

# 软化：只针对**必然导致生图出现乱码伪字**的写法（纪律 8）
_SOFTEN = [
    # ⚠️ 替换文本禁止自带 `**`（纪律 9）
    ("刻一串极小编号", "有一串极浅的细密刻痕（不可辨识、不成字）"),
    ("刻一串编号", "有一串极浅的细密刻痕（不可辨识、不成字）"),
    ("一串编号", "一串极浅的刻痕（不可辨识）"),
    ("内壁编号", "内壁一道极浅的刻痕（不可辨识）"),
    ("字符串", "浅刻痕"),
    ("刻着两个字", "有两道极浅的刻痕"),
    ("四个墨字", "四道极浅的墨痕"),
    ("两个字", "两道极浅的痕迹"),
]


def _strip_meta_parens(s: str) -> str:
    """括号感知（支持嵌套）地删掉"含元信息"的整组括号，保留纯视觉括号。"""
    stack = []
    kill = [False] * (len(s) + 1)
    for i, ch in enumerate(s):
        if ch == "（":
            stack.append(i)
        elif ch == "）" and stack:
            start = stack.pop()
            if _META_IN_PAREN.search(s[start + 1:i]):
                for k in range(start, i + 1):
                    kill[k] = True
                if stack:                      # 向上传播：外层内文本含元信息 → 一并删
                    kill[stack[-1]] = True
    for _ in range(3):                          # 收敛
        stack = []
        for i, ch in enumerate(s):
            if ch == "（":
                stack.append(i)
            elif ch == "）" and stack:
                start = stack.pop()
                if kill[start] or kill[i]:
                    for k in range(start, i + 1):
                        kill[k] = True
    return "".join(c for i, c in enumerate(s) if not kill[i])


def _balance(s: str) -> str:
    """括号修缮 —— **只删括号字符，绝不截断正文**，且返回结果保证括号平衡（纪律 5、6）。"""
    out, stack = [], []
    for ch in s:
        if ch == "（":
            stack.append(len(out))
            out.append(ch)
        elif ch == "）":
            if stack:
                stack.pop()
                out.append(ch)
            # 无配对右括号 → 丢弃
        else:
            out.append(ch)
    bad = set(stack)
    return "".join(c for i, c in enumerate(out) if i not in bad)


def _is_meta_sentence(seg: str) -> bool:
    return bool(_DROP_STRONG.search(seg) or _DROP_WEAK_HEAD.match(seg)
                or _DROP_WEAK_COLON.search(seg))


def sanitize(text: str) -> str:
    """把资产卡字段净化成**只含视觉描述**的提示词片段。幂等。"""
    if not text:
        return ""
    s = text
    s = _strip_meta_parens(s)
    for a, b in _SCRUB:                                 # 内部自称：擦词不丢句
        s = s.replace(a, b)
    s = _STRIP_INFER.sub("", s)
    s = _ARROW.sub("", s)
    s = _LABEL.sub("", s)
    s = _CODE.sub("", s)
    s = _STRIP_REF.sub("", s)
    s = _SECTION_REF.sub("", s)
    s = _RATIO_WORD.sub("", s)
    s = _STRIP_MARK.sub("", s)
    s = _balance(s)
    for a, b in _SOFTEN:                                # 软化在 _STRIP_FMT **之前**
        s = s.replace(a, b)
    s = _STRIP_FMT.sub("", s)                           # 放在软化后 → 二次净化幂等
    s = re.sub(r"（\s*）", "", s)                        # 空括号
    s = re.sub(r"[「『]\s*(?=[；。，、）]|$)", "", s)     # 孤立左引号
    s = re.sub(r"\s{2,}", " ", s)
    keep = []
    for seg in re.split(r"(?<=[；。])", s):
        seg = re.sub(r"^[，、；。：\s]+", "", seg).strip(" ；。")
        if not seg:
            continue
        if _is_meta_sentence(seg):
            continue
        keep.append(seg)
    out = "；".join(keep)
    out = re.sub(r"[；]{2,}", "；", out).strip("； ")
    out = _balance(out)                                 # ★ 兜底：整句丢弃会带走括号闭合（纪律 7）
    return out


def F(fields: dict, key: str) -> str:
    """取字段并净化。"""
    return sanitize((fields or {}).get(key, ""))


# ---------------------------------------------------------------------------
# 三项自检
# ---------------------------------------------------------------------------
META_PAT = re.compile(r"SC\s*\d{2,3}|EP\s*\d{2,3}|§|\.md|【推断|禁用|待裁|阻断|"
                      r"比例\s*\d+\s*[:：]|剧本|原文|原著|本卡|全卡")


def self_check(prompts_dir: str) -> dict:
    """全量提示词三项自检。返回 {meta:[…], unbalanced:[…], empty_seg:[…], files:n}"""
    meta, unbal, empty = [], [], []
    files = sorted(glob.glob(os.path.join(prompts_dir, "*", "*.txt")))
    for p in files:
        t = io.open(p, encoding="utf-8").read()
        base = os.path.relpath(p, prompts_dir)
        for m in META_PAT.finditer(t):
            meta.append((base, m.group(0)))
        if t.count("（") != t.count("）"):
            unbal.append((base, t.count("（"), t.count("）")))
        lines = t.split("\n")
        for i, ln in enumerate(lines):
            if not re.fullmatch(r"【[^】]{2,}】", ln.strip()):
                continue
            nxt = ""
            for j in range(i + 1, len(lines)):
                if lines[j].strip():
                    nxt = lines[j].strip()
                    break
            if nxt == "" or nxt.startswith("【"):
                empty.append((base, ln.strip()))
    return {"files": len(files), "meta": meta, "unbalanced": unbal, "empty_seg": empty}


_SELFTEST = [
    # (输入, 断言：必须含 / 必须不含)
    ("", None, None),
    ("浅灰色（哑光）", "哑光", None),
    ("袖口磨毛起球（SC20 第 20 镜）", "袖口磨毛起球", "SC"),
    ("外搭（简洁（SC45 第 11 镜））", "外搭", "SC"),
    ("玉质半透【推断：剧本只写金线细细地箍着】；断过一次", "断过一次", "推断"),
    ("断口以金线箍好（§③3.1「玉」行）。", "断口以金线箍好", "§"),
    ("行政部职员→星锐董事长", "行政部职员", "董事长"),
    ("⚠️ 本卡禁用崩口二字", None, "本卡"),
    ("**素圈**无雕工", "素圈无雕工", "**"),
    ("哑光包浆；剧本未写；内壁温润", "内壁温润", "剧本"),
    ("内壁刻一串极小编号", "刻痕", "编号"),
    ("外搭（肩线与腰带", "肩线与腰带", "（"),
    ("外搭）肩线", None, "）"),
    # 回归：EN40 坑 —— 整句丢弃带走 `）` 后，不得从右截断、连正文一起删
    ("主光（约 3200K，落在风衣上；合记忆点纪律的说明）；顶灯作环境光（约 4000K，均匀）",
     "环境光", None),
    # 回归：`按…执行` 类正常描述不得误删
    ("闪回层按现代都市多年前做旧执行，暖褐降饱和", "暖褐", None),
    # 回归：正文里的视觉同名词不得触发整句丢弃
    ("椅腿刮出的浅白痕是固定标记。", "浅白痕", None),
]


def selftest() -> int:
    bad = 0
    for inp, must, mustnot in _SELFTEST:
        out = sanitize(inp)
        if must and must not in out:
            print("[FAIL] 缺 %r → %r" % (must, out))
            bad += 1
        if mustnot and mustnot in out:
            print("[FAIL] 残留 %r → %r" % (mustnot, out))
            bad += 1
        if sanitize(out) != out:
            print("[FAIL] 非幂等：%r → %r" % (out, sanitize(out)))
            bad += 1
    print("自测：%d 例，失败 %d" % (len(_SELFTEST), bad))
    return bad


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="资产卡字段净化器 + 提示词三项自检")
    ap.add_argument("--file", help="净化单个字段文件并打印结果")
    ap.add_argument("--check", help="对提示词目录做三项自检（元信息/括号/空段位）")
    ap.add_argument("--selftest", action="store_true", help="内置样例自测")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(1 if selftest() else 0)
    if a.file:
        print(sanitize(io.open(a.file, encoding="utf-8").read()))
        return
    if a.check:
        r = self_check(a.check)
        print("文件数 %d" % r["files"])
        print("元信息残留 %d %s" % (len(r["meta"]), r["meta"][:5]))
        print("括号不平衡 %d %s" % (len(r["unbalanced"]), r["unbalanced"][:5]))
        print("空段位      %d %s" % (len(r["empty_seg"]), r["empty_seg"][:5]))
        sys.exit(0 if not (r["meta"] or r["unbalanced"] or r["empty_seg"]) else 1)
    ap.print_help()


if __name__ == "__main__":
    main()
