# 模块职责与数据契约（四部分流程）

> 对应用户定义的交付四部分：**① 榜单调研与对比 → ② 爆款拆解 → ③ 选题定位 → ④ 剧本改写**。

## 一、职责边界（单一职责，越界即返工）

| 部分 | 模块 | 只负责 | **明确不做** |
|---|---|---|---|
| **① 榜单调研与对比** | `market_data.py` | 多市场榜单 + 横向对比（西语重点）+ 覆盖度门禁 + 检索清单 | 不做分析结论、不产选题 |
| **② 爆款拆解** | `hit_breakdown.py` | 题材类型分布 / 剧情套路库 / 爆点元素频次 / 榜单洞察 / 内容偏好 / 差异化趋势 | 不产选题、不打分、不写剧本 |
| **③ 选题定位·A** | `topic_analysis.py` | 候选池 → 七维评分 + 三维交叉 + 差异化去重 + **定位三要素**萃取 | 不写剧本；不替用户想选题（LLM 产候选） |
| **③ 选题定位·B** | `hit_selection.py` | 否决隔离 + 多样性排期 + 对标匹配 + **定位卡**（受众画像/题材方向/差异化卖点） | 不新增选题（只做减法） |
| **④ 剧本改写** | `script_rewrite.py` | 源剧本登记 + N 集规划（每集冲突/反转/钩子）+ FFS 骨架 + 结构自检 | 不写正文（LLM 按 prompt 填）；不生成分镜提示词 |
| 交付对接 | `<工作区>/tools/cli.py` | init / import-bible / parse / check / convert | 不涉及出图出片 |
| 执行引擎 | `stages/production/drama.py` | 模型门禁 → 资产 → 接线 → 出片 → 字幕 → 验收 | 不碰创作层 |

> 创作层**思考**（写什么）由会话内 LLM 承担；本层脚本承担**确定性的加工/校验/搬运**。
> 每个环节都有配套 LLM 提示词：`prompts/s0-market-research.md` / `s1-hit-breakdown.md` /
> `s2-topic-positioning.md` / `s3-script-rewrite.md`。

## 二、落盘约定（全部在**工作区**，不进仓库）

```
<工作区>/
├── project.json / bible/ / script/ / storyboard/     # 创作产物（tools/cli.py 约定）
├── model-plan.json                                   # ★ 模型选择确认单（drama.py models 写）
├── creation/
│   ├── brief.json                                    # 项目输入（平台/形态/受众/集数…）
│   ├── market/market-data.json                       # ① 榜单 + 多市场对比（覆盖度门禁）
│   ├── 01-breakdown/
│   │   ├── breakdown.json                            # ② 拆解结论
│   │   └── breakdown.md
│   ├── 02-positioning/
│   │   ├── candidates.raw.json                       # LLM 产出（按 prompts/s2）
│   │   ├── candidates.json / candidates.md           # ③A 评分 + 交叉 + 定位三要素
│   │   └── positioning.json / positioning.md         # ③B 定位卡（受众/题材方向/差异化卖点）
│   └── 03-script/
│       ├── source-script.md                          # 待改写的「很颠」源剧本（人工放入）
│       ├── reference-structure.json                  # 对标结构（可选）
│       ├── blueprint.json / blueprint.md             # ④ N 集规划（默认 60）
│       ├── script-brief.md                           # 给 LLM 的改写指令书
│       └── skeleton_ep_XX.md                         # FFS 骨架
└── video-pipeline/                                   # 引擎侧数据契约（data/ output/ ledger/）
```

## 三、模块间数据契约

### ① → ②（市场数据 → 拆解）

`market-data.json` 关键字段（完整 schema 见 `assets/market-data.template.json`）：

| 字段 | 用途 |
|---|---|
| `window.{from,to}` | 覆盖度门禁（跨度 ≥55 天；右端 ≤7 天前） |
| `markets[]` | 多市场横向对比；**必须含西语市场**（否则①不通过） |
| `rankings[]` | 榜单条目（平台/市场/名次/题材/日期）→ ② 榜单洞察与市场偏好 |
| `hits[]` | 爆款对标剧（`id/track/tags/added`）→ ② 套路/爆点拆解、③B 对标匹配 |
| `trends[]` | 趋势条目（`id/dim/date`）→ ③ 三维交叉的 trend/preference 来源 |

② 前置：`market_data.check` 必须**通过**（近 2 个月 + 多市场 + 含西语市场）。

### ② → ③（拆解 → 定位）

`breakdown.json` 的 `tropes[]` / `boom_elements[]` / `content_preferences[]` 用于：
- ③A：把「市场高频套路」作为定位卖点的校准依据（写入候选的 `positioning.market_hot_tropes`）；
- ③B：判定定位是「顺势（踩中已验证套路）」还是「差异化（走长尾切口）」。

### ③A → ③B（候选 → 定位卡）

`candidates.json` 每个候选含 `scores/score_basis/total/grade/veto/cross/positioning/status`
（`status ∈ kept|demoted|vetoed|error`）。③B 只消费 `status == "kept"`。

### ③B → ④（定位卡 → 改写）

`positioning.json`：`selected[] = {rank, title, audience_profile, genre_direction,
differentiation_selling_points, match_hits, breakdown_alignment, differentiation_redlines}`。
④ 默认取 `rank == 1`（可 `--rank N`）。

### ④ → 引擎（剧本 → 出片）

`blueprint.json` 提供 `total_ep` / `style_lock` / `episodes[]`；FFS 正文经 `tools/cli.py check/convert` 后，
按 `stages/video/workflow/references/data-schema.md` 整理 `data/` 四文件，交 `drama.py` 执行。

## 四、模型选择门禁（跨创作层与执行层的前置条件）

- **每个项目，首次生图与首次出片前**必须确认模型选择：**本地 ComfyUI / RunningHub / 闭源模型**。
- 确认结果落 `<工作区>/model-plan.json`；`drama.py assets --submit` 与 `drama.py video run --yes`
  会先查此单，未确认即停（退出码 4）。
- 确认了 A 却跑 B（如确认本地却 `--engine rh`）→ 同样拦下，防「确认一套、跑另一套」。

## 五、退出码约定（创作层与执行层统一）

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 其它错误 |
| 2 | 校验/门禁不过（数据覆盖度、结构自检、参数非法） |
| 3 | 熔断 / 计费保护触发 |
| 4 | **等待用户确认**（模型选择门禁等关卡停点） |
