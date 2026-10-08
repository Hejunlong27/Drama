# STYLE LOCK 预设

STYLE LOCK 一经确定**全剧恒定**，写入 `projects.style_lock` 并冻结 hash。
分镜的 `[STYLE LOCK]` 段必须**原样包含**这段英文常量（E011 检查前 60 字符）。

## 通用底座（所有题材必带）

```
no watermark, no subtitle, no text overlay, no logo
```

## 按题材预设

### 科幻悬疑 / 近未来（当前项目在用）

```
cinematic live-action short drama, 35mm anamorphic lens, desaturated cold-blue palette with warm amber practicals, high contrast chiaroscuro lighting, subtle film grain, shallow depth of field, 2.39:1 widescreen, no watermark, no subtitle, no text overlay, no logo
```

中文展开（写入分镜 `[STYLE LOCK]` 段）：
> 35mm 变形宽银幕镜头，整体去饱和冷蓝调，唯一暖色来自琥珀色实用光（台灯、表盘、警示灯）。明暗对比强烈，暗部保留细节不过曝。手持微呼吸感，禁止稳定到失真的电子防抖。导演调度偏沉静：多用固定机位与极慢推镜，把情绪留给演员的微表情而非剪辑。全片无水印、无字幕、无文字叠加、无 logo。音轨只保留环境音、动作音、人声台词与特殊音效，不配 BGM。

### 都市情感 / 女频

```
webtoon-adapted live-action style, soft warm lighting, pastel color palette, detailed character design, gentle bokeh in close-ups, 2.39:1 widescreen, no watermark, no subtitle, no text overlay, no logo
```

### 逆袭爽剧 / 职场

```
webtoon-adapted live-action style, sleek modern interiors, sharp contrast, high-fashion styling, cool steel-blue accents, 2.39:1 widescreen, no watermark, no subtitle, no text overlay, no logo
```

### 热血动作 / 男频

```
webtoon-adapted live-action style, dynamic action framing, dramatic rim lighting, bold inking, dust particles in fight scenes, 2.39:1 widescreen, no watermark, no subtitle, no text overlay, no logo
```

### 脑洞 / 规则怪谈

```
webtoon-adapted live-action style, desaturated palette with neon accents, uncanny detail, subtle lens distortion, 2.39:1 widescreen, no watermark, no subtitle, no text overlay, no logo
```

## 色卡锚定模板（写进分镜 `[形象视觉 道具 场景和色卡锚定]`）

```
色卡锚定：主色 #XXXXXX，辅助色 #XXXXXX，点缀色 #XXXXXX，暗部 #XXXXXX
```

当前项目《平行世界的另一个我》：
`主色 冷灰蓝 #4A5A6B，辅助色 深墨黑 #14181C，点缀色 琥珀 #C88A3C，暗部 近黑不并块 #0A0C0E`

## 音色锚定模板（写进分镜 `[音色锚定]`）

```
<代号>（<姓名>）：<音色> + <语速> + <情绪特征>
NARRATOR（旁白）：与主视角角色同音色但更冷静，作为后置复盘视角
```

## 改 STYLE LOCK 的代价

改一次 = 全剧分镜的视觉基调全部作废，需要重跑所有已生成的集。
**不要中途改。** 如果确实改了，必须：
1. 更新 `bible/stylelock.md` 与数据库（重跑 `init`）
2. 逐集重跑 Step 4→7
