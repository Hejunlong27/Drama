# VERSIONS.md —— 引擎收编清单与版本固化

- 收编日期：2026-10-08
- 收编方式：**拷贝固化**（副本为唯一运行真源，原 Skill 包不再承担引擎职责）
- 2026-10-08 更新：按新宗旨（让所有人都可以做AI短剧，每个人都可以把自己的故事拍出来！！！）决策移除免费通道（rh-free-h3-video），出片仅保留「AI 应用 + 工作流」标准通道
- **2026-10-10 更新**：新增**本地 ComfyUI 资产出图通道**（收编自 WorkBuddy Skill `comfyui-local-imagegen`），资产出图改为**默认本地（￥0）**、云端计费通道并存；同时修复两处收编遗留缺陷（`drama.py` 的脚本路径与工作区下传）
- 完整性校验：`manifest.sha256`（每文件 SHA-256，可用 `Get-FileHash` / `sha256sum -c` 核对）
- 依赖锁定：`requirements.txt`（httpx==0.28.1 / Pillow==12.2.0，实测 Python 3.13）

## 一、源 → 副本映射

| 副本路径 | 收编自 | 引擎版本标记 | 说明 |
|---|---|---|---|
| `stages/video/workflow/` | WorkBuddy Skill `ai-video-pipeline` | v1.0.0（2026-09-17） | 出片引擎：doctor / probe / configure / check_billing / run / report + `engine/` 四件套 + probe_nodes；含 references 技术契约（data-schema / runninghub-contract / config-layers / operations / troubleshooting）与 assets 模板 |

| `stages/subtitle/` | WorkBuddy Skill `drama-subtitle-pipeline` | 2026-09-20 四缺陷修复版 + 2026-10-06 安全区/竖屏标定 | `subtitle.py` + `upscale.py`（单真源）+ compare/cost 工具 + config 模板 + references |
| `stages/production/` | WorkBuddy Skill `short-drama-production` | 2026-10-08 在用版 | 总控 `drama.py`、体检 `episode_preflight.py`、资产 `gen_char_assets.py` / `gen_scene_assets.py`、接线 `wire_*.py` / `prepare_refs.py`、成本 `cost_*.py`、验收 `_qa_subtitle_frame.py` + references |
| `stages/assets/` | WorkBuddy Skill `asset-card-image-prompter` | 2026-10-05 实战版（28 例离线测试全绿） | 净化器 `sanitize.py` + 三类模板 `prompt_templates.py` + 离线测试 + references（AI 应用提交契约 / 踩坑全录） |
| `stages/assets/comfyui/` | WorkBuddy Skill `comfyui-local-imagegen` | **2026-10-10 收编** | **本地出图引擎**：`comfy_client.py`（纯标准库、零第三方依赖、不出网）+ `workflow_qwen_image21.json` 示例工作流 + references（出图流程 / 节点表与换工作流适配） |
| `stages/assets/scripts/asset_md.py` | 新增（补断点：创作产物 → 引擎输入） | **v1.0.0（2026-10-10）** | 资产提示词 md → 本地 ComfyUI 清单（纯标准库）；配套 19 例离线测试 |
| `stages/production/scripts/gen_assets_local.py` | 新增（薄桥） | **v1.0.0（2026-10-10）** | 生产总控侧本地出图桥：md → 清单 → 调本地引擎 → 产物清单 + 零成本台账 |
| `tools/` | 项目工具链 `D:\AI-Drama\parallel-me\tools` | 2026-09 版 | 创作层确定性工具：init / import-bible / parse / check / convert / reindex / report / set-anchor / gen(stub) |
| `docs/creation/` | WorkBuddy Skill `ai-drama-creator` references | 2026-09 版 | 创作层方法论：FFS 规范 / 转换规则 / QA 门 / STYLE LOCK 预设 / 流水线落盘规范 |

**未收编**（保持 WorkBuddy 技能形态）：创作层技能本体（`ai-drama-creator` / `3d-short-drama-studio` / `seedance-storyboard` / `overseas-ai-comic-script-master` / `parallel-world-writer` / `short-drama-hit-topics` 等）——它们是会话内 LLM 工作流，不属于本地引擎。

## 二、副本相对源的改动（去硬编码手术清单）

核心逻辑**零改动**；以下改动仅限「配置默认值 / 提示文案」，全部为去除个人机器路径与账号绑定 ID：

| 文件 | 改动 | 原因 |
|---|---|---|
| `stages/production/scripts/drama.py` | `AI_VIDEO_PIPELINE_SKILL` 缺省回退改为仓库相对路径 `<repo>/stages/video/workflow` | 原硬编码本机 Skill 安装路径 |
| `stages/production/scripts/gen_char_assets.py` | 同上；`DRAMA_ASSET_MD` 改为必填环境变量（缺省 fail-fast）；`RH_CHAR_APP_ID` 改为环境变量注入；`RH_CHAR_HAVE_SECTIONS` 可覆盖已生成集合；docstring/argparse 描述去 ID 化 | 原硬编码本机路径、项目专属 md 路径、账号 AI 应用 ID |
| `stages/production/scripts/gen_scene_assets.py` | ENGINE 回退仓库相对；`DRAMA_ASSET_MD` 必填；`RH_SCENE_HAVE_SECTIONS` 可覆盖；docstring 去个人路径 | 同上 |
| `stages/production/scripts/episode_preflight.py` | `DRAMA_ASSET_MD` 缺省为空（本就容缺失跳过） | 去个人路径默认值 |
| `stages/production/scripts/cost_ledger.py` | 读余额时引擎路径回退改仓库相对/环境变量 | 原硬编码本机 Skill 路径 |
| `stages/production/scripts/probe_char_app.py` | ENGINE 回退仓库相对；快照目录 `DRAMA_WORKSPACE` 优先；`RH_CHAR_APP_ID` 环境变量注入；docstring 去 ID 化 | 原硬编码本机路径与账号 ID |
| `stages/production/scripts/_check_seg.py` | 工作区改 `DRAMA_WORKSPACE` 优先 | 原硬编码工作区路径 |
| `stages/production/scripts/_ws.py`、`cost_ledger.py` | docstring 示例路径泛化 | 去个人路径（历史问题描述保留语义） |
| `stages/production/scripts/prepare_refs.py` | 缺 Pillow 提示改为通用安装指引 | 去本机解释器路径 |
| `stages/video/workflow/scripts/run.py`、`doctor.py` | 用法/体检提示文案泛化 | 去本机路径 |
| `stages/video/workflow/scripts/engine/tools/probe_nodes.py` | 用法示例工作流 ID → 占位符 | 去账号工作流 ID |
| `stages/video/workflow/references/config-layers.md`、`troubleshooting.md` | 示例 ID / 示例路径泛化 | 同上 |

| `stages/subtitle/assets/config.subtitle.example.json` | `whisper_cli` / `whisper_model` / `aligner` 改占位符 | 原为本机绝对路径（使用时复制为 config.subtitle.json 填入自己的路径） |
| `stages/subtitle/scripts/upscale_compare_frames.py` | ffmpeg 候选改 `FFMPEG_PATH` / `TOPAZ_FFMPEG_PATH` 环境变量；字体可用 `SUBTITLE_FONT_PATH` 覆盖（缺省仍为 Windows 系统雅黑） | 去本机个人路径 |
| `stages/subtitle/references/*.md`、`stages/production/references/*.md`、`stages/assets/references/runninghub-ai-app-提交契约.md` | 个人路径 / 账号 ID / webapp ID → 占位符 | 开源卫生 |
| `tools/cli.py`、`docs/creation/pipeline.md` | 用法示例路径泛化 | 去本机项目路径 |

### 2026-10-10 新增/修复（本地 ComfyUI 通道）

| 文件 | 改动 | 原因 |
|---|---|---|
| `stages/assets/comfyui/comfy_client.py` | 五类节点 id 改为 `COMFYUI_NODE_{PROMPT,PREFIX,SEED,ASPECT,LATENT}` 可覆盖；工作流路径改为 `COMFYUI_WORKFLOW` 可覆盖；新增**空代理 opener**；`--check` 失败改为友好 JSON + 退出码 1；清单级 `style_lock` 会在批量路径生效 | 从「改代码换工作流」变为「改配置换工作流」；**实测**：环境存在 `HTTP_PROXY` 时 urllib 会把 `127.0.0.1:8188` 也塞进代理，返回 `HTTP Error 502`；失败不再是原始 traceback |
| `stages/assets/comfyui/workflow_qwen_image21.json` | 节点 6 的示例提示词中性化（原为第三方动漫角色示例）；`filename_prefix` 由 `LQ-` 改为 `drama` | 开源卫生（不携带他人作品示例） |
| `stages/assets/scripts/asset_md.py` | 新增（纯标准库）。解析口径与 `gen_char_assets.py` / `gen_scene_assets.py` **同为**「`# 一、角色资产库` / `# 二、场景资产` / `# 三、道具资产`；`## N. 名字` 节内 ``` 块 = 提示词」。**刻意不 import 既有引擎**，以免把 httpx / RunningHub 客户端拖进零成本的本地通道 | 补「创作产物 → 引擎输入」断点；本地通道不得被云端依赖拖累 |
| `stages/assets/scripts/tests/test_asset_md.py` | 新增 19 例离线测试（零网络零计费） | 转换器是确定性的，应可回归 |
| `stages/production/scripts/gen_assets_local.py` | 新增（薄桥） | 生产总控侧统一入口，与云端两脚本并列 |
| `stages/production/scripts/drama.py` | ① `SCRIPTS` 由 `<工作区>/scripts` 改为**本文件所在目录**（`DRAMA_SCRIPTS_DIR` 可覆盖）；② `hydrate_env()` 下传 `DRAMA_WORKSPACE`、并把 `DRAMA_ASSET_MD` 纳入用户级环境变量补全；③ `assets` 子命令新增 `--engine {local,rh}` 与 `--check`，`--kind` 增加 `prop`；④ 状态面板聚合本地通道产物（资产不分通道，一并计入「已生成」） | **修复收编遗留缺陷**：脚本已住在仓库内（`stages/production/scripts/`），原 `<工作区>/scripts` 写法会让 `rh_doctor` / `cost_ledger` / `gen_char_assets` / `gen_scene_assets` **全部找不到**；子脚本也无法从自身位置推断工作区（脚本不在工作区里） |
| `configs/settings.example.json` | 新增 `comfyui` 段（base_url / workflow / 节点映射 / aspect 默认） | 配置单点，不硬编码 |
| `README.md`、`docs/Agent项目技术文档.md` | 新增本地通道的定位、命令、配置与模块表 | 文档同步 |

**行为不变性说明**：所有改动不触碰提交/轮询/下载/对齐/渲染/计费守卫等任何业务逻辑；在原环境设置相同环境变量时行为与源版本一致；未设环境变量时按仓库内缺省或显式报错（不再静默指向个人机器路径）。

## 三、运行时排除项（未收编）

| 项 | 原因 |
|---|---|
| `**/__pycache__`、`*.pyc` | 运行时产物 |
| `engine/.workspace`（工作区指针文件） | 内含本机工作区路径；副本缺省回退 `~/video-pipeline-workspace`，可用 `--workspace` / `VIDEO_PIPELINE_WORKSPACE` 覆盖 |
| 各 Skill 的 `SKILL.md` | WorkBuddy 会话层文档，含平台上下文；技术契约已由 `references/` 覆盖 |
| WorkBuddy Skill `rh-free-h3-video`（免费通道引擎） | **决策移除（2026-10-08）**：免费端点为限时能力、不具备可复用性；出片统一走「AI 应用 + 工作流」标准通道 |
| 创作层技能与真实剧项目数据 | 属内容资产，不入引擎仓库 |

## 四、验证方式

```powershell
# 1) 完整性
Get-FileHash -Algorithm SHA256 (Get-ChildItem -Recurse -File | Where-Object {$_.Name -ne 'manifest.sha256'}) | ...

# 或在 Git Bash / WSL：
sha256sum -c manifest.sha256

# 2) 依赖
pip install -r requirements.txt

# 3) 离线冒烟（零计费、零网络）
python stages/video/workflow/scripts/run.py doctor --json
python stages/assets/scripts/sanitize.py --selftest
python stages/assets/scripts/tests/test_prompter.py
python stages/assets/scripts/tests/test_asset_md.py

# 4) 本地出图通道（零第三方依赖，不需要任何 Key；需本机 ComfyUI 在跑）
python stages/assets/comfyui/comfy_client.py --check
python stages/production/scripts/drama.py assets --engine local          # 干跑
```
