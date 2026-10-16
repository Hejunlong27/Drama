#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""test_script_translate.py —— 剧本转译离线测试（零网络、零计费、零第三方依赖）

覆盖：
  * 语种注册表载入 / 别名解析（中文别名、短码、完整 code）/ 未支持语种报错
  * 源语言判定：中文 / 拉美西语 / 美式英语 / 日语 / 韩语 / 空文本
  * 分段（章节标记切分 / 无标记按字数切）
  * 字数与语速预算（char 单位与 word 单位两条路径）
  * 翻译腔黑名单编译：`~` 开头仅人工参考；`|` 变体 + 词干前缀能抓住词形变化
  * 质检：合格稿放行；中文残留 / 翻译腔 / 术语违规 → 拦（退出码 4）
  * 术语表 init / add（同 src 覆盖）/ list
  * plan：产出转译工作单与 brief；源=目标 时给出警告

用法：python test_script_translate.py     （全绿退出码 0）
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import shutil
import sys
import tempfile
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import _common as C  # noqa: E402
import script_translate as T  # noqa: E402

RESULTS: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a, **kw)
    return rc, buf.getvalue()


ZH_TEXT = (
    "第一章 落地\n"
    "苏念拖着行李箱走出机场，手机里是母亲发来的最后一条消息：别回来。\n"
    "她抬头看着这座城市的天际线，嘴角绷紧。三年前她从这里被赶走，今天她回来了。\n"
    "第二章 羞辱\n"
    "宴会厅里，顾家的长辈当着所有人的面把她的请柬撕成两半。满堂哄笑，她站着没动。\n"
)
ES_OK = (
    "Capítulo 1 — Aterrizar\n"
    "Su Nian sale del aeropuerto arrastrando la maleta. En el celular, el último mensaje de su madre: no vuelvas.\n"
    "Capítulo 2 — La humillación\n"
    "En el salón, los mayores de la familia Gu rompen su invitación delante de todos.\n"
    "—¿Y tú de quién eres? —pregunta el viejo.\n"
    "—Soy Su —dice ella. Toda la sala se ríe. Ella se queda de pie, sin moverse.\n"
)
ES_BAD = ("Su Nian sale del aeropuerto. 她站着没动。 Ella hizo una decisión. Su Niann volvió.\n")


def main() -> int:
    C.harden_stdio()
    ws = pathlib.Path(tempfile.mkdtemp(prefix="st_ws_"))
    src = ws / "source-script.md"
    src.write_text(ZH_TEXT, encoding="utf-8")
    try:
        reg = T.registry()
        check("注册表可读且 ≥9 语种", len(reg["languages"]) >= 9, str(len(reg["languages"])))
        check("注册表含 8 个目标语", all(k in reg["languages"] for k in
                                    ("es-419", "en-US", "ja-JP", "ko-KR", "pt-BR", "th-TH", "id-ID", "vi-VN")))
        check("注册表含红线与步骤", bool(reg.get("hard_redlines")) and bool(reg.get("translation_steps")))

        # ---- 语种解析 ----
        for q, want in (("es", "es-419"), ("es-419", "es-419"), ("西语", "es-419"),
                        ("西班牙语", "es-419"), ("拉美西语", "es-419"), ("en", "en-US"),
                        ("英语", "en-US"), ("日语", "ja-JP"), ("韩语", "ko-KR"), ("中文", "zh-CN")):
            code, _ = T.resolve_lang(q)
            check("语种解析 %s → %s" % (q, want), code == want, code)
        try:
            T.resolve_lang("火星语")
            check("未支持语种报错", False)
        except SystemExit:
            check("未支持语种报错", True)
        check("短码提取", T.short_code("es-419") == "es" and T.short_code("en-US") == "en")

        # ---- 源语言判定 ----
        for text, want in ((ZH_TEXT, "zh-CN"), (ES_OK, "es-419"),
                           ("The old man walked into the room and said nothing at all.", "en-US"),
                           ("彼女は空港を出て、まっすぐ家に帰った。", "ja-JP"),
                           ("그녀는 공항을 나와 집으로 돌아갔다.", "ko-KR")):
            code, conf, why = T.detect_language(text)
            check("源语言判定 → %s" % want, code == want, "%s (%.2f)" % (code, conf))
        check("空文本判定为 None", T.detect_language("")[0] is None)

        # ---- 分段 ----
        segs = T.split_segments(ZH_TEXT)
        check("按章节标记切成 2 段", len(segs) == 2, str(len(segs)))
        check("分段带段号与字数", segs[0]["seq"] == 1 and segs[0]["chars"] > 0)
        plain = "".join("段落%d。" % i for i in range(1, 600))
        check("无标记时按字数切分", len(T.split_segments(plain)) >= 2,
              str(len(T.split_segments(plain))))

        # ---- 预算 ----
        _, zh = T.resolve_lang("zh")
        _, es = T.resolve_lang("es")
        bud = T.budget(ZH_TEXT, "zh-CN", zh, es)
        check("中文按字数为单位", bud["src_units"] > 0 and "字" in bud["src_unit_label"])
        check("等效时长 > 0", bud["est_seconds"] > 0)
        check("目标语预算为正且上限 ≥ 目标", bud["target_units_max"] >= bud["target_units_target"] > 0)
        check("目标语单位显示为「词」", bud["target_unit_cn"] == "词")

        # ---- 黑名单编译 ----
        check("`~` 条目不参与自动匹配", T._blacklist_pattern("~ 仅人工参考用") is None)
        pat = T._blacklist_pattern("hac|hiz|hace una decisión（说明）")
        import re  # noqa: PLC0415
        check("黑名单能抓词形变化（hizo una decisión）",
              bool(pat) and bool(re.search(pat, "Ella hizo una decisión.", re.I)))
        check("黑名单不误伤正常句",
              not re.search(pat, "Ella tomó una decisión.", re.I))

        # ---- 质检：好稿 ----
        good = ws / "good.es.md"
        good.write_text(ES_OK, encoding="utf-8")
        _quiet(T.cmd_glossary, types.SimpleNamespace(workspace=str(ws), gcmd="init"))
        _quiet(T.cmd_glossary, types.SimpleNamespace(workspace=str(ws), gcmd="add", src="苏念",
                                                     target="Su Nian", note=None, forbidden=["Su Niann"]))
        rc, out = _quiet(T.cmd_check, types.SimpleNamespace(workspace=str(ws), to="es", file=str(good)))
        check("合格转译稿放行", rc == 0 and "[OK]" in out, "rc=%d" % rc)

        # ---- 质检：坏稿（中文残留 + 翻译腔 + 术语违规）----
        bad = ws / "bad.es.md"
        bad.write_text(ES_BAD, encoding="utf-8")
        rc, out = _quiet(T.cmd_check, types.SimpleNamespace(workspace=str(ws), to="es", file=str(bad)))
        check("坏稿被拦（退出码 4）", rc == 4, "rc=%d" % rc)
        check("检出源语言残留", "残留" in out)
        check("检出翻译腔", "翻译腔" in out and "命中" in out)
        check("检出术语违规", "术语表禁用写法" in out)

        # ---- 术语表 ----
        gl = json.loads(C.pdir(ws, C.GLOSSARY).read_text(encoding="utf-8"))
        check("术语表落盘且含条目", len(gl.get("terms") or []) == 1)
        _quiet(T.cmd_glossary, types.SimpleNamespace(workspace=str(ws), gcmd="add", src="苏念",
                                                     target="Su Nian", note="女主", forbidden=[]))
        gl2 = json.loads(C.pdir(ws, C.GLOSSARY).read_text(encoding="utf-8"))
        check("同 src 覆盖不重复", len(gl2["terms"]) == 1 and gl2["terms"][0]["note"] == "女主")

        # ---- plan ----
        rc, out = _quiet(T.cmd_plan, types.SimpleNamespace(
            workspace=str(ws), to="es", frm="auto", src=str(src), write=True))
        tp = C.pdir(ws, C.TRANSLATE_PLAN)
        brief = C.pdir(ws, C.TRANSLATION_BRIEF)
        obj = json.loads(tp.read_text(encoding="utf-8")) if tp.is_file() else {}
        check("plan 产出转译工作单", rc == 0 and obj.get("to") == "es-419")
        check("工作单记录源语判定", obj.get("from") == "zh-CN")
        check("工作单含分段清单", len(obj.get("segments") or []) >= 1)
        check("plan --write 产出 brief", brief.is_file() and "转译工作单" in brief.read_text(encoding="utf-8"))
        check("brief 含翻译腔黑名单段", "翻译腔黑名单" in brief.read_text(encoding="utf-8"))

        rc, out = _quiet(T.cmd_plan, types.SimpleNamespace(
            workspace=str(ws), to="zh", frm="auto", src=str(src), write=False))
        check("源=目标时给出无需转译警告", "无需转译" in out)

        # ---- 语种清单 / 判定命令 ----
        rc, out = _quiet(T.cmd_langs, types.SimpleNamespace())
        check("langs 列出 9 种语种", rc == 0 and out.count("●") >= 9)
        rc, out = _quiet(T.cmd_detect, types.SimpleNamespace(text=ZH_TEXT, file=None, json=True))
        check("detect --json 输出可解析", rc == 0 and json.loads(out).get("code") == "zh-CN")
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        print("  [%s] %s%s" % ("OK  " if ok else "FAIL", name,
                               "  → %s" % detail if (detail and not ok) else ""))
    print("\n共 %d 例，通过 %d，失败 %d" % (len(RESULTS), passed, len(RESULTS) - passed))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
