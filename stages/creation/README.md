# stages/creation —— 短剧「创作链路」（四部分 + 转译 + 题材公式）

把「从市场调研到剧本改写」固化为**职责清晰、流程连贯、可机器校验**的四个部分，
产物全部落**工作区**，并**对接现有 agent 体系**（`tools/cli.py` 创作工具链 + `stages/production/drama.py` 执行引擎）。

```
〇 剧本转译（可选）      →  ① 榜单调研   →  ② 爆款拆解   →  ③ 选题定位 / 3.6 题材公式  →  ④ 剧本改写
script_translate.py        market_data     hit_breakdown   topic_analysis / genre_formula   script_rewrite
小说/剧本→目标语种          多市场+榜单      题材/套路/爆点    候选评分→定位卡 / 关键词→固定公式  源剧本→N 集规划
                                                                                    → tools/cli.py + drama.py
```

对应任务原文的四个交付部分：

| 部分 | 任务要求 | 本层落地 |
|---|---|---|
| **一 榜单调研与对比** | 梳理 TikTok 等爆款榜单，结合北美/东南亚/拉美横向对比，**重点西语市场** | S0：多市场 `markets` + 榜单 `rankings` + 覆盖度门禁 |
| **二 爆款拆解** | 拆解热门题材类型、剧情套路、核心爆点；总结西语内容偏好与差异化趋势 | S1：题材分布 / 套路库 / 爆点频次 / 榜单洞察 / 内容偏好 / 差异化趋势 |
| **三 选题定位** | 明确目标受众画像、题材方向与差异化卖点 | S2：候选 → 七维评分 → **定位卡**（受众画像/题材方向/差异化卖点） |
| **〇 剧本转译**（可选） | 用户给小说/剧本并指定语种时，先转译并质检合规，再进下游 | S0b：语种判定 → 术语表 → 分段转译 → 语言质检（残留/翻译腔/术语/预算） |
| **二·五 题材公式**（可选） | 固定题材反复生产时不必重跑选题 | S2.5：关键词路由 → 公式套用（`genre-plan.json`）→ **跳过 ③选题** |
| **四 剧本改写** | 把「很颠」的源剧本改写为 **40 集 × 70 秒**规划，每集含开场钩子/核心冲突/反转/集尾钩子 | S3：源剧本登记 → 逐集规划（冲突+反转+钩子+爆点节拍）→ FFS 骨架 → 自检 |

> 设计原则：**思考交给 LLM，加工与校验交给脚本**。脚本只做搬运/打分/校验/规划，不替你想选题、不写正文。

## 一、四部分职责（单一职责，越界即返工）

| 部分 | 脚本 | 产物 | 命令 |
|---|---|---|---|
| ① 榜单调研与对比 | `market_data.py` | `creation/market/market-data.json` | `market template --write` / `market check` / `market compare` / `market queries` |
| ② 爆款拆解 | `hit_breakdown.py` | `creation/01-breakdown/breakdown.json` + `.md` | `breakdown analyze` |
| ③ 选题定位·A | `topic_analysis.py` | `creation/02-positioning/candidates.json` + `.md` | `topic scaffold` / `topic score` |
| ③ 选题定位·B | `hit_selection.py` | `creation/02-positioning/positioning.json` + `.md` | `hit select --top 3` |
| ④ 剧本改写 | `script_rewrite.py` | `creation/03-script/blueprint.json` + `.md` / `script-brief.md` / `skeleton_ep_XX.md` | `script plan [--genre <id>]` / `script build --ep N --emit` / `script lint --ep N` |
| 〇 剧本转译（可选） | `script_translate.py` | `creation/03-script/source-script.<lang>.md` / `glossary.json` / `translate-plan.json` | `langs` / `detect` / `plan --to es --write` / `check --to es` / `glossary` |
| 二·五 题材公式（可选） | `genre_formula.py` | `creation/03-script/genre-plan.json` | `list` / `match "女主逆袭"` / `apply --genre female-comeback` / `check` / `doc --write` |

总入口 `creation.py` 提供 `scaffold`（初始化并对接）与 `status`（四部分一屏），并转发上述子命令。

## 二、快速开始

```powershell
PY=<python 3.10+ 解释器>
WS="D:/剧/新剧"

# 0) 初始化项目（建目录 + 拷 tools/ + 写 project.json + 生成 brief 与数据模板）
$PY stages/creation/scripts/creation.py scaffold --workspace $WS `
      --slug mi-venganza --title "Mi venganza tiene garras" --platform ReelShort --ep 40 --sec 70

# 1) 前置条件：确认模型（本地 ComfyUI / RunningHub / 闭源模型）
$PY stages/production/scripts/drama.py --workspace $WS models --ask

# 2) ① 榜单调研与对比：检索 → 回填 → 门禁 → 对比表
$PY stages/creation/scripts/creation.py --workspace $WS market queries
#    （按 prompts/s0-market-research.md 用 WebSearch 执行，回填 market-data.json）
$PY stages/creation/scripts/creation.py --workspace $WS market check
$PY stages/creation/scripts/creation.py --workspace $WS market compare

# 3) ② 爆款拆解（确定性）
$PY stages/creation/scripts/creation.py --workspace $WS breakdown analyze

# 4) ③ 选题定位
$PY stages/creation/scripts/creation.py --workspace $WS topic scaffold
#    （按 prompts/s2-topic-positioning.md 填 candidates.raw.json）
$PY stages/creation/scripts/creation.py --workspace $WS topic score
$PY stages/creation/scripts/creation.py --workspace $WS hit select --top 3

# 4.5) 〇 剧本转译（仅当源稿不是目标语种；质检不过不许进下游）
$PY stages/creation/scripts/script_translate.py --workspace $WS plan --to es --write
$PY stages/creation/scripts/script_translate.py --workspace $WS check --to es

# 4.8) 二·五 题材公式（可选：命中关键词则跳过 ③选题）
$PY stages/creation/scripts/genre_formula.py --workspace $WS match "女主逆袭"
$PY stages/creation/scripts/genre_formula.py --workspace $WS apply --genre female-comeback

# 5) ④ 剧本改写：源剧本 → 40 集规划 → 单集骨架 → 自检
#    把「很颠」的源剧本放到 $WS/creation/03-script/source-script.md
$PY stages/creation/scripts/creation.py --workspace $WS script plan            # 或 script plan --genre female-comeback
$PY stages/creation/scripts/genre_formula.py --workspace $WS check --genre female-comeback   # 公式模式的结构 QC
$PY stages/creation/scripts/creation.py --workspace $WS script build --ep 1 --emit
#    （按 prompts/s3-script-rewrite.md 填 $WS/script/ep_01.md）
$PY stages/creation/scripts/creation.py --workspace $WS script lint --ep 1
```

## 三、离线自检（零网络、零计费）

```powershell
$PY stages/creation/scripts/tests/test_creation_pipeline.py    # ①-④ 全链路 53 例
$PY stages/creation/scripts/tests/test_genre_formula.py        # 题材公式 41 例
$PY stages/creation/scripts/tests/test_script_translate.py     # 剧本转译 46 例
```

## 四、资源索引

| 资源 | 用途 |
|---|---|
| `prompts/s0-market-research.md` | ① 榜单调研与对比的检索与回填口径 |
| `prompts/s1-hit-breakdown.md` | ② 爆款拆解的写法（复盘脚本结论） |
| `prompts/s2-topic-positioning.md` | ③ 候选生成口径（七维依据必填 → 定位三要素） |
| `prompts/s3-script-rewrite.md` | ④ 正文改写口径（FFS + 40 集四件套 + **爽感强化/五拍打脸/台词大胆** + 红线） |
| `prompts/s0b-script-translate.md` | 〇 剧本转译口径（多语种规范 + 质检 + 红线） |
| `references/genre-formulas/` | 题材公式库（`female-comeback` 女频逆袭；JSON 真源 + 生成 md） |
| `references/translation-languages.json` | 转译语种注册表（9 语种：语速/口语规范/翻译腔黑名单/质检阈值） |
| `../../docs/creation/剧本强度工程.md` | 爽点结构方法论：根本原因 / 策略 / 步骤 / 质控标准 |
| `references/spanish-market.md` | 多市场横向对比基线 + 西语市场细节 + 对标剧（2026-09 数据） |
| `references/scoring-model.md` | 七维评分权重、评级档位、三维交叉口径（③A） |
| `references/module-contracts.md` | 四部分职责边界、落盘约定、数据契约、退出码 |
| `assets/market-data.template.json` | ① 市场数据 schema（markets/rankings/hits/trends） |
| `assets/creation-brief.template.json` | 项目 brief 模板 |

## 五、与现有 agent 体系的对接点

1. **创作层工具链**：`creation.py scaffold` 自动把仓库 `tools/` 拷入工作区并跑 `cli.py init`，
   产出 `project.json` / `bible/stylelock.md` / 数据库，`script/` 等目录就位。
2. **执行引擎**：④ 输出的 FFS 正文经 `tools/cli.py check/convert` 后，
   按 `data-schema.md` 整理 `data/` 四文件，交 `drama.py` 执行；**模型门禁**为首次生图/出片的前置条件。
3. **风格一致性**：④ 的 `style_lock` 取自 `docs/creation/stylelock-presets.md` 的预设，
   与引擎分镜的 `[STYLE LOCK]` 段（E011 校验）口径一致。
