# 第一部分 · 榜单调研与对比（LLM 提示词模板）

> 用途：指导会话内 LLM 完成**第一部分**的调研与对比，并把结果**结构化回填**到
> `creation/market/market-data.json`（第①部分的数据底座）。
>
> 脚本侧：`market_data.py queries` 给检索清单 → 检索 → 回填 → `market check` 过门禁 → `market compare` 出对比表。

## 任务

> 请完成 **TikTok 短剧市场研究与剧本创作** 的第一部分：
> **一、榜单调研与对比**：梳理 TikTok 当前爆款短剧榜单，结合北美、东南亚、拉美等海外市场数据做**横向对比**，
> **重点聚焦西语市场**。

## 输出要求（回填 market-data.json）

| 字段 | 填什么 | 硬要求 |
|---|---|---|
| `window` | `{from,to,months:2}` | **必须覆盖最近 2 个月**，`to` 距今天 ≤7 天 |
| `markets[]` | 多市场横向对比：北美 / 东南亚 / 拉美（西语）/ 其他 | **≥3 个市场**，且**必须含西语市场** |
| `platforms[]` | 平台（ReelShort / DramaBox / ShortTV / TikTok …） | ≥3 个 |
| `rankings[]` | 各平台/各市场**榜单条目**（名次、剧名、题材、热度、日期） | ≥8 条 |
| `hits[]` | **爆款对标剧**（西语优先） | ≥5 部 |
| `trends[]` | 趋势/热点/偏好条目（带日期与来源） | ≥6 条 |
| `audience` | 受众画像（地区/年龄/性别） | — |
| `genre_heat[]` | 题材热度（带依据） | — |
| `sources[]` | 检索来源（标题/URL/日期） | 建议保留 |

### markets 每条必填
`id / name / lang / region / downloads_share_pct / mau / monetization / top_genres[] / competition / note`

其中 `competition ∈ high|mid|low`，`downloads_share_pct` 无精确数字时写"约 X%"或"—"。

## 检索纪律

1. 按 `market_data.py queries` 的清单逐条检索（含平台榜、市场体量、偏好、合规）。
2. **不编造**：拿不到就写"—"或留空，绝不许写不存在的榜单排名与数字。
3. 每条 `hits`/`rankings`/`trends` 都要带**日期与来源**（否则覆盖度与新鲜度校验会拦）。
4. 重点市场（西语）要多给条目：西语一套内容覆盖十余国，是本期对比的核心结论来源。

## 交付措辞（写进最终报告）

在报告里给出**一段横向对比结论**：北美（付费能力最强）/ 东南亚（体量最大）/ 拉美西语（增量最快、翻译成本最低）
在「体量、变现、题材偏好、竞争度」上的差异，并指出**西语市场为什么值得重点做**。
