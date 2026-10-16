# 〇 · 剧本转译（LLM 提示词模板）

> 用途：用户给一部**小说或剧本**并指定**目标语种**时，先转译、再质检，通过后才交给 ④剧本改写。
> 指令来源：`stages/creation/references/translation-languages.json`（语种注册表，**唯一真源**）。
> 确定性部分由 `stages/creation/scripts/script_translate.py` 完成（判定、分段、预算、术语表、质检）。

## 何时触发

- 用户给的小说/剧本**不是**目标市场语言（如中文小说要做西语/英语/日语/韩语短剧）。
- 用户明确说「翻译成 X 语」「转译」「本地化」。
- 目标语种未给 → **必须先问用户**，不许猜。

## 任务

> **〇 剧本转译**：把源稿转译为**目标语种的地道口语**，符合该语种表达规范与本地文化，
> 然后交付给 ④剧本改写。**只换语言与本地化表达**：不增删情节、不改人设、不改钩子、不改镜序。

## 前置（脚本已做完，直接读）

```bash
python stages/creation/scripts/script_translate.py --workspace <工作区> langs        # 看支持语种
python stages/creation/scripts/script_translate.py --workspace <工作区> plan --to es --write
# → 产出 03-剧本/translate-plan.json 与 03-剧本/translation-brief.md（含语种规范、预算、分段清单）
```

源稿默认读 `03-剧本/source-script.md`；`--src` 可指定别处。

## 转译方式（七步，顺序不可乱）

1. **确认语种**：源语种未给时先 `detect`；判定不确定（置信度低）**必须问用户**。
2. **固化术语表**：`glossary init` → `glossary add --src 苏念 --target "Su Nian" --forbidden "Su Niann"`。
   人名/地名/机构/专有名词**全剧唯一写法**，先定再译。
3. **分段转译**：按 brief 里的分段清单逐段译，**保留段号**便于对照与回退。
4. **本地化改写（不是直译）**：俚语、笑话、文化梗替换为目标语观众可懂的**等价表达**；
   读 brief 里该语种的 `oral_rules` / `localization`。
5. **台词预算**：目标语篇幅不得超过 brief 给出的上限；**超了改写，不许加速语速**。
6. **称谓成体系**：敬语/人称/亲属称谓必须与角色关系一致（西语 tú/usted、日语常体/敬体、
   韩语 -습니다체/-요체/반말、越南语 anh/em/chị），**同一关系不得漂移**。
7. **写出并自检**：写入 `03-剧本/source-script.<lang>.md`（如 `source-script.es.md`），然后跑质检。

## 各语种要点（详见注册表）

| 语种 | 语速口径 | 最容易翻车的地方 |
|---|---|---|
| `es-419` 拉美西语 | 3.5–4.0 词/秒 | 用西班牙本土词（coche/móvil/ordenador）；疑问感叹句漏掉倒置符号 `¿ ¡`；`hacer una decisión` 类英语直译 |
| `en-US` 美式英语 | 2.8–3.2 词/秒 | 中式语序（`I very like`）；英式拼写混入 |
| `ja-JP` 日语 | 4.5–5.5 字/秒 | 文体（常体/敬体）与角色关系不符；`あなた` 直用；`〜することができます` 滥用 |
| `ko-KR` 韩语 | 4.0–5.0 音节/秒 | 敬语阶混乱；`당신` 当第二人称；中文语序直译 |
| `pt-BR` / `id-ID` / `vi-VN` / `th-TH` / `zh-CN` | 见注册表 | 用欧洲葡语 / 书面印尼语 / 人称代词与年龄不符 / 泰语语气词性别错 |

## 红线（违反即返工）

见注册表 `hard_redlines`，核心五条：

1. **不直译**：逐词对译、语序照搬一律返工。
2. **不混语**：转译稿里不得残留源语言整句（专有名词音译除外）。
3. **不改剧情**：不增删情节、不改人设、不改钩子。
4. **术语一致**：同一人/物/机构全剧一种写法，出现第二种即失败。
5. **不越合规**：本地化后仍须满足目标市场合规红线（`docs/compliance/发布前合规预检清单.md`）。

## 交付自检（全绿才允许进下游）

```bash
python stages/creation/scripts/script_translate.py --workspace <工作区> check --to es
```

四项确定性检查：**① 目标语特征字符占比 ② 残留源语言字符占比 ③ 翻译腔黑名单 ④ 术语一致性**（+ 篇幅预算）。
未过 → 退出码 **4**，**禁止进入 ④剧本改写**。

## 交付物

| 文件 | 内容 |
|---|---|
| `03-剧本/source-script.md` | 源稿（原语言，不动） |
| `03-剧本/source-script.<lang>.md` | 转译稿（下游 ④改写 的输入） |
| `03-剧本/glossary.json` | 术语表（全剧唯一译法） |
| `03-剧本/translation-brief.md` | 转译工作单（语种规范 + 预算 + 分段） |

## 进入下游

转译质检通过后，把 `source-script.<lang>.md` 作为 ④剧本改写的源稿：

```bash
# 常规路线：③选题 → ④改写
python stages/creation/scripts/script_rewrite.py --workspace <工作区> plan
# 或题材公式路线（跳过选题）
python stages/creation/scripts/genre_formula.py --workspace <工作区> apply --genre female-comeback
python stages/creation/scripts/script_rewrite.py --workspace <工作区> plan --genre female-comeback
```
