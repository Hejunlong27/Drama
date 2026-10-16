# SoloDrama

**一个人，就是一个剧组。**

> **宗旨：一个人、一天时间、一台电脑、N部短剧！！！**
>
> 用得起、算得清、跑得稳：资产出图默认走**本地 ComfyUI（￥0）**，云端出片只走 RunningHub 官方标准通道，干跑默认 + 计费双确认 + 逐笔台账。
>
> **一台家用笔记本就够**：本项目全程在一台 **RTX 4060（8G 显存）+ 16G 内存的笔记本**上开发并验证 —— 本地出图、拼接、2K 放大都在本机完成，不需要工作站、服务器或云主机。

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B%20(tested%203.13)-3776AB.svg)](requirements.txt)

AI 短剧批量生产的**本地全链路工具集**：**创作**与**成片**在同一仓库里闭环。

- **创作层** `stages/creation/`：① 榜单调研与对比（多市场横向比较，西语重点）→ ② 爆款拆解（题材 / 套路 / 爆点 / 内容偏好 / 差异化趋势）→ ③ 选题定位（七维加权评分 + 定位卡）→ ④ 剧本改写（源剧本 → N 集规划）。
  其中确定性的部分——数据覆盖度门禁、评分、交叉去重、集数排期与爆点节拍、结构抽取、FFS 骨架、格式 lint——都已落成**可离线回归的脚本**；LLM 创意环节仍可留在 AI 会话中完成。
- **执行层** `stages/assets|video|subtitle|production/`：由 `data/` 四文件驱动
  **资产出图 → 逐段成片 → 整集母版 → 2K 高清 → 烧录字幕 → 像素验收 → 成本台账**，全本地 CLI，支持断点续跑、成本可控。

两层之间只有一份**只读数据契约**（`data/` 四文件）：换模型、换通道、换创作方式，另一侧零感知。

---

## 一、交付成果

### 1.1 交付清单

| 类别 | 交付内容 | 状态 |
|---|---|---|
| **本地执行引擎集** | 5 个阶段模块（创作 / 出片 / 字幕 / 总控 / 资产），共 115 个受管文件 | ✅ 已交付 |
| **统一 CLI 入口** | `stages/production/scripts/drama.py`，11 类子命令 | ✅ 已交付 |
| **西语漫剧创作链路** | `stages/creation/`（四部分，49 例离线测试） | ✅ 已交付 |
| **本地出图通道** | `stages/assets/comfyui/`（￥0 成本、零第三方依赖、不出网） | ✅ 已交付 |
| **模型选择门禁** | `stages/production/scripts/model_gate.py`（19 例离线测试） | ✅ 已交付 |
| **离线测试集合** | **131 例**离线用例（零网络零计费）+ 字幕安全区断言集，全部通过 | ✅ 全绿 |
| **完整性指纹** | `manifest.sha256`（114 条，显式排除自身） | ✅ 逐文件 0 不符 |
| **设计文档** | 可行性检查报告 + 技术文档 + 创作层方法论 5 篇 + 模块 references | ✅ 已交付 |
| **依赖锁定** | `requirements.txt`（`httpx==0.28.1` / `Pillow==12.2.0`，实测 Python 3.13） | ✅ 已交付 |
| **开源发布** | GitHub [`Hejunlong27/SoloDrama`](https://github.com/Hejunlong27/SoloDrama)，Apache-2.0 | ✅ 已发布 |

### 1.2 已交付功能矩阵

| 阶段 | 模块 | 功能 |
|---|---|---|
| **创作** | `stages/creation/` | ① 榜单调研与对比（多市场横向比较 + 西语重点）→ ② 爆款拆解（题材分布 / 套路库 / 爆点 / 内容偏好 / 差异化趋势）→ ③ 选题定位（七维加权评分 + 定位卡）→ ④ 剧本改写（源剧本 → N 集规划，默认 60 集，每集含核心冲突 / 反转 / 钩子） |
| **资产** | `stages/assets/` | 资产卡提示词净化（14 条纪律）+ 三类模板 + `md → 清单` 转换；出图双通路：**本地 ComfyUI（默认，￥0）** 与 RunningHub 云端（★计费） |
| **接线** | `stages/production/` | 参考图落位接线（`<Picture N>` ↔ refs 顺序一致）、悬空图槽 / 超 6 槽体检、参考图瘦身 |
| **出片** | `stages/video/workflow/` | RunningHub「工作流 + AI 应用」标准通道批量出片：`doctor` 体检 → 干跑计划 → 小样 → 全量；taskId 级断点续跑、并发上限 5（单账号，见下方说明）、计费预估与用量核对 |
| **成片** | `stages/subtitle/` | CTC 强制对齐 → ASS 生成 → 无损拼接母版 → Topaz 2K 放大 → 字幕烧录 → 七道质量断言 |
| **验收结账** | `stages/production/` | 字幕像素级验收、集级前置体检、成本台账（Excel 7 工作表 + markdown），口径只认平台返回的 `usage` |
| **总控** | `stages/production/scripts/drama.py` | `status / preflight / assets / refs / models / video(plan·run·status) / report / balance / cost / guide` |

### 1.3 视频通道并发上限（RunningHub）

> **RunningHub 单账号最多支持 5 个 AI 应用 API 并发。**
>
> 本引擎据此把并发固定为 **5**，并将该值写入配置（`concurrency=5`）后作为**平台硬限**显式声明——它不是可调优参数。
> 超限提交会被平台拒绝（典型表现为 `errorCode 1520 并发已达上限`，引擎内置退避重试；仍失败则降并发到 1）。
> 该上限与「默认干跑 + 计费双确认 + 断点续跑」共同构成成本控制的边界。

### 1.4 流程实战背书

以下数据来自本项目改造前的 WorkBuddy 工作流阶段（引擎同源，收编时核心逻辑零改动）：

| 实战项目 | 规模 |
|---|---|
| 《七大姑催婚》ep03–ep10 | 64 段 / 172 条字幕 / 792 秒 / 9,705 RH 币 |
| 《贤妻不装了》 | 81/81 参考图全量出图；多段并发出片 |
| 《La Novia del Enemigo》 | 西语竖屏端到端（源剧本 → 成片） |

---

## 二、关键创新

### 1. 编排层与引擎层分离，引擎「单真源」收编

- **机制**：新增代码只做「调度、校验、搬运、记账」；引擎以子进程 + 固定 CLI 契约调用，业务逻辑一行不动。原 Skill 包不再承担引擎职责，副本为唯一运行真源。
- **价值**：消除「Skill 包 vs 项目 scripts/ 双副本分叉」——该分叉历史上真实引发过一次线上事故；分层后换剧只换 `--workspace`，引擎零改动。

### 2. 本地优先出图：默认路径 ￥0

- **机制**：资产出图默认走本机 ComfyUI（`stages/assets/comfyui/comfy_client.py`），纯标准库、零第三方依赖、不出网、**不需要任何 Key**；工作流路径与五类节点 id 全部用环境变量覆盖，换工作流不改代码。
- **价值**：批量铺量不产生任何云端费用；且该通道**刻意不 import** 既有云端引擎，避免把 `httpx` / RunningHub 客户端拖进零成本链路。

### 3. 模型选择门禁：把「确认」变成不可绕过的前置条件

- **机制**：首次生图与首次出片之前，必须先确认模型（本地 ComfyUI / RunningHub / 闭源模型），确认单落盘 `<工作区>/model-plan.json`；未确认即拦（退出码 4）。
- **价值**：门禁不只是拦「未确认」，还拦「确认一套、跑另一套」——确认了本地 ComfyUI 却执行 `--engine rh`，会被要求先改确认单。杜绝默认值静默生效造成的意外计费。

### 4. 成本可控四道闸

| 闸门 | 机制 |
|---|---|
| **默认干跑** | 所有会花钱的动作默认只打印清单与预估，真实提交必须显式 `--submit` / `--yes` |
| **计费双确认** | 先小样（1 段）验证成片与单价，确认后才全量 |
| **断点续跑** | `--resume <batch_id>` 复用原 taskId，只轮询不重复提交，已生成的自动跳过 |
| **硬限显式声明** | 并发 ≤5（**RunningHub 单账号最多支持 5 个 AI 应用 API 并发**）、单镜头参考图 ≤6 写入配置并在文档标注为**平台硬限**，不作为可调优参数暴露 |

台账口径只认平台返回的 `usage`；**本地通道消耗恒为 ￥0 也照记**，保证全链路逐笔可核算。

### 5. 数据契约驱动的解耦

- **机制**：创作层与执行层之间只有 `data/` 四文件（`shots.json` / `characters.json` / `scenes.json` / `h3_prompts.json`）这一份**只读契约**，外加一份 `data-schema.md`。
- **价值**：创作侧可替换任意工作流与模型，执行侧零感知；校验走 `--dry-run --strict`，缺关键字段**报错让人补，不猜不编**。

### 6. 创作层的「确定性化」

- **机制**：把创作流程中非创意的部分——市场数据覆盖度门禁、七维加权评分、三维交叉与差异化去重、集数排期与爆点节拍、结构抽取、FFS 骨架、格式 lint——全部用**纯标准库**实现；LLM 创意环节仍留在会话工作流中。
- **价值**：可离线回归（49 例）、零网络零计费，且每条结论强制可回指到具体数据条目（`score_basis` 逐维必填）。

### 7. 开源卫生与可复现性工程

- **去硬编码手术 17 处**：个人机器路径、账号 AI 应用 ID、工作流 ID 一律改为环境变量或占位符；合规扫描（用户名 / 盘符 / 中文路径 / 16 位 ID / 密钥样式）**0 命中**，真实 Key 从不入库。
- **零新增第三方依赖**：编排层与全部新增模块仅用标准库；`requirements.txt` 只锁 `httpx` 与 `Pillow`。
- **完整性可验证**：`manifest.sha256` 逐文件指纹（显式排除自身）防篡改防漂移；`.gitattributes` 固定 `eol=lf` + 仓库级 `core.autocrlf=false`，保证跨平台字节一致。
- **数据与引擎严格分离**：`data/`、`output/`、台账、密钥一律落工作区，`.gitignore` 兜底排除。

---

## 三、实现步骤

按实际开发顺序（2026-10-08 → 2026-10-12）：

### 阶段 1 · 只读盘点与断点诊断（10-08 上午）

盘点 9 个 WorkBuddy Skill + 确定性工具链 + 实战工作区，产出《可行性检查报告》。
识别出 **B0–B7 断点**（其中 **B1「创作 → 出片无数据转换器」定为唯一 P0**）、O1–O8 优化点、A1–A8 待拍板假设。
> 产出：`docs/可行性检查报告.md`

### 阶段 2 · 架构设计（10-08 下午）

确定**混合模式**：创作层保留在 AI 会话（人工质量闸），执行层独立为本地 CLI；确定编排层 / 引擎层分层、关卡制、数据契约与退出码约定。
> 产出：`docs/Agent项目技术文档.md`

### 阶段 3 · 引擎收编与去硬编码（10-08 傍晚）

按「拷贝固化、单真源」收编引擎：`stages/{video/workflow, subtitle, production, assets}` + `tools/` + `docs/creation/`；
执行去硬编码 17 处，合规扫描 0 命中；生成全量指纹与锁定依赖；跑通离线冒烟（doctor / 净化器 / 提示词用例）。

### 阶段 4 · 出片通道收敛（10-08 深夜）

把出片能力收敛为 RunningHub **「AI 应用 + 工作流」两条官方标准通道**——判据是长期稳定、可复算、可替换。
只保留官方标准通道也意味着一条统一规则：**单账号并发上限 5**，成本与排队行为都可预期。
同步更新设计文档与配置，重建指纹；确立项目宗旨与成本底线。

### 阶段 5 · 首次开源发布（10-09）

`git push` 被本地代理隧道打成 502，改用 **PAT + GitHub Git Data API** 完成首次单提交快进推送。
> 落地：`github.com/Hejunlong27/SoloDrama`（2026-10-13 由 `Drama` 更名，旧地址自动跳转），Apache-2.0。

### 阶段 6 · 接入本地 ComfyUI 出图通道（10-10）

新增 `stages/assets/comfyui/`（纯标准库本地出图引擎）+ `asset_md.py`（md → 清单转换器）+ 生产桥 `gen_assets_local.py`；
资产出图改为**默认本地（￥0）、云端计费通道并存**。
顺带修复 3 个收编遗留真缺陷：脚本路径失效导致子命令全找不到、子脚本无法推断工作区、代理劫持 localhost 导致 502。
> 验证：新增 19 例离线测试全绿，指纹重建。

### 阶段 7 · 模型门禁 + 西语创作链路（10-12）

① 新增 `model_gate.py` 并接入两个计费入口（生图、出片），19 例测试全绿；
② 新增 `stages/creation/` 创作链路，并按交付语义重构为**四部分**：调研对比 → 爆款拆解 → 选题定位 → 剧本改写，49 例测试全绿；
③ 打通与 `tools/cli.py`（创作工具链）及 `drama.py`（执行引擎）的对接。

### 阶段 8 · 发布与一致性核验（10-12 晚）

固定行尾策略（`.gitattributes` = `* text=auto eol=lf` + 仓库级 `core.autocrlf=false`），重建指纹（114 条，排除自身）；
以 API 方式推送两个快进提交，并用远端 tree 逐 blob 比对完成核验：**tree 一致、0 字节不符、无遗漏无多余**。

---

## 四、怎么用

> **统一入口（2026-10-16 起）**：本项目现在是短剧生产的**单一编排层** ——
> 此前分散在多个 WorkBuddy Skill 里的流程、规则与门禁，已收敛为「一份总纲 + 一套预置脚本 + 一个 CLI」。
> 全流程、目录规范与硬规则见 **[`docs/orchestration/生产流程总纲.md`](docs/orchestration/生产流程总纲.md)**（单一真源）。
>
> **开跑前必须先过通道确认门禁**（每次任务开始前）：
>
> ```powershell
> python stages/production/scripts/drama.py models status   # 看生图/视频通道与「执行依据」是否齐备
> python stages/production/scripts/drama.py models set image=local_comfyui video=runninghub --workflow-id <ID>
> ```
>
> 通道四选一：**本地 ComfyUI（￥0）/ 云端 ComfyUI / RunningHub AI 应用 / 闭源模型**。
> 选了**云端 ComfyUI** 必须同时提供**服务地址 + 工作流 API 文件**；选了 **RH AI 应用**必须提供**应用地址**；
> 缺依据即停在关卡（退出码 4），不许开工。

先分清你是哪一类 —— 两条路线的入口、前置与产出完全不同：

| | **使用者**（内容创作者 / 短剧团队） | **开发者**（改造引擎 / 接新通道 / 做集成） |
|---|---|---|
| **目标** | 把手上一个故事变成可发行的成片 | 读懂并扩展引擎，让它稳定可复用 |
| **要不要写代码** | **完全不需要** —— 把两段提示词交给 AI 助手，它替你跑完 | 需要（新增模块以 Python 标准库为主） |
| **入口** | **AI 助手 + 一句话提示词**（想自己敲命令，见 4.1 折叠区） | 各 `stages/*/scripts/` 源码 + `references/` 契约 |
| **前置** | Windows 电脑 + 一个能执行命令的 AI 助手（独显 / 云端账号均可选） | 同上，且需读懂数据契约再动手 |
| **交付物** | 成片 mp4 + 字幕 + 成本台账（xlsx / md） | 代码改动 + 离线测试全绿 + 重建后的指纹 |

### 4.1 使用者路线：把仓库交给 AI 助手，你只说两句话

**不需要写代码，也不需要敲命令。** 你只要把仓库地址和下面两段提示词发给你的 AI 助手（WorkBuddy 等能读写本地文件、能执行命令的 Agent），它会完成安装、配置和全部生产步骤；你只需在几个「要不要花钱」的节点上答一句「可以」。

整条链路 Agent 会依次替你跑完：**选题 → 世界观与人物 → 剧本 → 分镜与视频提示词 → 资产出图 → 渲染出片 → 拼接与 2K → 字幕 → 验收结账**。

#### 你只需要准备这几样

| 准备 | 说明 |
|---|---|
| 一台 Windows 电脑 | Python 3.10+（**Agent 会自己装**，你不用管） |
| 一块 NVIDIA 独显（可选） | **有**：本地出图 ￥0，2K 放大建议 ≥8GB 显存（本项目实测机型：RTX 4060 8G + 16G 内存笔记本）；**没有**：全部走云端，照样能跑 |
| RunningHub 账号（可选） | 只有走云端出片时才要；**只用本地 ComfyUI 出图可以完全不申请** |
| 一个 AI 助手 | 能克隆仓库、执行命令、读写本地文件的 Agent |

#### 第 1 步：让 Agent 帮你安装

把下面整段复制给 Agent（路径可改成你想要的）：

```text
请帮我安装并初始化这个开源项目，我要用它制作 AI 短剧：
https://github.com/Hejunlong27/SoloDrama

请依次完成，每步做完告诉我结果：
1. 克隆到 D:\SoloDrama，安装 requirements.txt 里的依赖
2. 跑一遍离线自检，确认 131 例用例全绿（这一步不联网、不花钱）
3. 检查本机有没有装 ComfyUI；没装就告诉我怎么装，装好后帮我在项目里配置好
4. 检查 RunningHub 的 API Key 是否已配置成环境变量；没有就告诉我怎么申请
5. 新建一个剧项目工作区，路径用 D:\短剧\我的第一部剧
6. 全部完成后告诉我：怎么启动，以及我会被问到哪些问题

注意：任何要花钱的步骤，执行前必须先告诉我要花多少，等我同意再执行。
```

#### 第 2 步：让 Agent 帮你做剧

装好之后，把这段发给它（把故事、平台、集数换成你自己的）：

```text
用 D:\SoloDrama 帮我把下面这个故事做成一部短剧。

【故事】<把你的故事或小说粘在这里>
【平台】竖屏（TikTok / 红果风格）
【规格】10 集，每集约 90 秒

请走完整流程：选题 → 世界观与人物 → 剧本 → 分镜与视频提示词 →
资产出图 → 渲染出片 → 拼接 2K → 字幕 → 成本台账。

硬性要求：
- 所有要花钱的动作（云端出图、云端出片）先报价给我，我同意后再执行
- 出片先做 1 段小样给我看，我确认后再全量
- 中途失败或中断就用 --resume 续跑，绝不重复提交、不重复花钱
- 每做完一步告诉我：现在到哪一步、这一集花了多少、下一步预计花多少
```

#### Agent 会问你几个问题，照实答就行

| Agent 问 | 你怎么答 |
|---|---|
| 首次生图和出片用哪个模型？ | **有独显** → 本地 ComfyUI（￥0）；**没有** → RunningHub（按量付费） |
| 这几张人物定妆图满意吗？ | 满意就继续；不满意就说「第 2 张重出」 |
| 这一集出片预计花 XX，确认执行吗？ | 确认；或者说「先只跑 1 段小样」 |
| 2K 母版好了，这集要烧字幕吗？ | 要 / 不要（不要就给你无字幕的高清母版） |
| 这集收工了，要结账吗？ | 要（生成成本台账 Excel） |

#### 只要记住三条，就不会多花钱

1. **不确认不花钱** —— 没等到你点头，Agent 不会执行任何计费动作。
2. **先小样后全量** —— 先出 1 段看效果，满意了再放开整集。
3. **中断就续跑** —— 让它用 `--resume`，已完成的片段自动跳过，不重复扣费。

<details>
<summary><b>命令行速查（给愿意自己动手、不想用 Agent 的人）</b></summary>

```powershell
# 一次性：环境 + 模型确认（首次必做；未确认时一切计费动作会被拦下，退出码 4）
pip install -r requirements.txt
python stages/production/scripts/drama.py models status
python stages/production/scripts/drama.py models set image=local_comfyui video=runninghub

# ---- 每集循环：顺序执行；带 ★ 的会花钱，都需要你显式确认 ----
python stages/production/scripts/drama.py status          # 先看全盘状态（零网络）

# ⓪ 通道确认（★ 首次必做；每次任务开始前先看一遍）
python stages/production/scripts/drama.py models status
python stages/production/scripts/drama.py models set image=<local_comfyui|cloud_comfyui|runninghub|closed_source> \
        video=<local_comfyui|cloud_comfyui|runninghub|runninghub_ai_app|closed_source> \
        [--comfy-url ...] [--comfy-workflow <导出的工作流API.json>] [--app-url ...] [--workflow-id ...]

# ⓪.5 分镜/提示词 → data/ 四文件 + 入门口禁（零费用，把错误挡在计费之前）
python stages/production/scripts/convert_storyboard.py --workspace <工作区> --ep ep01            # 干跑
python stages/production/scripts/convert_storyboard.py --workspace <工作区> --ep ep01 --apply    # 真写
python stages/production/scripts/ingest_check.py      --workspace <工作区> --ep ep01
python stages/production/scripts/script_timing_check.py --workspace <工作区> --ep ep01          # 台词时长审计（4.2 字/秒）

# ① 资产出图（★ 默认本地 ComfyUI，￥0，只花本机电与时间）
python stages/production/scripts/drama.py assets --engine local --check    # 体检 ComfyUI 是否在线
python stages/production/scripts/drama.py assets --engine local            # 干跑：只看清单，不出图
python stages/production/scripts/drama.py assets --engine local --submit   # 批量出图（先出一张看风格）
python stages/production/scripts/drama.py assets --engine local --only char_01   # 只补某一个资产

# ② 参考图接线 + 一次过体检（零费用）
python stages/production/scripts/drama.py refs --apply --write-config
python stages/production/scripts/drama.py preflight

# ③ 出片（★ 计费：先小样，看到实际用量与单价再全量）
#    RunningHub 单账号最多支持 5 个 AI 应用 API 并发（引擎已据此固定并发=5，勿超限提交）
python stages/production/scripts/drama.py video plan                       # 计划与成本预估（零费用）
python stages/production/scripts/drama.py video run --yes --limit 1        # 小样：只出 1 段
python stages/production/scripts/drama.py video run --yes --resume <批次>   # 全量
python stages/production/scripts/drama.py video status <批次>               # 进度与失败清单

# ④ 拼接 + 2K 放大 + 字幕（零费用，GPU 独占，一次只跑一集）
python stages/subtitle/scripts/subtitle.py --workspace <工作区> --ep ep03
#   → 跑到 2K 母版就【停下】问你「要不要烧字幕」；答「要」才继续
python stages/subtitle/scripts/subtitle.py --workspace <工作区> --ep ep03 --burn   # 一口气完成
python stages/subtitle/scripts/subtitle.py --workspace <工作区> --ep ep03 --preview 2   # 只出 2 段预览

# ⑤ 验收 + 结账 + 发布前合规
python stages/production/scripts/drama.py report
python stages/production/scripts/drama.py cost --checkpoint                # 收工结账，记基线
python stages/production/scripts/compliance_precheck.py --workspace <工作区> --ep ep01 --q1 no --q2 no --q3 no
python stages/production/scripts/compliance_precheck.py --workspace <工作区> --all --aigc-label yes --restricted 0 --total 30
```

> 出片缺图 / 提示词有缺陷时，`preflight` 与干跑会先拦下来 —— 把错误挡在计费之前。

</details>

### 4.2 开发者路线：环境 → 契约 → 扩展 → 提交

#### ① 环境与离线自检（零网络、零计费）

```powershell
pip install -r requirements.txt        # 只装 httpx + Pillow，无其他第三方依赖

python stages/video/workflow/scripts/run.py doctor --json
python stages/assets/scripts/sanitize.py --selftest
python stages/assets/scripts/tests/test_prompter.py
python stages/assets/scripts/tests/test_asset_md.py
python stages/production/scripts/tests/test_model_gate.py
python stages/creation/scripts/tests/test_creation_pipeline.py
# → 131 例离线用例应全绿
```

#### ② 先读契约，再读代码（改动前必看四份）

| 契约 | 位置 | 管什么 |
|---|---|---|
| 出片数据契约 | `stages/video/workflow/references/data-schema.md` | `data/` 四文件的字段与校验规则（唯一输入契约） |
| RunningHub 行为契约 | `stages/video/workflow/references/runninghub-contract.md` | 提交 / 轮询 / 计费字段 / 错误码语义 |
| 创作层模块契约 | `stages/creation/references/module-contracts.md` | 四部分的输入输出、门禁与产物结构 |
| 生产总控坑册 | `stages/production/references/pitfalls.md` | 已知坑与处置口径 |

#### ③ 常用扩展点（多数不需要改上游逻辑）

| 想做什么 | 改哪里 |
|---|---|
| 换本地出图用的工作流 | 环境变量 `COMFYUI_WORKFLOW` / `COMFYUI_NODE_*`（**改配置不改代码**） |
| 改字幕样式 / 放大参数 | 工作区里的 `config.subtitle.json` / `config.upscale.json` |
| 换站点 / 工作流 ID / 节点映射 | 工作区 `config.local.json` 或对应环境变量 |
| 加一种字幕语言 | 依 `stages/subtitle/references/rendering-and-styling.md` 补字体、断行与字符预算 |
| 接一条新出片通道 | 沿用 `channel` 适配器接口扩写；**产出 schema 不变** |
| 加一类资产（如道具） | `assets --kind prop` 已支持；提示词 md 章节约定见 `stages/assets/comfyui/references/本地ComfyUI出图.md` |

#### ④ 改动红线

- **不碰业务规则**：计费守卫、关卡停靠、断点续跑语义、产出 schema 一律不动。
- **数据与密钥不入库**：`data/`、`output/`、台账、真实 Key 全部落工作区，`.gitignore` 兜底排除。
- **新依赖要克制**：新增模块优先用纯标准库（现有新增模块均为标准库实现）。
- **不猜数据**：缺关键字段时报错让人补，不静默编造默认值。

#### ⑤ 提交前必做

- **重建 `manifest.sha256`**：受管文件清单以 `git -c core.quotePath=false ls-files` 为唯一真源，**显式排除 `manifest.sha256` 自身**（正确条数 = 受管文件数 − 1）；格式 `sha256  *相对路径`、ASCII 排序、UTF-8 无 BOM、LF。**任何文件改动都要重建**，否则自校验出现 mismatch。
- **自校验 0 不符**：逐条比对 `manifest.sha256` 与磁盘文件后再提交。
- **行尾**：`.gitattributes` 已声明 `* text=auto eol=lf`；工作树不得出现 CRLF 文件。

## 目录结构

```
SoloDrama/
├── stages/
│   ├── creation/          ★ 西语漫剧创作链路（① 调研对比 → ② 爆款拆解 → ③ 选题定位 → ④ 剧本改写）
│   ├── video/workflow/    批量出片引擎（RunningHub 工作流 / AI 应用）
│   ├── subtitle/          字幕引擎（CTC 对齐 → ASS → 无损拼接 → Topaz 2K → 烧录 → 七道断言）
│   ├── production/        一集总控（模型门禁 / 体检 / 补资产 / 参考图接线 / 出片调度 / 验收 / 成本台账）
│   └── assets/            资产出图：净化器 + 三类模板 + md→清单转换器 + 离线测试
│       └── comfyui/       ★ 本地 ComfyUI 出图引擎（￥0、零第三方依赖、不出网）
├── tools/                 创作层确定性工具（初始化 / 锚点入库 / 剧本校验 / 格式转换）
├── docs/                  设计文档（可行性报告、技术文档）与创作层方法论
├── configs/               配置模板（含 model-plan.example.json 模型确认单）
├── requirements.txt       锁定依赖
├── manifest.sha256        引擎文件指纹（防篡改 / 防漂移）
└── VERSIONS.md            收编来源、版本标记、改动清单
```

## 配置与密钥

- **所有平台密钥走环境变量**，代码与配置模板中不落任何真实 Key（**本地 ComfyUI 通道不需要任何 Key**）：
  - `RUNNINGHUB_API_KEY`（消费级；工作流 / AI 应用通道）
  - `RUNNINGHUB_ENT_API_KEY`（企业级-共享；仅标准模型 API 使用，如场景空镜文生图）
  - `RUNNINGHUB_BASE_URL`（站点，按账号所在站设置）
  - `SOLODRAMA_WORKSPACE`（默认工作区）、`SOLODRAMA_ASSET_MD`（资产提示词 md）
    > 旧前缀 `DRAMA_WORKSPACE` / `DRAMA_ASSET_MD` / `DRAMA_SCRIPTS_DIR` **仍然生效**（新名优先、旧名回退），已有配置无需改动。
  - `RH_CHAR_APP_ID` / `RH_SCENE_APP_ID`（账号绑定的 AI 应用 ID）
  - `COMFYUI_URL`（本地 ComfyUI 地址，默认 `http://127.0.0.1:8188`）
- 运行配置唯一可写文件是 `<工作区>/config.local.json`（模板：`stages/video/workflow/assets/config.local.example.json`）。
- 字幕样式 / 放大参数：复制 `stages/subtitle/assets/config.*.example.json` 到工作区后按需修改，**不要改代码**。
- 字幕链外部工具（ffmpeg / whisper.cpp / ctc-forced-aligner / Topaz）路径写在字幕配置的 `tools` 段，不进代码。
- 本地出图的**节点映射**（换工作流时）用 `COMFYUI_NODE_*` / `COMFYUI_WORKFLOW` 环境变量覆盖，不必改代码。

## 目录纪律

- 引擎代码与剧项目数据严格分离：`data/`、`output/`、台账一律落**工作区**，绝不写进本仓库。
- 已在 `.gitignore` 中排除数据、产物、密钥类文件；发布前请用 `manifest.sha256` 核对引擎完整性。

## 文档索引

| 主题 | 文档 |
|---|---|
| **生产流程总纲（流程 · 目录规范 · 硬规则 · 门禁）★ 单一真源** | `docs/orchestration/生产流程总纲.md` |
| **技能映射、缺口台账与重叠裁决** | `docs/orchestration/技能映射与缺口.md` |
| **发布前合规预检清单（TikTok 口径）** | `docs/compliance/发布前合规预检清单.md` |
| **台词时长审计口径（4.2 字/秒）** | `docs/creation/台词时长审计口径.md` |
| 改造背景、可行性分析 | `docs/可行性检查报告.md` |
| 架构、数据契约、执行流程 | `docs/Agent项目技术文档.md` |
| **西语漫剧创作链路（四部分）** | `stages/creation/README.md` |
| **创作层模块职责与数据契约** | `stages/creation/references/module-contracts.md` |
| **西语与多市场基线（近 2 个月）** | `stages/creation/references/spanish-market.md` |
| **七维评分模型** | `stages/creation/references/scoring-model.md` |
| 出片数据契约 | `stages/video/workflow/references/data-schema.md` |
| RunningHub API 行为契约 | `stages/video/workflow/references/runninghub-contract.md` |
| 字幕对齐 / 样式 / 放大 | `stages/subtitle/references/` |
| 生产总控坑册 | `stages/production/references/pitfalls.md` |
| 本地 ComfyUI 出图（流程 / 清单格式 / 症状表） | `stages/assets/comfyui/references/本地ComfyUI出图.md` |
| Qwen-Image 2.1 工作流节点表与换工作流适配 | `stages/assets/comfyui/references/workflow-qwen-image21.md` |
| 资产卡净化纪律 | `stages/assets/references/踩坑全录-6个真缺陷.md` |
| 创作层方法论 | `docs/creation/` |

## License

本项目以 **Apache-2.0** 协议开源，详见 [LICENSE](LICENSE)。
