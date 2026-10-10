# Qwen-Image 2.1 工作流节点表（本地 ComfyUI）

> 对应工作流文件：本目录 `workflow_qwen_image21.json`（ComfyUI 导出格式，客户端在提交前覆盖提示词/前缀/种子/画幅节点）。
> 属性：**本地桌面版 ComfyUI 0.38.2**，RTX 4060 Laptop 8GB，实测 **约 40 秒/张**（2.2MP、2:3）。
> 收编自 WorkBuddy Skill `comfyui-local-imagegen`（2026-10-10）；示例提示词已中性化，产物前缀改为 `drama`。

## 节点清单

| id | class_type | 标题 | 关键输入 | 用途 |
|---|---|---|---|---|
| `6` | `PrimitiveStringMultiline` | 提示词 | `value` | **正向提示词**（客户端改写此节点） |
| `11` | `TextEncodeQwenImage21` | Text Encode Qwen Image 2.1 | `prompt←6`, `clip←8`, `negative_prompt=""`, `resolution=0` | 文本编码 |
| `8` | `CLIPLoader` | 加载CLIP | `clip_name=qwen3vl_8b_int8_convrot.safetensors`, `type=qwen_image` | 文本编码器 |
| `14` | `UNETLoader` | UNet加载器 | `unet_name=qwen_image_2.1_int8_convrot.safetensors` | 主模型 |
| `17` | `LoraLoaderModelOnly` | LoRA加载器（仅模型） | `lora_name=Qwen-Image-2.1-viggle-turbo-4step-lora-r64.safetensors`, `strength_model=1` | 4-step 加速 LoRA |
| `16` | `TESpeedQwenImage21` | TE-Speed Qwen Image 2.1 | `attention=kitchen_int8`, `step_cache=te_predictor`, `reuse_threshold=0.06`, `predictor_error_limit=0.08` | 推理加速 |
| `13` | `ResolutionSelector` | 分辨率选择器 | `aspect_ratio="2:3 (Portrait Photo)"`, `megapixels=2.2`, `multiple=32` | 输出尺寸 |
| `7` | `EmptyLatentImage` | 空Latent图像 | `width←13,0`, `height←13,1`, `batch_size=1` | 潜空间起点 |
| `10` | `Seed (rgthree)` | Seed (rgthree) | `seed=-1`（随机） | 种子 |
| `5` | `KSampler` | K采样器 | `seed←10`, `steps=8`, `cfg=1`, `sampler_name=euler`, `scheduler=simple`, `denoise=1`, `model←16`, `positive←11,0`, `negative←11,1`, `latent_image←7` | 采样 |
| `9` | `VAELoader` | 加载VAE | `vae_name=qwen_image_2.1_vae_bf16.safetensors` | VAE |
| `12` | `VAEDecode` | VAE解码 | `samples←5`, `vae←9` | 解码 |
| `2` | `SaveImage` | 保存图像 | `filename_prefix="drama"`, `images←12` | 落盘 |

## 关键参数解读

- **steps=8 / cfg=1**：配合 `viggle-turbo-4step` LoRA 与 TE-Speed，属"少步快出"配置。**不要**调成 20 步以上（LoRA 过曝/糊）。
- **aspect_ratio**：可选值取决于 ComfyUI 的 `ResolutionSelector` 实现，常见 `2:3 (Portrait Photo)`、`9:16 (Portrait Widescreen)`、`1:1 (Square)`、`16:9 (Widescreen)`、`3:4 (Portrait)`。
- **megapixels=2.2 / multiple=32**：边长会向下取整到 32 的倍数。降 `megapixels` 可省显存。
- **seed=-1**：`Seed (rgthree)` 的 -1 表示每次随机；固定 seed 便于复现同一张图。

## 改造成"多变体"输出的方法

1. 固定 `seed` + 改 `prompt` → 同风格不同人物（推荐）。
2. `batch_size>1` + 同 prompt → 同 prompt 多张（用于挑一张最好的）。
3. 需要图生图时，该工作流需新增 `LoadImage` + `VAEEncode` + `KSampler.denoise<1` 节点，并同步更新客户端节点常量。

## 换工作流的适配清单

节点 id **不必改代码**，用环境变量覆盖即可（`comfy_client.py` 顶部读取）：

| 环境变量 | 对应节点 | 默认 |
|---|---|---|
| `COMFYUI_NODE_PROMPT` | 正向提示词 | `6` |
| `COMFYUI_NODE_PREFIX` | 保存图像（文件名前缀） | `2` |
| `COMFYUI_NODE_SEED` | 种子 | `10` |
| `COMFYUI_NODE_ASPECT` | 分辨率 | `13` |
| `COMFYUI_NODE_LATENT` | 空 Latent（batch_size） | `7` |

- 若新工作流的提示词节点不是 `PrimitiveStringMultiline`（如 `CLIPTextEncode`），改 `COMFYUI_NODE_PROMPT` 指向它，但**输入字段名可能不是 `value`**——需同步改 `comfy_client.py` 里 `build()` 的赋值。
- 若输出节点不是 `SaveImage`，改 `COMFYUI_NODE_PREFIX`；若有多路输出，`run_one()` 已按 `outputs` 全量取图。
- 工作流文件路径用 `COMFYUI_WORKFLOW` 覆盖，不必替换本目录的默认工作流。
