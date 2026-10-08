# 高清放大（Topaz Video 本地 AI 超分）

> 首次实测：2026-09-21，《七大姑催婚》ep03，1440×832 → 2560×1480。
> 引擎：本 Skill 的 `scripts/upscale.py`。项目侧完整手册：
> `<workspace>/docs/高清放大-2K-实测与操作手册-2026-09-21.md`（实测手册落工作区 docs/）

## 1. 什么时候用它

**合并母版之后、烧字幕之前**。这是顺序铁律，理由：烧字幕必然重编码；
先烧后放会让 AI 把字幕当成画面纹理一起重绘（描边发糊、笔画变形），还多一代损失。

## 2. 它到底调用什么

Topaz **GUI 本身没有命令行**，但它自带的那个 `ffmpeg.exe` 带 `tvai_up` 滤镜 ——
GUI 导出时内部就是在调它。所以 `upscale.py` 复刻 GUI 的命令行，产出与 GUI 一致，但可批处理、可断言。

```text
<安装目录>\ffmpeg.exe -i <母版> -sws_flags spline+accurate_rnd+full_chroma_int \
  -filter_complex tvai_up=model=prob-4:scale=0:w=2560:h=1480:preblur=0:noise=0:details=0:\
halo=0:blur=0:compression=0:estimate=8:blend=0.2:device=0:vram=1:instances=1,\
scale=w=2560:h=1480:flags=lanczos:threads=0 \
  -c:v h264_nvenc -preset p5 -rc constqp -qp 18 -profile:v high -pix_fmt yuv420p \
  -map 0:a? -c:a copy -fps_mode:v passthrough -movflags +faststart <输出>
```

## 3. ★ 三个必踩的坑

### 坑 1：`Model not found: prob-4`（权重在别的盘）

独立跑的 ffmpeg **不知道模型目录在哪**，默认只看 `C:\ProgramData\Topaz Labs LLC\Topaz Video\models`
（那里通常只有模型定义 `*.json`，没有权重 `*.tz3`）。用户把模型目录挪到 D 盘后必踩。

**修法**：设两个环境变量（名字是从 `videoai.dll` 里挖出来的）：

```powershell
$env:TVAI_MODEL_DIR      = "C:\ProgramData\Topaz Labs LLC\Topaz Video\models"   # 定义 *.json
$env:TVAI_MODEL_DATA_DIR = "D:\ProgramData\Topaz Labs LLC\Topaz Video\models"   # 权重 *.tz3
```

`upscale.py` 会自动探测这两处并给子进程设好；探测不到就在 `config.upscale.json` 里写死。

### 坑 2：`Cannot find an unused video input stream to feed the unlabeled input pad tvai_up:default`

命令里写了 `-map 0:v:0`。显式 map 会先把输入视频流"预定"走，滤镜图的输入垫就拿不到流。
**去掉它，只留 `-map 0:a?`**（Topaz GUI 自己也只 map 音频）。

### 坑 3：滤镜参数分隔符

`tvai_up` 的**参数之间用冒号**、**滤镜之间用逗号**：
`tvai_up=model=prob-4:scale=0:w=2560:h=1480:...,scale=w=2560:h=1480:flags=lanczos`。
全用逗号会被当成未知滤镜/参数（要么报错，要么参数被静默忽略）。

## 4. 目标分辨率口径（配置，不改代码）

源 1440×832 的比例是 **1.7308**（不是 16:9 的 1.7778）。

| 想要 | 配置 | 结果 | 代价 |
|---|---|---|---|
| **默认：宽度对齐 2K、等比** | `mode=width, width=2560` | 2560×**1480** | 无（各向异性 0.06%，肉眼不可见） |
| 严格 16:9 的 2K | `mode=exact, 2560x1440` | 2560×1440 | 裁掉 22px 高度（2.6%），或拉伸 2.7% |
| 刚好 2 倍 | `mode=factor, scale=2` | 2880×1664 | 无 |
| 4K | `--target 3840x2160` | 3840×2160 | 显存压力大，未实测 |

## 5. 模型选择

| 模型 | 适用 | 备注 |
|---|---|---|
| `prob-4`（Proteus 4，默认） | 通用向，实拍/AI 生成人物最稳 | 用户 GUI 实测用的就是它；`estimate=8` = GUI 的 Auto |
| `iris-3` | 人脸修复 | 脸部糊得厉害时试 |
| `nyx-3` | 降噪 | 暗部噪点多时 |
| `rhea-1` | 4 倍 | |
| `astra*` / `slm-*` | Astra / Starlight 系列 | 更慢更吃显存，未在本机验证 |

模型短名 = `models\<短名>-<版本>.json` 里的 `shortName` + `version`（如 `prob-4`）。

## 6. 自检口径（7 项，全机器判定）

分辨率精确 / 宽高偶数 / **帧数与源一致** / 时长一致（±0.2s）/ **音轨保留且时长一致** /
抽样帧非黑帧 / **确实比纯插值更锐**。

最后一项是关键：把抽样帧与「源帧用 PIL lanczos 放大到同一尺寸」比，
要求 `hf_ratio` ≥ ×1.02 且 `lap_var` ≥ ×1.05，否则判失败
（阈值在 `config.upscale.json` 的 `verify` 段）。实测 ep03：×1.153 / ×2.099。

> 这条断言的意义：如果模型没加载/没生效，输出本质就是插值，指标会与 lanczos 几乎一样 ——
> **不会出现"跑了 8 分钟其实什么都没发生"却报成功的情况**。

## 7. 性能与体积（RTX 4060 Laptop 8GB，实测）

| 项 | 值 |
|---|---|
| 速度 | **8.2 帧/秒**（0.31× 实时）⇒ 166 秒的集 ≈ **8 分钟** |
| 体积 | 1440p 91 MB → 2K **236 MB**（nvenc constqp 18，≈11 Mbps） |
| 8 集预估 | 约 65 分钟，+1.9 GB |

**不要并行跑多集**（8GB 显存会被抢爆，可能出黑帧/绿帧）。

## 8. 缓存

`<ep>_upscale.json` 里存 `cache_key`（源文件**内容指纹** + 目标尺寸 + 模型 + 编码参数）。
参数或素材没变就直接跳过重跑 —— 注意指纹**故意不用 mtime**：
`subtitle.py --stage all` 每次都会重跑一遍无损拼接，mtime 必变，
用 mtime 当键会导致"每跑一次都白放大一遍"（实测踩到，代价 8 分钟 GPU）。
要强制重跑用 `--force`。
