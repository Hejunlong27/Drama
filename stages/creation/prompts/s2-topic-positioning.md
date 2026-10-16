# 第三部分 · 选题定位（LLM 提示词模板）

> 用途：指导会话内 LLM 产出**原始候选池** `creation/02-positioning/candidates.raw.json`，
> 交 `topic_analysis.py score`（③A）打分与萃取定位三要素，再由 `hit_selection.py select`（③B）敲定定位卡。
>
> 「思考」由 LLM 负责；「打分 / 交叉 / 去重 / 排期 / 定位卡」由脚本负责。
>
> ⚠️ **本环节可被「题材公式」跳过**（2026-10-16）：当用户输入命中已收录题材关键词（如「女主逆袭」）时，
> 走 `genre_formula.py apply --genre <id>` → `script_rewrite.py plan --genre <id>`，
> **S2a/S2b 不跑**。未命中时本环节照旧。见 `docs/creation/剧本强度工程.md` §六。

## 任务

> **三、选题定位**：明确**目标受众画像、题材方向与差异化卖点。**

前置：第①部分（`market check` 通过）与第②部分（`breakdown.json`）已就绪。

## 产出：candidates.raw.json（8–12 个候选）

`python topic_analysis.py scaffold` 会写出模板。每个候选**必填**：

| 字段 | 说明 |
|---|---|
| `id` | C01…（唯一） |
| `name_zh` / `name_es` | 中文名（身份+冲突 直给式）/ 西语名 |
| `logline` | ≤40 字，含主角身份、困局、钩子 |
| `conflict` | 写成「谁 vs 谁 vs 什么」的对抗结构 |
| `track` | 主赛道（用于去重，如 狼人奇幻 / 霸总 / 复仇 / 女频逆袭） |
| `audience` | 目标受众（平台画像 + 年龄/性别/城市线级 + 情绪需求） |
| `platform` | 主投平台 |
| `scores` | 七维分数（0–10）：market_heat / trend_freshness / audience_fit / platform_fit / hook_strength / differentiation / compliance |
| `score_basis` | **七维逐维给依据**（哪部剧/哪个榜单/哪条政策）—— 缺一维该候选不合格 |
| `hook_formula` | 2–3 个爽点叠加组合 |
| `differentiation_anchor` | 与同赛道头部剧的**具体**区别 |
| `selling_points` | **差异化卖点 2–3 条**（第三部分核心交付之一） |
| `freshness_evidence` | 引用①的记录 id（如 `H01`/`R03`/`T05`），**至少一条落在最近 2 个月** |
| `production_path` | 成本档位 / 周期 / AI 适配 / 系列化 |
| `risks` | 风险与合规红线 |

## 定位三要素（脚本会自动萃取成 `positioning`）

- **受众画像**：`audience` + 平台 + 市场。
- **题材方向**：`track`。
- **差异化卖点**：`selling_points` + `differentiation_anchor`。

## 硬约束

1. **七维依据必填**：`score_basis` 逐维写清；只给数字视为无效。
2. **合规一票否决**：`compliance ≤ 3` → 脚本强制 D 并置顶风险。
3. **新鲜度**：`freshness_evidence` 至少一条落在最近 2 个月，否则降级。
4. **差异性是门槛**：候选之间不得出现同一核心冲突结构；同类赛道占比 > 40% 会被脚本降级。
5. **候选数 8–12**：少于 8 说明交叉做得不够。
6. 不编造榜单排名与热度数字；引不到就用②拆解结论，并如实标注依据。

## 完成后

```bash
python topic_analysis.py score            # ③A：评分 + 交叉 + 去重 + 定位三要素
python hit_selection.py select --top 3    # ③B：多样性排期 + 对标匹配 + 定位卡
```
