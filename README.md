# Drama-agent

> **宗旨：让所有人都可以做AI短剧，每个人都可以把自己的故事拍出来！！！**
> 用得起、算得清、跑得稳：资产出图默认走**本地 ComfyUI（￥0）**，云端只接标准通道，干跑默认 + 计费双确认 + 逐笔台账。

AI 短剧批量生产的**本地执行引擎集**：从「已写好的分镜/视频提示词」到「资产出图 → 逐段成片 → 整集母版 → 2K 高清 → 烧录字幕 → 像素验收 → 成本台账」，全链路本地 CLI、断点续跑、成本可控。
创作环节（选题/剧本/分镜提示词）保留在 AI 会话工作流中完成，产物按本仓库的数据契约落盘即可驱动引擎。

## 怎么做到「人人都能拍」（本项目的三条底线）

| 底线 | 机制 |
|---|---|
| **本地优先，免费步骤默认** | 资产出图默认走**本地 ComfyUI（￥0，只花本机电与时间）**；体检/干跑/探测/状态零费用可随时跑；一切**计费**动作（云端出图/出片）必须经用户显式确认 |
| **云端只接标准通道** | RunningHub 出片只走「**工作流 + AI 应用**」两条标准通道（长期稳定、可复算、可替换）；不接入任何限时免费端点 |
| **每一笔都可核算** | 台账口径只认平台返回的 `usage`，**本地通道消耗恒为 ￥0 也照记**；默认 dry-run、省钱闸门（已生成不重复生成）、taskId 级断点续跑不重复扣费、并发≤5 防超支 |

## 模块总览

```
Drama-agent/
├── stages/
│   ├── video/workflow/    批量出片引擎（RunningHub 工作流/AI 应用：九宫格出图→裁剪→H3 视频）
│   ├── subtitle/          字幕引擎（CTC 强制对齐→ASS→无损拼接→Topaz 2K 放大→烧录→七道断言）
│   ├── production/        一集总控（体检/补资产/参考图接线/出片调度/验收/成本台账）
│   └── assets/            资产出图：提示词净化器 + 三类模板 + md→清单转换器 + 离线测试
│       └── comfyui/       ★ 本地 ComfyUI 出图引擎（￥0 成本、零第三方依赖、不出网）
├── tools/                 创作层确定性工具（项目初始化/锚点入库/剧本校验/格式转换）
├── docs/                  设计文档与创作层方法论
├── configs/               配置模板
├── requirements.txt       锁定依赖
├── manifest.sha256        引擎文件指纹（防篡改/防漂移）
└── VERSIONS.md            收编来源、版本标记、改动清单
```

## 资产出图的三条路（`drama.py assets --engine`）

| 通道 | 命令 | 计费 | 适用 |
|---|---|---|---|
| **本地 ComfyUI**（默认） | `assets --engine local --submit` | **￥0** | 批量铺量、预算/隐私敏感；需本机有 GPU 且 ComfyUI 在跑 |
| RunningHub AI 应用 | `assets --engine rh --submit --kind char` | 人物 17–27 RH币/张 | 要云端高质量定妆板、本机无 GPU |
| RunningHub 模型 API | `assets --engine rh --submit --kind scene` | 场景 ≈￥0.07/张 | 场景空镜、本机无 GPU |

## 快速开始

```powershell
# 1) 环境：Windows + Python 3.10+（实测 3.13）
pip install -r requirements.txt

# 2) 离线自检（零网络、零计费）
python stages/video/workflow/scripts/run.py doctor --json
python stages/assets/scripts/sanitize.py --selftest
python stages/assets/scripts/tests/test_prompter.py
python stages/assets/scripts/tests/test_asset_md.py

# 3) 初始化一个剧项目工作区
#    把创作层产出的提示词按契约整理成 data/ 四文件（契约见 stages/video/workflow/references/data-schema.md）
#    把资产提示词整理成一份 md（章节约定见 stages/assets/comfyui/references/本地ComfyUI出图.md）

# 4) 资产出图（★ 默认本地 ComfyUI，￥0；先出一张确认风格再批量）
python stages/production/scripts/drama.py assets --engine local --check     # 体检 ComfyUI 是否在线
python stages/production/scripts/drama.py assets --engine local            # 干跑：只看清单
python stages/production/scripts/drama.py assets --engine local --only char_01
python stages/production/scripts/drama.py assets --engine local --submit   # 批量出图（￥0）

# 5) 体检 → 干跑（免费）
python stages/production/scripts/episode_preflight.py <集号...>
python stages/video/workflow/scripts/run.py --dry-run --strict

# 6) 出片（★ 计费动作：小样验证 → 展示实际用量与单价 → 确认后才全量）
python stages/video/workflow/scripts/run.py --limit 1              # 小样
python stages/video/workflow/scripts/run.py --resume <batch_id>    # 全量/续跑
```

## 配置与密钥

- **所有平台密钥走环境变量**，代码与配置模板中不落任何真实 Key（**本地 ComfyUI 通道不需要任何 Key**）：
  - `RUNNINGHUB_API_KEY`（消费级；工作流 / AI 应用通道）
  - `RUNNINGHUB_ENT_API_KEY`（企业级-共享；仅标准模型 API 使用，如场景空镜文生图）
  - `RUNNINGHUB_BASE_URL`（站点，按账号所在站设置）
  - `DRAMA_WORKSPACE`（默认工作区）、`DRAMA_ASSET_MD`（资产提示词 md）
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
| 改造背景、可行性分析（含免费通道移除决策记录） | `docs/可行性检查报告.md` |
| 架构、数据契约、执行流程 | `docs/Agent项目技术文档.md` |
| 出片数据契约 | `stages/video/workflow/references/data-schema.md` |
| RunningHub API 行为契约 | `stages/video/workflow/references/runninghub-contract.md` |
| 字幕对齐/样式/放大 | `stages/subtitle/references/` |
| 生产总控坑册 | `stages/production/references/pitfalls.md` |
| 本地 ComfyUI 出图（流程/清单格式/症状表） | `stages/assets/comfyui/references/本地ComfyUI出图.md` |
| Qwen-Image 2.1 工作流节点表与换工作流适配 | `stages/assets/comfyui/references/workflow-qwen-image21.md` |
| 资产卡净化纪律 | `stages/assets/references/踩坑全录-6个真缺陷.md` |
| 创作层方法论 | `docs/creation/` |

## License

本项目以 **Apache-2.0** 协议开源，详见 [LICENSE](LICENSE)。
