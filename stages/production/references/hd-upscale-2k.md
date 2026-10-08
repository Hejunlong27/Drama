# 高清放大（2K）· 实测与操作手册

> 日期：2026-09-21 ｜ 适用：《七大姑催婚》短剧流水线（工作区路径按项目自定）
> 状态：**已端到端跑通并交付第一集**（ep03：1440×832 → 2560×1480，3980 帧全对，7 项自检全过）

---

## 一、为什么是「先放大、后烧字幕」这个顺序

烧字幕**一定会重编码**（`subtitles=` 是滤镜，必须重新解码再编码）。
所以如果把字幕先烧上去、再拿去做 AI 超分，会出两个问题：

1. **字幕被 AI 当画面纹理一起"重绘"** —— 描边发糊、笔画变形、偶尔出现虚影；
2. **多一代损失** —— 字幕像素也经历一次有损编码 → 超分 → 再编码。

正确顺序（也是用户拍板的顺序）：

```
逐段成片 → 无损拼接母版(1440p) → 【高清放大到 2K】 → 问用户要不要字幕 → 在 2K 上烧字幕
```

好处：字幕是**在 2K 上原生渲染的**（libass 直接输出 2K 字），边缘锐利、位置/字号比例与 1440p 完全一致。

---

## 二、本机环境（实测）

| 项 | 值 |
|---|---|
| 应用 | **Topaz Video**（不是老的 Video AI），装在 `D:\Program Files\Topaz Labs LLC\Topaz Video` |
| 可编程入口 | 应用自带的 **`ffmpeg.exe`（带 `tvai_up` 滤镜）** —— GUI 导出时内部调的就是它 |
| 模型定义 | `C:\ProgramData\Topaz Labs LLC\Topaz Video\models`（`*.json`，95 个） |
| 模型权重 | **`D:\ProgramData\Topaz Labs LLC\Topaz Video\models`**（`*.tz3`，656 个文件 / 33 GB）★用户把模型目录挪到了 D 盘 |
| GPU | NVIDIA RTX 4060 Laptop 8GB（`device=0`）+ AMD 780M 核显 |
| 编码器 | `h264_nvenc`（GUI 完整导出的选择；预览用的 `h264_amf` 是核显） |
| 历史证据 | 用户 2026-09-16 在 GUI 里用 **prob-4** 把《麻辣转校生》片段放大到 3840×2160，成功（日志 `%APPDATA%\Topaz Labs LLC\Topaz Video\logs`） |

### ★ 最大的坑（不看这条会以为"Topaz 坏了"）

直接跑那个 ffmpeg 会报：

```
[Parsed_tvai_up_0] Model not found: prob-4
```

**原因不是没授权、也不是没装**：独立运行的 ffmpeg 默认只看 `C:\ProgramData\...\models`，
而那里**只有模型定义 json，没有权重 `.tz3`**（权重被用户挪到了 D 盘）。
GUI 之所以能用，是因为 GUI 自己知道这个目录。

**解法**：给子进程设两个环境变量（`upscale.py` 会自动探测并设置）：

```powershell
$env:TVAI_MODEL_DIR      = "C:\ProgramData\Topaz Labs LLC\Topaz Video\models"   # 模型定义 *.json
$env:TVAI_MODEL_DATA_DIR = "D:\ProgramData\Topaz Labs LLC\Topaz Video\models"   # 模型权重 *.tz3
```

> 这两个变量名是从 `videoai.dll` 里挖出来的（同段字符串还有
> `TVAI_MODEL_DOWNLOAD_HOSTNAME` / `TVAI_MODEL_DOWNLOAD_PATH`，说明库本身也支持联网下载模型）。
> 设置后**再没有任何报错，直接开跑** —— 已实测。

**另一个坑**：命令里**不能写 `-map 0:v:0`**。写了会报
`Cannot find an unused video input stream to feed the unlabeled input pad tvai_up:default`
（显式 map 先把输入视频流"预定"走了，滤镜图的输入垫就拿不到流）。Topaz GUI 自身也只 `-map 0:a?`。

---

## 三、实测数据（本机 RTX 4060 Laptop）

| 项 | 实测值 |
|---|---|
| 输入 | `ep03_master.mp4` 1440×832 / 24fps / 3980 帧 / 165.87s / 91 MB / 4.47 Mbps |
| 输出（无字幕） | `ep03_master_2k.mp4` **2560×1480** / 24fps / 3980 帧 / 165.87s / **235.8 MB** / 11.79 Mbps |
| 输出（带字幕） | `ep03_subbed.mp4` **2560×1480** / 24fps / 3980 帧 / 165.87s / **214.4 MB** / 10.71 Mbps |
| 放大耗时 | **484 s ≈ 8.1 分钟**（平均 **8.2 帧/秒**，约 0.31× 实时） |
| 2K 烧字幕耗时 | **111 s ≈ 1.9 分钟**（libx264 crf18 medium，约 36 帧/秒） |
| 整条链路 | `subtitle.py --ep ep03`（含对齐缓存/条目/拼接/放大/自检）共 **231.9 s** 那次是命中放大的缓存；首次全跑约 **11 分钟** |
| 模型 | `prob-4`（Proteus 4，`estimate=8` 自动估参、`blend=0.2` 保留 20% 原始细节） |
| 音轨 | aac 165.858s 全程 `-c:a copy`，**逐字节未动** |

**画质证据（机器判定，不是眼睛）**：抽样帧与「纯 lanczos 插值放大」对比 ——

| 指标 | 相对 lanczos |
|---|---|
| 高频能量比 `hf_ratio` | **×1.153** |
| 拉普拉斯方差 `lap_var` | **×2.099** |

⇒ 明显高于纯插值，**证明 AI 模型真的在跑**（如果模型没生效，结果会与 lanczos 几乎一样）。
这条已经写成硬断言：低于阈值直接判失败。

**字幕侧同步验收**（`_qa_subtitle_frame.py ep03 --all`，2K 画布）：
**32/32 条全过** —— 单行高 71–81px、双行高 172–180px、底部留白 134–139px（配置 129，容差 ±16）、
左右最宽 x[566,1992] 未越界（边距 108）。ASS 头已自动写成 `PlayResX/Y: 2560x1480`、字号 99px、边距 108/129。

**全剧预估**：8 集 × 约 8 分钟放大 + 2 分钟烧字幕 ≈ **80 分钟**，额外占用 ≈ **1.9 GB**（D 盘现有 260 GB 空闲）。
放大是 **GPU 独占**的，不要并行跑多集（8GB 显存会被抢爆）。

---

## 四、用法

### 4.1 三个最常用的命令

```powershell
# ① 先看会做什么（不跑，秒回）
python scripts\upscale.py --ep ep03 --dry-run

# ② 让流水线一路走到 2K 母版，然后【停下来问你要不要字幕】
python scripts\subtitle.py --ep ep03
#    → 产物：output\subs\ep03\ep03_master_2k.mp4（无字幕）

# ③ 要字幕 → 在 2K 母版上烧（约 1–3 分钟）
python scripts\subtitle.py --ep ep03 --stage render
#    → 产物：output\subs\ep03\ep03_subbed.mp4（2560×1480，带字幕）
```

想一口气跑完（不在中间停）：

```powershell
python scripts\subtitle.py --ep ep03 --burn
```

只想单独放大（不碰字幕）：

```powershell
python scripts\upscale.py --ep ep03
```

### 4.2 控制台（短剧控制台.bat）

新增三项：

| 菜单 | 作用 | 计费 |
|---|---|---|
| **10** 高清放大·预演 | 打印目标分辨率/模型/完整命令行 | 不花钱 |
| **11** 高清放大·2K | 真的跑（约 10 分钟/集） | **不进 API 计费**（本地 GPU） |
| **12** 加字幕 | 在 2K 母版上烧字幕 | 不花钱 |

### 4.3 分辨率口径（改配置，不改代码）

配置文件：`video-pipeline/config.upscale.json`

| 想要的效果 | 配置 |
|---|---|
| **默认：宽度到 2K、等比不变形** → 2560×1480（高比 16:9 多一点，0.06% 误差，肉眼不可见） | `"target": {"mode": "width", "width": 2560}` |
| **严格 16:9 的 2560×1440**（会裁掉 22px 高度，约 2.6%） | `"mode": "exact", "width": 2560, "height": 1440` |
| **刚好 2 倍** → 2880×1664（无损、不变形、比 2K 更大） | `"mode": "factor", "scale": 2` |
| 直接 4K（用户 9/16 在 GUI 里试过的那档） | `"--target 3840x2160"` 或 `"mode":"exact"` |

> 为什么默认不是 2560×1440：源是 1440×832（比例 1.7308），16:9 是 1.7778。
> 硬拉到 2560×1440 会**横向拉伸 2.7%**（脸会变宽），要不变形就得裁掉上下各 11px。
> 所以默认选「宽度对齐、高度等比」——不变形、不裁切，代价只是高度是 1480 而不是 1440。

### 4.4 换模型

```json
"model": { "name": "prob-4", "estimate": 8, "blend": 0.2 }
```

| 模型 | 适用 |
|---|---|
| `prob-4`（默认） | Proteus 4：通用向，实拍/AI 生成人物最稳，**用户 GUI 实测用的就是它** |
| `iris-3` | 人脸修复（脸部糊得厉害时试） |
| `nyx-3` | 降噪（暗部噪点多时） |
| `rhea-1` | 4 倍放大 |
| `astra*` / `slm-*` | 新出的 Astra / Starlight 系列（更大、更慢，未在本机验证过） |

---

## 五、自检与验收（7 项，全部机器判定）

`upscale.py` 每次跑完都会断言，并把数据写进 `<集号>_upscale.json`：

| # | 断言 | ep03 实测 |
|---|---|---|
| 1 | 分辨率精确等于目标 | 2560×1480 ✅ |
| 2 | 宽高都是偶数（yuv420p 硬要求） | ✅ |
| 3 | **帧数与源一致** | 3980 / 3980 ✅ |
| 4 | 时长一致（±0.2s） | 165.83 / 165.83 ✅ |
| 5 | **音轨保留且时长一致** | aac 165.86 / 165.86 ✅ |
| 6 | 抽样帧不是黑帧/绿帧（超分失败的典型症状） | 无黑帧 ✅ |
| 7 | **确实比纯插值更锐**（模型真生效） | 高频 ×1.153 / 拉普拉斯 ×2.098 ✅ |

字幕侧另有：烧录后成片帧数必须等于母版帧数、像素自检（字幕带近白像素增加）、
`_qa_subtitle_frame.py`（已改成**按母版真实分辨率**判定字号/边距，2K 下不再误报）。

---

## 六、产物清单（以 ep03 为例，均为本次实测产出）

```
video-pipeline\output\subs\ep03\
  ep03_master.mp4        1440×832 无字幕母版（保留，可回退）      91.0 MB
  ep03_master_2k.mp4     2560×1480 无字幕 2K 母版  ← 交付/再剪辑用这个   235.8 MB
  ep03_subbed.mp4        2560×1480 带字幕成片       ← 给用户看这个       214.4 MB
  ep03.ass               字幕文件（PlayResX/Y 已按 2K 写：2560×1480，字号 99px）
  ep03_upscale.json      放大参数 + 7 项自检数据（含 relative-to-lanczos 增益）
  ep03_upscale.log       Topaz ffmpeg 原始日志
  report.json            整条链路的运行报告
  _up_tmp/ _up_tmp_src/  抽帧临时目录（跑完自动清空）
```

---

## 七、代码与 Skill 的同步（**改完必须两边一致**）

| 真源（项目） | Skill 副本 |
|---|---|
| `scripts\subtitle.py` | `<repo>/stages/subtitle/scripts/subtitle.py` |
| `scripts\upscale.py` | `...\drama-subtitle-pipeline\scripts\upscale.py` |
| `scripts\_qa_subtitle_frame.py` | `...\short-drama-production\scripts\_qa_subtitle_frame.py` |
| `video-pipeline\config.upscale.json` | `...\drama-subtitle-pipeline\assets\config.upscale.example.json` |
| 本文件 | `...\short-drama-production\references\hd-upscale-2k.md` |

`subtitle.py` **会 import 同目录的 `upscale.py`** ⇒ 两个必须一起同步；
缺了 `upscale.py` 不会崩，但会退化成 1440p（并在输出里打警告）。

---

## 八、故障速查

| 症状 | 根因 | 修法 |
|---|---|---|
| `Model not found: prob-4` | 独立 ffmpeg 找不到权重（在别的盘） | 设 `TVAI_MODEL_DIR` / `TVAI_MODEL_DATA_DIR`（脚本会自动探测；探测不到就在配置里写死） |
| `Cannot find an unused video input stream ... tvai_up:default` | 命令里写了 `-map 0:v:0` | 去掉，只留 `-map 0:a?`（脚本已内置这条经验） |
| 输出是黑帧/绿帧 | GPU 显存不足或模型没加载 | 关掉别的占显存程序；`"vram": 0.8`；或 `"instances": 0` |
| 跑到一半卡住 | 8GB 显存被抢 | 不要并行跑多集；`device=0` 固定用独显（别让它落到核显） |
| 字幕字号看着不对 | ASS 的 PlayResX/Y 与视频分辨率不等 | 已自动处理：`subtitle.py` 按母版**实际**分辨率重写 ASS 并提示 |
| 想跳过放大 | —— | `--no-upscale`（回到 1440p 直接烧字幕的旧行为） |
| Topaz 完全不可用但要出片 | —— | `--fallback-lanczos`：**只把分辨率数字变大，画质不会真的变好**，脚本会打 WARN |

---

## 九、未验证 / 风险（如实说明）

1. **其它集没跑过**：只实测了 ep03。ep04–ep10 时长相近，预计 8–10 分钟/集，但**没实测**。
2. **4K（3840×2160）没在本机 CLI 跑过**：用户在 GUI 跑成功过；CLI 路径未实测，显存压力更大。
3. **模型审美未经人眼**：本机无 vision provider，只能用指标证明"比插值更锐"，
   **不能证明"更好看"**（AI 超分在极少数镜头可能出现塑料感/脸部过锐）。建议每集抽 2–3 帧看片。
4. **暂不支持中途取消续跑**：放大是一次性 ffmpeg 进程，中断要重跑整集（约 8 分钟）。
5. **磁盘**：8 集约 +1.9 GB；若再出 4K 版会翻倍。
6. **本功能依赖用户机器上的 Topaz Video**（含其授权/模型）。换机器要重跑一次 `--dry-run` 确认目录探测结果。
