# 本地 ComfyUI 出图通道（零成本资产图）

> 收编自 WorkBuddy Skill `comfyui-local-imagegen`（2026-10-10）。把「资产清单 + 提示词」变成本地硬盘上的图片，
> **不走云、不花 API 钱**，只用本机 ComfyUI 的 HTTP 接口。
>
> 在《Vuelo 603》（TikTok 西语短剧）真实跑通：`assets/manifest.json` 25 项资产（11 人物 / 8 场景 / 6 道具），
> Qwen-Image 2.1 工作流单张 **约 40 秒**（RTX 4060 Laptop 8GB，2:3 / 2.2MP）。

---

## 一、在 Drama-agent 里的位置

本通道是**资产出图的第三条路**，与前两条**并存**，由 `drama.py assets --engine` 切换：

| 通道 | 命令 | 引擎 | 成本 | 适用 |
|---|---|---|---|---|
| **本地 ComfyUI**（本通道） | `drama.py assets --engine local` | 本目录 `comfy_client.py` | **￥0**（本机电费） | 批量铺量、预算敏感、隐私敏感；要求本机有 GPU + ComfyUI |
| RunningHub AI 应用 | `drama.py assets --engine rh --submit` | `gen_char_assets.py` | 人物 17–27 RH币/张 | 需要云端高质量定妆板、无本地 GPU |
| RunningHub 模型 API | `drama.py assets --engine rh --submit --kind scene` | `gen_scene_assets.py` | 场景 ≈￥0.07/张 | 场景空镜、无本地 GPU |

> **本地优先**：默认 `--engine local`。本地通道**零费用**，所以 `--submit` 对它只是「真的执行」的闸门，不涉及计费。

---

## 二、前置条件

| 条件 | 检查方式 |
|---|---|
| 本地 ComfyUI 已启动 | `python stages/assets/comfyui/comfy_client.py --check`（默认 `http://127.0.0.1:8188`） |
| 工作流含已知节点 | 见 `workflow-qwen-image21.md`「节点清单」；换工作流必须核对节点 id 或用 `COMFYUI_NODE_*` 覆盖 |
| 模型已就位 | UNet / LoRA / CLIP / VAE 与工作流 JSON 内的文件名一致（缺哪个 ComfyUI 会报错） |
| 显存够 | 8GB 可跑 2.2MP int8；显存 <6GB 建议降 `megapixels`（改工作流 JSON 的 `13` 节点） |

ComfyUI 未启动时 **不要** 尝试拉起 GUI —— 让用户自己启动，或确认后再说。

---

## 三、标准流程（四步）

| 步 | 做什么 | 命令 |
|---|---|---|
| ① | **体检**：确认 ComfyUI 在线（读 `/system_stats`，不出图、不占显存） | `python stages/assets/comfyui/comfy_client.py --check` |
| ② | **转换**：资产提示词 md → ComfyUI 清单 | `python stages/assets/scripts/asset_md.py --md <资产提示词.md> --out <工作区>/video-pipeline/data/comfyui_manifest.json` |
| ③ | **试跑一张**：先确认风格，再批量 | 干跑看清单 → `drama.py assets --engine local --only char_01` |
| ④ | **批量**：断点续跑，跳过已出图 | `drama.py assets --engine local --submit` |

> **★ 风格确认是硬关卡**：先出 **一张** 给用户看，确认通过再批量。批量 25 张约 16 分钟，返工成本高。

---

## 四、接口机制（零依赖，纯 stdlib）

```
POST /prompt           {"prompt": <工作流dict>, "client_id": "<uuid>"}   → {"prompt_id": "..."}
GET  /history/<pid>    → {"<pid>": {"outputs": {"<save节点>": {"images":[{filename,subfolder,type}]}}}}
GET  /view?filename=&subfolder=&type=output  → 图片二进制
```

- 出图是**异步**的：拿到 `prompt_id` 后轮询 `/history`，直到 `outputs` 非空或 `status.completed`。
- `node_errors` 非空 = 参数错误（提示词节点 id 错 / 模型没装），直接中止不要重试。
- 输出文件既落在 ComfyUI 的 `--output-directory`，脚本也会**另存一份**到你指定的 `--out`。

---

## 五、资产清单格式（manifest.json）

`asset_md.py` 自动生成；也可手写：

```json
{
  "style_lock": "统一风格串（会重复写进每条 prompt，保证系列一致）",
  "aspect": "2:3 (Portrait Photo)",
  "assets": [
    {
      "id": "char_01",                // 唯一 id，决定文件名前缀
      "kind": "char",                 // char | scene | prop（决定输出子目录）
      "name": "陆鸣",
      "prompt": "……电影感写实风格……",
      "seed": 12345,                  // 可选，固定种子便于复现
      "aspect": "2:3 (Portrait Photo)" // 可选，覆盖全局
    }
  ]
}
```

输出目录按 `kind` 自动分：`char → characters/`、`scene → scenes/`、`prop → props/`。
`id` 与既有云端通道同口径（`char_01` / `scene_01`），文件名形如 `char_01_陆鸣_00.png`。

---

## 六、命令速查

```bash
# 直接调引擎（不经 drama.py 时）
python stages/assets/comfyui/comfy_client.py --check
python stages/assets/comfyui/comfy_client.py --spec <manifest.json> --only char_01 --out <工作区>/video-pipeline/output/assets
python stages/assets/comfyui/comfy_client.py --spec <manifest.json> --kind char --out <工作区>/video-pipeline/output/assets --skip-existing
python stages/assets/comfyui/comfy_client.py --prompt "……" --prefix test --out <临时目录>

# 经生产总控（推荐）
python stages/production/scripts/drama.py assets --engine local            # 干跑
python stages/production/scripts/drama.py assets --engine local --check    # 只体检 ComfyUI
python stages/production/scripts/drama.py assets --engine local --only char_01
python stages/production/scripts/drama.py assets --engine local --submit   # 真的出图（￥0）
```

参数：`--spec` 清单｜`--kind {char,scene,prop}` 分类｜`--only a,b` 指定 id｜`--skip-existing` 跳过已出图｜
`--seed N`｜`--aspect "2:3 (Portrait Photo)"`｜`--count N`｜`--out DIR`｜`--check` 体检｜`--workflow 路径`。

---

## 七、红线

- ❌ **不确认风格就批量** —— 先出一张给用户看。
- ❌ 不把 `assets/`、`output/` 产物写进仓库；产物一律落工作区（`<工作区>/video-pipeline/output/assets`）。
- ❌ 不硬编码路径以外的环境假设；ComfyUI 地址用 `COMFYUI_URL` 环境变量可覆盖。
- ❌ 换工作流时**不核对节点 id** 直接跑 —— 会把提示词写进错误的节点。
- ❌ 提示词里不要塞 "1girl, masterpiece" 式英文 tag 混中文描述（Qwen-Image 吃自然语言长句）。

---

## 八、症状 → 根因 → 修法

| 症状 | 根因 | 修法 |
|---|---|---|
| `--check` 连不上 | ComfyUI 没启动 / 端口非 8188 | 启动 ComfyUI；或设 `COMFYUI_URL` |
| `node_errors` 报缺模型 | UNet/LoRA/CLIP/VAE 文件名与工作流不符 | 在 ComfyUI 里核对已装模型名，改工作流 JSON |
| 出图 40 秒变很慢 / 超时 | 显存不足或别的程序占 GPU | 关掉其他占用；降 `megapixels` |
| 图片是黑图 / 噪点 | LoRA 步数不匹配（4-step LoRA 却跑 20 步） | 保持 `steps=8 / cfg=1` |
| 日志里中文乱码 | Windows 控制台 GBK | 脚本已 `sys.stdout.reconfigure("utf-8")`；重定向日志用 UTF-8 读 |
| 批量中途断了 | 某张失败会 `!! FAILED` 但不中断整体 | 重跑同样命令 + `--skip-existing` 续跑 |
| 人物跨图不一致 | 提示词描述不足 / 未固定 seed | 补全外观细节；固定 `seed`；或用同一张图做 img2img 参考 |

---

## 九、运行环境注意（本机实操踩坑）

1. **Git Bash 环境可能损坏**（`dirname`/`mkdir` 缺失）→ 文件操作用 **PowerShell**。
2. **PowerShell 工具不回显 stdout** → 命令输出重定向到日志文件，再用 Read 读取。
3. ComfyUI 输出目录示例：`D:\Comfy-Desktop\ComfyUI-Shared\output`；输入：`...\ComfyUI-Shared\input`（**本机特有，不应写进仓库**）。
4. 脚本零第三方依赖，**managed python 直接可跑**，不需要 venv，也不需要 `RUNNINGHUB_*` 任何 Key。

---

## 十、已验证 / 未验证

**已验证**：Qwen-Image 2.1 单张 40 秒（2.2MP/2:3，RTX 4060 8GB）；25 项资产清单断点续跑；Windows 编码修复。

**未验证**：换其他工作流（Flux / SDXL / 图生图）的节点映射；多人同框的一致性；超过 100 项的长时间批量稳定性。
