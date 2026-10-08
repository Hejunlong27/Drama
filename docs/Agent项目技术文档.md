# drama-agent：AI 短剧批量生产 Agent 项目 · 技术文档

- 版本：v0.1（设计稿，待确认后实施）
- 日期：2026-10-08
- 定位：把 WorkBuddy 内已跑通的短剧批量生成工作流，固化为**可独立运行、可复用**的本地 Python 项目
- 前置文档：《可行性检查报告.md》（断点编号 B0–B7、待确认事项 A1–A8 均沿用该文档）

---

## 1. 项目概述

### 1.1 目标

| 目标 | 说明 |
|---|---|
| 独立运行 | 执行层（资产出图 → 参考图接线 → 批量出片 → 拼接/放大/字幕 → 验收/结账）脱离 WorkBuddy 会话，用一条 CLI 驱动 |
| 可复用 | 一部剧 = 一个工作区目录；换剧只换 `--workspace`，引擎与编排层零改动 |
| 断点补齐 | 新增 storyboard→data 转换器（B1）与生成端门禁（B7），打通创作层到出片层 |
| 成本可控 | 计费双确认、dry-run 默认零费用、省钱闸门、taskId 级断点续跑——**全部业务规则原样继承** |
| 状态可溯 | 统一台账 `ledger.jsonl` + 整剧进度聚合视图，不修改任何现有产出 schema |

### 1.2 非目标（明确不做）

- ❌ 不重写任何引擎逻辑；不改动提示词模板、净化纪律、字幕样式口径、成本口径
- ❌ 不改造创作层（选题/世界观/剧本/分镜提示词）——混合模式下仍由 WorkBuddy 技能人工驱动（A1）
- ❌ 不做 Web UI、不做跨平台（Windows 优先，A7）
- ❌ 不把 `data/`、`output/`、API Key 写进代码仓库

### 1.3 运行模式（混合，A1 建议）

```
WorkBuddy 会话（人工质量闸）                 drama-agent（独立 CLI，可定时/可无人值守）
┌──────────────────────────────┐            ┌──────────────────────────────────────┐
│ 0 选题 short-drama-hit-topics │            │ 7  资产卡净化+出图    asset-card-*    │
│ 1 世界观+人物  ai-drama-creator│   产物落盘  │ 9  参考图接线          wire_*        │
│ 3 逐集大纲    plot-master      │ ─────────▶ │ 10 批量出片（AI应用+工作流） video engine │
│ 4 FFS 剧本    overseas-*       │  ingest门禁 │ 11 拼接+2K 放大        subtitle.py   │
│ 5 机器校验    cli.py check     │            │ 12 字幕烧录（停点问询） subtitle.py   │
│ 6 分镜/H3 提示词 3d-studio     │            │ 13 验收+成本结账       qa + ledger   │
└──────────────────────────────┘            └──────────────────────────────────────┘
```

---

## 2. 架构与模块说明

### 2.1 设计原则

1. **薄编排**：新增代码只做「调度、校验、搬运、记账」；引擎以子进程 + 固定 CLI 契约调用，业务逻辑一行不动。
2. **单真源**：引擎脚本拷贝收编进项目（A2），消除 Skill 包/项目双副本分叉（B5）。
3. **状态外置**：所有可变配置在配置文件与环境变量；代码内零硬编码路径。
4. **关卡制**：计费动作前强制停点确认，与现有 G0–G6 关卡一一对应。

### 2.2 项目目录结构

```
drama-agent/
├── drama_agent/                      # ★ 编排层（新增，薄）
│   ├── cli.py                        # 唯一入口：drama <command>（argparse）
│   ├── settings.py                   # 配置加载：configs/settings.json + 环境变量 + --workspace 覆盖
│   ├── workspace.py                  # 工作区解析（--workspace > DRAMA_WORKSPACE > ./）与目录树初始化
│   ├── state.py                      # 编排状态视图：只读聚合 project.json / output/*/state.json → progress.json
│   ├── ledger.py                     # 统一台账：ledger.jsonl（追加式事件）+ cost 汇总
│   ├── gates.py                      # 关卡确认点：计费双确认、字幕停点（免费步骤默认、计费必确认）
│   ├── scheduler.py                  # 段级并发调度：≤5/账号、重试上限+冷却、批次对账
│   └── adapters/
│       └── channel_rh.py             # RunningHub 标准通道适配（AI 应用 + 工作流）
│       └── media.py                  # ffmpeg / Topaz 调用包装（含 TVAI_* 探测）
│
├── stages/                           # ★ 环节脚本（收编 + 少量新增）
│   ├── convert_storyboard.py         # 🆕 B1：storyboard/H3 提示词 md → data/ 四文件（严格按 data-schema.md）
│   ├── ingest_check.py               # 🆕 B7：生成端门禁（<d> 齐全 / Picture 槽≤6 / duration↔时间戳一致 / 守卫表齐）
│   ├── assets/                       # 收编自 asset-card-image-prompter：sanitize.py / prompt_templates.py / tests/
│   ├── refs/                         # 收编：wire_new_assets.py / wire_episode_refs.py / prepare_refs.py
│   ├── video/                        # 收编自 ai-video-pipeline：scripts/（run.py/doctor.py/configure.py/
│   │                                 #   check_billing.py/report.py/config_layer.py + engine/ 四件套）
│   ├── subtitle/                     # 收编自 drama-subtitle-pipeline：subtitle.py / upscale.py（单真源）
│   └── qa/                           # 收编：_qa_subtitle_frame.py / test_safe_area.py / episode_preflight.py
│
├── tools/                            # 收编自 ai-drama-creator：cli.py + core/（确定性工具链，零依赖）
├── configs/
│   ├── settings.example.json         # 全局配置模板（复制为 settings.json 使用）
│   └── channel.rh.example.json        # RunningHub 标准通道：工作流/AI 应用 ID 与节点映射（对应 config.local.json 内容）
│   ├── subtitle.example.json         # = 原 config.subtitle.example.json（样式不动）
│   ├── upscale.example.json          # = 原 config.upscale.example.json（Topaz 参数不动）
│   └── guards.example.json           # 道具身份守卫表模板（guards.<项目>.json）
├── templates/workspace/              # 工作区初铺模板：data/ 四文件样例 + README（原 workspace-template）
├── tests/                            # 离线测试集合（零网络零计费）
│   ├── test_prompter.py              # 收编（28 例）
│   ├── test_safe_area.py             # 收编（三画布×两语言+负向篡改）
│   └── test_convert_storyboard.py    # 🆕 转换器回归：真实剧集样例 → 四文件逐字段比对
└── docs/
    ├── 可行性检查报告.md
    └── Agent项目技术文档.md           # 本文档
```

### 2.3 模块职责表

| 模块 | 职责 | 来源 | 改动程度 |
|---|---|---|---|
| `drama_agent/cli.py` | 子命令分发、全局 `--workspace`、退出码约定 | 新增 | 🆕 |
| `drama_agent/settings.py` | 三层配置合并（默认 < settings.json < 环境变量）；Key 一律打码展示 | 新增 | 🆕 |
| `drama_agent/state.py` | 聚合创作层 `project.json`（state.step / ep_status）与出片层 `output/*/state.json`，生成 `progress.json`；**只读，不改源文件** | 新增 | 🆕（补 B4） |
| `drama_agent/ledger.py` | 追加式事件日志（submit/usage/gate_confirm/error/retry…）+ 按 batch/集/段汇总；口径仍以平台 `usage` 为准 | 新增 | 🆕（补 O4） |
| `drama_agent/gates.py` | G3/G5 计费确认、G6 汇报契约、⑦ 字幕停点问询——确认话术沿用原 Skill 模板 | 新增 | 🆕 |
| `drama_agent/scheduler.py` | 账号→槽位建模（1 账号=5 槽）、队列派单、失败段重试上限+冷却、跨批次对账（`--batch-id`） | 新增（吸收 supervise.py 实测结论） | 🆕 |
| `stages/convert_storyboard.py` | 解析 H3 六段式提示词 md（`<Picture N>` / `<d>` / `[Shot N] At MM:SS` / 守卫表引用）→ 生成 `data/` 四文件；**只做搬运不改一字提示词** | 新增 | 🆕（补 B1） |
| `stages/ingest_check.py` | 门禁：提示词非空、`<d>` 台词齐全、`<Picture N>` 连续且 ≤6、`duration` 与时间戳总时长一致、参考图 key 存在、守卫表覆盖全部道具 | 新增 | 🆕（补 B7） |
| `stages/video/` | 出片引擎（并发≤5、断点续跑、熔断、probe、计费预估） | ai-video-pipeline | ✅ 原样收编 |
| `stages/subtitle/` | CTC 对齐→ASS→无损拼接→Topaz 2K→烧录→七道断言；先放大后烧录、跑完即停 | drama-subtitle-pipeline | ✅ 原样收编（单真源） |
| `stages/assets/` | 资产卡解析→净化（14 条纪律）→三类模板→三项自检→AI 应用提交契约 | asset-card-image-prompter | ✅ 原样收编 |
| `stages/refs/` | 参考图落位接线（`<Picture N>` ↔ `character_refs` 顺序） | short-drama-production | ✅ 原样收编 |
| `stages/qa/` | 字幕像素级验收、安全区自测、集级前置体检 | short-drama-production / drama-subtitle-pipeline | ✅ 原样收编 |
| `tools/` | 创作层确定性工具链（init/import-bible/parse/check/convert/reindex/report/set-anchor） | ai-drama-creator | ✅ 原样收编 |

### 2.4 数据流转

```
WorkBuddy 创作会话
   │ 落盘（人工/技能产物）
   ▼
<workspace>/
├── project.json            ← 创作层状态（原 schema 不动）
├── bible/ script/ storyboard/ prompts/    ← 创作产物（原目录约定不动）
│
│ ① drama ingest --ep N      【门禁 ingest_check，不过不往下走】
│ ② drama convert --ep N     【convert_storyboard → data/ 四文件，data-schema.md 契约】
 ▼
├── data/                   shots.json / characters.json / scenes.json / h3_prompts.json（只读）
├── data/refs_h3/           参考图（角色/场景/道具）
│
│ ③ drama doctor / plan     【免费：体检/干跑】
│ ④ drama smoke / render    【计费双确认 → 标准通道出片 → output/<batch>/】
 ▼
├── output/
│   ├── <batch>/<shot_id>/final.mp4 + state.json + summary.json     ← 出片层状态（原 schema 不动）
│   ├── assets/characters|scenes/…                                  ← 资产参考图
│   └── subs/<ep>/…（master.mp4 / master_2k.mp4 / .ass / cues.json / report.json / 样张）
│
│ ⑤ drama assemble --ep N   【拼接+2K，跑完即停 → 问询】
│ ⑥ drama subtitle --ep N --stage render  【用户确认后烧录】
│ ⑦ drama qa / cost         【像素验收 + 台账结账】
 ▼
├── orchestrator/
│   ├── progress.json        ← 🆕 聚合视图（只读派生，可随时删除重建）
│   └── ledger.jsonl         ← 🆕 统一台账（追加式）
└── docs/成本台账_<剧名>.xlsx ← 主交付物（原口径）
```

---

## 3. 运行环境与依赖

### 3.1 硬件与系统

| 项 | 要求 |
|---|---|
| OS | Windows 10/11（PowerShell；含中文/空格路径一律加引号） |
| GPU | NVIDIA ≥8GB 显存（Topaz 2K 放大约 8–10 分钟/集，**一次只跑一集**，中断整集重跑） |
| 控制台 | GBK 纪律：脚本输出禁 emoji，用 `[OK]/[WARN]/[FAIL]`；文件读写 UTF-8 无 BOM |

### 3.2 软件依赖（全部已在本机，无新增安装）

| 依赖 | 版本/位置 | 用途 |
|---|---|---|
| Python | 3.13 venv（`C:/Users/15274/.workbuddy/binaries/python/envs/default`），已装 `httpx` + `Pillow` | 编排层 + 全部引擎；**编排层零新增第三方包**（A3） |
| ffmpeg / ffprobe | 8.1.1-full（含 libass/fontconfig/freetype） | 拼接/烧录/whisper 前处理 |
| ctc-forced-aligner | 独立 venv `C:/Users/15274/align-env`（GitHub 源码安装，勿用 PyPI） | 字幕强制对齐；需 `PYTHONUTF8=1` + `--romanize` |
| whisper.cpp | `C:/Users/15274/whisper-cli/Release/whisper-cli.exe` + `ggml-base.bin` | 逐字时间戳真值/兜底、声学覆盖检查（只吃 16k 单声道 WAV） |
| Topaz Video | 自带 tvai ffmpeg；需 `TVAI_MODEL_DIR` / `TVAI_MODEL_DATA_DIR`（权重在 D 盘，upscale.py 自动探测） | 2K AI 超分 |
| RunningHub 账号 | 双 Key：消费级（工作流/AI 应用，出图出片）+ 企业级-共享（标准模型 API，如场景空镜文生图） | 云端出图/出片 |

### 3.3 环境变量清单

| 变量 | 必填 | 说明 |
|---|---|---|
| `RUNNINGHUB_API_KEY` / `RUNNINGHUB_API_KEY_2` | 出图/收费通道 | 消费级；**AI 应用通道禁止用企业级 Key** |
| `RUNNINGHUB_ENT_API_KEY` | 标准模型 API（如场景空镜文生图） | 企业级-共享 |
| `DRAMA_WORKSPACE` | 建议 | 默认工作区（`--workspace` 参数优先） |
| `PYTHONUTF8=1` | 必须 | 中文文本处理（对齐器硬要求） |
| `TVAI_MODEL_DIR` / `TVAI_MODEL_DATA_DIR` | 放大时 | Topaz 模型定位（可写进 configs/upscale.json 兜底） |
| `AI_VIDEO_PIPELINE_SKILL` | 收编后不再需要 | 引擎已在项目内，路径由 settings.json 提供 |

---

## 4. 配置说明

### 4.1 配置分层与优先级

```
内置默认 < configs/settings.json（全局） < <workspace>/config.local.json（项目级，引擎唯一可写配置） < 命令行参数
```

- 引擎侧纪律不变：**唯一可写的运行配置是 `<workspace>/config.local.json`**（Key 回填、工作流 ID、节点映射）。
- 样式/放大参数改 `config.subtitle.json` / `config.upscale.json`，**不改代码**。

### 4.2 `configs/settings.json` 字段表（新增文件）

```json
{
  "workspace_default": "D:/剧/某剧项目",
  "python": "C:/Users/15274/.workbuddy/binaries/python/envs/default/Scripts/python.exe",
  "paths": {
    "align_env_python": "C:/Users/15274/align-env/Scripts/python.exe",
    "whisper_cli": "C:/Users/15274/whisper-cli/Release/whisper-cli.exe",
    "whisper_model": "C:/Users/15274/whisper-models/ggml-base.bin",
    "ffmpeg": "ffmpeg"
  },
  "runninghub": {
    "base_url": "https://www.runninghub.ai",
    "channel": "workflow",
    "concurrency": 5,
    "submit_gap_seconds": 2,
    "fuse": 3
  },
  "video": {
    "resolution_default": "1080p",
    "audio_mode": "native",
    "max_ref_images_per_shot": 6
  },
  "retry": {
    "max_attempts_per_shot": 3,
    "cooldown_seconds": 300,
    "transport_backoff": "builtin"
  },
  "ledger": { "path": "orchestrator/ledger.jsonl" }
}
```

> 字段仅此一张表；`concurrency=5`、`max_ref_images_per_shot=6` 是平台硬限，**不是可调优参数**，写在配置里只为显式声明。

### 4.3 项目级文件

| 文件 | 谁写 | 内容 |
|---|---|---|
| `<workspace>/config.local.json` | `configure.py`（引擎） | Key、工作流 ID、节点映射 |
| `<workspace>/video-pipeline/config.subtitle.json` | 人工/模板复制 | 字幕样式（竖屏标定：距底 400px、30 字符/行 → ~49px，见原 §九.五） |
| `<workspace>/video-pipeline/config.upscale.json` | 人工/模板复制 | Topaz 参数（`vram: 0.8` 等） |
| `<workspace>/guards.<项目>.json` | 人工（新剧必做） | 每个道具「它是什么 / 绝对不是什么」 |
| `<workspace>/project.json` | 创作工具链 | 创作层状态（原 schema） |

---

## 5. 完整执行流程

### 5.1 阶段状态机（编排层视角，集级）

```
planned → scripted(创作层) → ingested(门禁过) → converted(data/ 就位)
        → assets_ready(参考图齐) → refs_wired(接线过)
        → rendering → rendered → upscaled(2K 就绪，⏸ 停点) 
        → subtitled(可选) → delivered(验收+结账)
```

段级状态沿用引擎原样：`pending / submitted / success / failed / skipped`。

### 5.2 标准流程（一集从提示词到交付）

| 步 | 命令 | 关卡 | 计费 |
|---|---|---|---|
| ⓪ 创作层 | 在 WorkBuddy 会话完成第 0–6 环节，产物落 `<workspace>/storyboard/`（H3 六段式提示词 md） | 人工质量闸 | 免费 |
| ① 门禁 | `drama ingest --ep N` | 不过即停，列明缺陷（缺 `<d>` / 槽位超 6 / duration 不一致） | 免费 |
| ② 转换 | `drama convert --ep N` | 生成 `data/` 四文件；`--diff` 预览不落盘 | 免费 |
| ③ 体检 | `drama doctor` → `drama plan --ep N` | G0/G1：环境体检 + 干跑（将提交 N / 跳过 M / 缺哪些资产） | 免费 |
| ④ 补资产 | `drama assets --kind char\|scene --only <名单>`（干跑）→ 加 `--submit` 真跑 | G1 计费确认：清单+单价+合计 | ★计费 |
| ⑤ 接线 | `drama refs --ep N --apply` → 复跑 ③ 复检 | 悬空槽/串位清零 | 免费 |
| ⑥ 小样 | `drama smoke --ep N --only <段>` | G4：单段跑通、final.mp4>0 字节、**用户看过成片** | ★计费（1–2 次调用） |
| ⑦ 全量 | `drama render --ep N`（默认 `--resume <batch>`） | G5 计费确认②后执行；并发≤5；熔断即停 | ★计费 |
| ⑧ 拼接+放大 | `drama assemble --ep N` | 拼接母版 → Topaz 2K → **跑完即停** | 免费（GPU 独占） |
| ⑨ 字幕 | `drama subtitle --ep N --stage render` | **问过用户才做**；不要 → 交付 2K 无字幕母版 | 免费 |
| ⑩ 验收结账 | `drama qa --ep N` → `drama cost --checkpoint` | 七道断言 + 像素验收 + 台账落 xlsx | 免费 |

### 5.3 失败重试（规则全部继承，编排层只加「重试上限+冷却」）

| 失败类型 | 处置 | 依据 |
|---|---|---|
| 出片「工作流运行失败」 | 先按 transient：`drama render --resume <批次> --only <段>`，**不改提示词/配置**（实测 9.1% 全部未计费、重跑即过） | short-drama-production 铁律 6 |
| 提交成功轮询失败 | 直接 `--resume`：复用原 taskId 只轮询，不重复扣费 | ai-video-pipeline |
| 传输层异常（空错误信息） | 指数退避 6 次（8s→90s）+ `--submit-gap` 节流（2s），实施于 channel_rh 调度层 | 设计继承 rh-free-h3 v1.3.0 实测 |
| 平台限流 errorCode 1520 | 退避重试（不计入熔断）；仍失败降并发到 1 | 引擎内置（工作流通道）+ 设计继承 |
| 连续失败熔断 | 默认 3 次（`fuse`），熔断即停整批，先查排障再续跑 | 双引擎 |
| 重试上限+冷却 | 同段超过 `retry.max_attempts_per_shot`（3）→ 标 `exhausted`，冷却 300s 后才可再派；**防无限重复提交** | supervise.py 实测坑① |
| 下载截断 | <2KB 判截断，重试 4 次 | 设计继承（同上，实施于 channel_rh） |
| 资产出图 poll 失败 | create 成功但 poll 失败 → **只重试 poll，复用 taskId** | asset-card-prompter |
| 对齐失败 | 引擎三级降级（整句→标点切片→估算）；整集断言失败 = 中止交付并报告，不静默通过 | drama-subtitle |
| 加 `--resume` 纪律 | 不加 `--resume` 的全量重跑 = 重复扣费；新建批次必须显式确认 | 双引擎铁律 |

### 5.4 并发批量处理

- **段级并发**：`1 账号 = 1 worker = 5 槽`；scheduler 按「谁空派谁」自发负载均衡，队首 chunk 整批派发。
- **批次对账**：外部调度器用 `--batch-id` 强制指定批次目录名，避免「引擎自建批次名与外部台账对不上 → 在途任务被误判失败而重复提交」（实测最贵的坑）。
- **GPU 串行**：放大一次一集；渲染后体积约翻倍（91MB→236MB）。
- **创作层**（混合模式）：集与集之间人工顺序推进；API 化后（Phase 2）可集间并行。

### 5.5 任务状态记录

| 层 | 载体 | 说明 |
|---|---|---|
| 创作层 | `project.json` → `state.step` / `ep_status: draft→scripted→storyboarded` | 原 schema 不动；断点续跑第一件事 `tools/cli.py report` |
| 出片层 | `output/<batch>/state.json`（每段 status/task_id/usage/attempts）+ `summary.json`（totals/usage/fuse/failures） | 原 schema 不动；`--resume` 的依据 |
| 编排层 🆕 | `orchestrator/progress.json` | **只读聚合**三源（project.json + 各 batch state.json + subs/ 产物存在性）→「整剧一屏」：每集处于哪个阶段、哪些段失败、花了多少 |
| 台账 🆕 | `orchestrator/ledger.jsonl` | 追加式事件：`gate_confirm / submit / usage / error / retry / stage_end`；每条带 batch_id/ep/shot_id/通道/Key 指纹（打码）；汇总口径仍以平台 `usage` 为准，余额只用于判断「够不够跑」 |

---

## 6. 数据结构与接口约定

### 6.1 `data/` 四文件（出片层输入契约，**原样继承 data-schema.md**）

| 文件 | 形态 | 必填字段 | 关键规则 |
|---|---|---|---|
| `shots.json` | 数组或 `{"shots":[...]}` | `shot_id`（唯一）、`banana_prompt` | `ratio` / `crop`（默认 3×3）/ `character_refs` / `scene_refs` 可选 |
| `characters.json` | `{"char_01": "<URL\|base64\|本地路径>"}` | key 存在 | 本地路径相对 `data/` 解析，引擎自动上传换 URL（约 1 天有效，缓存 20h） |
| `scenes.json` | 同上 | 同上 | — |
| `h3_prompts.json` | `{shot_id: {...}}` | `prompt` | 缺条目直接 FAILED 不猜；`duration` / `ratio` 可选 |

校验（`--dry-run --strict`）：空表 / 缺 id / id 重复 / 缺 prompt / 引用不存在 / 本地图缺失，**缺关键字段报错让人补，不猜不编**。

### 6.2 H3 提示词内嵌约定（转换器与门禁的解析对象）

| 标签 | 约定 | 校验 |
|---|---|---|
| `<Picture N>` | 对全部 Subject 顺序编号；`character_refs[i]` 顺序喂第 i 槽 | 连续、≤6、不留空号、不追加到末尾 |
| `<d>[语言] 台词</d>` | 字幕真源，只覆盖剧本写明的台词 | 门禁查非空；音轨画外音属台词真源缺失，只报告不改写 |
| `[Shot N] At MM:SS` | 镜头切点 | 总时长必须 == `--duration`（否则模型等比压缩时间轴） |
| `【禁止项】/【强制声明】` | 段尾固定块 | 重切/转换时逐字保留（script-timing-resegment 纪律） |

### 6.3 产出物 schema（全部不动）

- `output/<batch>/summary.json`：`totals.{total,success,failed,skipped,pending}`、`usage.{consumeMoney,consumeCoins}`、`fuse.{blown,reason}`、`shots[].{shot_id,image_status,crop_status,video_status,error}`
- 字幕链：`cues.json` / `align/<shot_id>.json` / `asr/*.srt` / `<ep>.ass`（PlayRes==母版实际分辨率）/ `<ep>_upscale.json`（7 项自检）/ `report.json`
- 成本台账：`docs/成本台账_<剧名>.xlsx`（7 工作表）+ `.md`

### 6.4 新增接口约定

**统一通道适配器**（`drama_agent/adapters/channel_rh.py`，单实现可扩展——后续新引擎按同签名接入）：

```
doctor()      -> {ok, checks[], next_action}          # 零网络/零计费
plan(ws, ep)  -> {will_submit, will_skip, missing[]}  # 干跑
smoke(ws, shot_id)  -> {ok, task_id, mp4, usage}      # 需 G4 确认
run(ws, opts)       -> {batch_id, summary}            # 需 G5 确认；opts: resume/only/resolution/concurrency
status(ws, batch)   -> {shots[]}
report(ws, batch)   -> 四段式战报文本
```

**LLM 适配器**（Phase 2 预留，本期不实现）：

```
llm.complete(role_prompt, input, schema)   # OpenAI 兼容；role_prompt 复刻 3d-short-drama-studio 各角色
```

**CLI 退出码**：`0` 成功；`2` 门禁/校验不过；`3` 熔断（连续失败超限）；`4` 等待用户确认（关卡停点）；`1` 其他错误。

---

## 7. 使用示例

### 7.1 新剧初始化（混合模式）

```powershell
# 0) WorkBuddy 会话里：短剧选题 → 世界观 → FFS 剧本 → H3 六段式提示词（人工质量闸）
#    产物落在 <workspace>/storyboard/ep01_h3_prompts.md

# 1) 初始化项目工作区（编排层）
drama init --workspace "D:/剧/新剧A" --title "新剧A" --platform TikTok --ep 20 --sec 110
#   → tools/cli.py init + data/ 模板初铺 + config.*.json 模板复制

# 2) 门禁 + 转换
drama ingest  --workspace "D:/剧/新剧A" --ep 1
drama convert --workspace "D:/剧/新剧A" --ep 1 --diff     # 先看差异再落盘

# 3) 体检 → 补资产 → 接线
drama doctor --workspace "D:/剧/新剧A"
drama plan   --workspace "D:/剧/新剧A" --ep 1
drama assets --workspace "D:/剧/新剧A" --kind char --only char_01,char_02          # 干跑
drama assets --workspace "D:/剧/新剧A" --kind char --only char_01,char_02 --submit # ★计费确认后
drama refs   --workspace "D:/剧/新剧A" --ep 1 --apply
```

### 7.2 工作流通道批量出片

```powershell
drama render --workspace "D:/剧/新剧A" --ep 1 --channel workflow --resolution 1080p --yes
# 熔断/中断后续跑（taskId 级复用，不重复提交、不重复扣费）
drama render --workspace "D:/剧/新剧A" --ep 1 --channel workflow --resume <batch_id>
```

### 7.3 分段批量与单段重跑

```powershell
# 只跑指定段（分片执行 / 补缺口）
drama render --workspace "D:/剧/新剧A" --ep 2 --only ep02_seg01-ep02_seg25 --batch-id ep02_part1
# 单段重跑（transient 失败：未计费，重跑即过；taskId 级复用）
drama render --workspace "D:/剧/新剧A" --ep 2 --resume <batch_id> --only ep02_seg07
```

### 7.4 日常断点续跑与重跑

```powershell
drama status --workspace "D:/剧/新剧A"          # 整剧进度一屏（聚合视图）
drama render --resume <batch_id> --only ep02_seg07   # 单段补跑
drama assemble --ep 2                            # 拼接 + 2K，跑完即停
drama subtitle --ep 2 --stage render             # 问过用户后烧字幕
drama qa --ep 2 ; drama cost --checkpoint        # 验收 + 结账
```

### 7.5 无人值守整夜（单集）

```powershell
drama run-episode --workspace "D:/剧/新剧A" --ep 3
# 仅免计费环节可全自动；任何计费关卡仍会停（业务规则不变）
```

---

## 8. 常见问题（FAQ）

| 症状 | 根因 | 处置 |
|---|---|---|
| `未设置 RUNNINGHUB_API_KEY` | 绕开 `drama.py assets` 直跑了生成脚本（shell 继承不到用户级环境变量） | 资产一律走 `drama assets`（内部 winreg 读取注入） |
| 上传报 `code=1 API Key不存在` | 站点错（本账号在国际站） | 确认 settings.json `base_url=https://www.runninghub.ai` |
| `errorCode 1014` | 标准模型 API 用了消费级 Key | 改企业级 `RUNNINGHUB_ENT_API_KEY` |
| `errorCode 1520 并发已达上限` | 前序任务槽位未释放 | 引擎退避重试；仍失败降并发到 1 |
| `[FAIL] <段> :`（错误信息为空） | 传输层异常 | v1.3.0 已加固（退避+类型标注）；批量保持 `--submit-gap 2` |
| 成片镜头节奏与分镜不符 | `--duration` 与提示词时间戳总时长不一致 | 门禁会拦；确已入库则让 `--duration` 对齐提示词总时长 |
| 体检报「悬空图槽 / 超 6 槽」 | `<Picture N>` 缺位或超上限 | 补资产→`drama refs --apply` 重排；超 6 槽必须改提示词（删槽位改纯文字），**别只改 refs** |
| 参考图串脸/串场景 | 编号与 `character_refs` 顺序不一致 | `drama refs --ep N --apply` 重排 |
| 字幕整集没对上 / 断言中止 | 旧版引擎或整段对齐 | 确认 `stages/subtitle/` 是收编的新版（单真源，分叉已消除） |
| 字幕变豆腐块 | 字体名错/缺字符 | zh 用 `Microsoft YaHei`；es 禁 `simhei` |
| 放大报 `Model not found: prob-4` | Topaz 模型目录未定位 | upscale.py 自动探测；探测不到写进 `config.upscale.json` |
| 放大后画质没变 | 退化为纯插值 | 看 `<ep>_upscale.json` 的 `sharpness_gain_hf`/`engine` 字段，自检会判失败 |
| 跑到一半显存爆/黑帧 | 并行多集或显存被占 | 一次一集；`"vram": 0.8` |
| 找不到工作区 | 未传 `--workspace` 且无 `DRAMA_WORKSPACE` | 两者给其一；解析链 `--workspace > 环境变量 > ./` |
| 转换器报「字段缺失」 | 提示词 md 不符合内嵌约定 | 按第 6.2 节补齐标签；**转换器不猜不编** |

---

## 9. 后续扩展建议

| 阶段 | 扩展 | 说明 |
|---|---|---|
| Phase 2 | **创作层 API 化** | 接 OpenAI 兼容端点（含本地 DeepSeek，数据不出局域网）：把 3d-short-drama-studio 的 16 角色 / ai-drama-creator 各步的技能提示词固化为 `role_prompt` 模板 + JSON Schema 输出校验；集间并行；质量用现有 E001–E011 与 ingest 门禁兜底 |
| Phase 2 | 引擎插件化 | `channel_*` 适配器接口已统一，可加 ComfyUI / 可灵 / Seedance 直连等新通道，不动上游 |
| Phase 3 | 4K 放大 | CLI 路径实测后再开（8GB 显存压力大）；先在 Topaz GUI 验证 |
| Phase 3 | Web 面板 | `progress.json` + `ledger.jsonl` 已结构化，套一层只读看板即可（FastAPI/静态页），不碰引擎 |
| Phase 3 | 选题→全链自动化 | short-drama-hit-topics 接入创作层 API 化后，实现「题材→成片」整夜无人值守（计费关卡保留人工） |
| 随时 | 多语言 | 字幕链 zh/es 已验证；新语言按 `rendering-and-styling.md` 补字体/断行/字符预算即可 |
| 随时 | SkillHub 商品化 | 编排层与引擎天然分层，可按「薄壳 Pay Skill」模式把转换器/门禁/调度器打包售卖（履约走服务端） |

---

## 附录 A：模块 ↔ 源 Skill 对应与「不改动承诺」

| 项目内模块 | 收编自 | 承诺不改动 |
|---|---|---|
| `tools/` | ai-drama-creator | 七步流程、E001–E011 校验规则、project.json schema、STYLE LOCK 机制 |
| `stages/assets/` | asset-card-image-prompter | 14 条净化纪律、三类模板、三项自检口径、台账存实际提交 prompt |
| `stages/video/engine` | ai-video-pipeline | G0–G6 关卡、data-schema 契约、resume/taskId 复用、输出契约四段式 |
| `stages/refs/` | short-drama-production | `<Picture N>` 落位规则、体检项（悬空槽/超 6 槽）、成本口径（≈145/段，保守 210）、清.tmp 纪律 |
| `stages/subtitle/` | drama-subtitle-pipeline | 先放大后烧录、跑完即停、七道断言、安全区（≥200px / ≤70% 宽）、zh/es 分派 |
| `stages/qa/` | short-drama-production | 像素验收判定标准、已验证/未验证边界表述 |
| `drama_agent/`（新增） | — | 只做编排/校验/记账；不重写引擎、不改任何源 schema |

## 附录 B：实施顺序（确认 A1–A8 后）

1. 搭骨架：目录 + settings/workspace/state/ledger（1 个会话可完成）
2. 收编引擎：拷贝 + 路径改配 + `doctor` 冒烟（哈希留档）
3. 写 `convert_storyboard.py` + `ingest_check.py` + 回归测试（用《七大姑催婚》ep07 真实数据比对）
4. 接 `scheduler.py`（单账号先行）
5. 用一部旧剧全流程 dry-run 回归 → 真跑 1 集 smoke → 交付
