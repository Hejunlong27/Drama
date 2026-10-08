# Drama-agent

> **宗旨：让所有人都可以做AI短剧，每个人都可以把自己的故事拍出来！！！**
> 用得起、算得清、跑得稳：标准通道 + 干跑默认 + 计费双确认 + 逐笔台账，把每一分钱花在明处。

AI 短剧批量生产的**本地执行引擎集**：从「已写好的分镜/视频提示词」到「逐段成片 → 整集母版 → 2K 高清 → 烧录字幕 → 像素验收 → 成本台账」，全链路本地 CLI、断点续跑、成本可控。
创作环节（选题/剧本/分镜提示词）保留在 AI 会话工作流中完成，产物按本仓库的数据契约落盘即可驱动引擎。

## 怎么做到「人人都能拍」（本项目的三条底线）

| 底线 | 机制 |
|---|---|
| **免费步骤默认，计费步骤确认** | 体检/干跑/探测/状态零费用可随时跑；一切计费动作（出图/出片）必须经用户显式确认，双重确认关卡写死在流程里 |
| **只接标准通道，不依赖限时能力** | RunningHub 出片只走「**工作流 + AI 应用**」两条标准通道（长期稳定、可复算、可替换）；不接入任何限时免费端点 |
| **每一笔都可核算** | 台账口径只认平台返回的 `usage`；默认 dry-run、省钱闸门（已生成不重复生成）、taskId 级断点续跑不重复扣费、并发≤5 防超支 |

## 模块总览

```
Drama-agent/
├── stages/
│   ├── video/workflow/    批量出片引擎（RunningHub 工作流/AI 应用：九宫格出图→裁剪→H3 视频）
│   ├── subtitle/          字幕引擎（CTC 强制对齐→ASS→无损拼接→Topaz 2K 放大→烧录→七道断言）
│   ├── production/        一集总控（体检/补资产/参考图接线/出片调度/验收/成本台账）
│   └── assets/            资产卡→生图提示词（净化器+三类模板+离线测试）
├── tools/                 创作层确定性工具（项目初始化/锚点入库/剧本校验/格式转换）
├── docs/                  设计文档与创作层方法论
├── configs/               配置模板
├── requirements.txt       锁定依赖
├── manifest.sha256        引擎文件指纹（防篡改/防漂移）
└── VERSIONS.md            收编来源、版本标记、改动清单
```

## 快速开始

```powershell
# 1) 环境：Windows + Python 3.10+（实测 3.13）
pip install -r requirements.txt

# 2) 离线自检（零网络、零计费）
python stages/video/workflow/scripts/run.py doctor --json
python stages/assets/scripts/sanitize.py --selftest

# 3) 初始化一个剧项目工作区
#    把创作层产出的提示词按契约整理成 data/ 四文件（契约见 stages/video/workflow/references/data-schema.md）

# 4) 体检 → 干跑（免费）
python stages/production/scripts/episode_preflight.py <集号...>
python stages/video/workflow/scripts/run.py --dry-run --strict

# 5) 出片（★ 计费动作：小样验证 → 展示实际用量与单价 → 确认后才全量）
python stages/video/workflow/scripts/run.py --limit 1              # 小样
python stages/video/workflow/scripts/run.py --resume <batch_id>    # 全量/续跑
```

## 配置与密钥

- **所有密钥走环境变量**，代码与配置模板中不落任何真实 Key：
  - `RUNNINGHUB_API_KEY`（消费级；工作流 / AI 应用通道）
  - `RUNNINGHUB_ENT_API_KEY`（企业级-共享；仅标准模型 API 使用，如场景空镜文生图）
  - `RUNNINGHUB_BASE_URL`（站点，按账号所在站设置）
  - `DRAMA_WORKSPACE`（默认工作区）、`DRAMA_ASSET_MD`（资产提示词 md）
  - `RH_CHAR_APP_ID` / `RH_SCENE_APP_ID`（账号绑定的 AI 应用 ID）
- 运行配置唯一可写文件是 `<工作区>/config.local.json`（模板：`stages/video/workflow/assets/config.local.example.json`）。
- 字幕样式 / 放大参数：复制 `stages/subtitle/assets/config.*.example.json` 到工作区后按需修改，**不要改代码**。
- 字幕链外部工具（ffmpeg / whisper.cpp / ctc-forced-aligner / Topaz）路径写在字幕配置的 `tools` 段，不进代码。

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
| 资产卡净化纪律 | `stages/assets/references/踩坑全录-6个真缺陷.md` |
| 创作层方法论 | `docs/creation/` |

## License

本项目以 **Apache-2.0** 协议开源，详见 [LICENSE](LICENSE)。
