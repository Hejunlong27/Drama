# 两处转换层（不做就串不起来）

## T1 上游：`parallel-world-writer` → 视觉锚点

### 问题
它的 world-bible 结构里，角色只有 `name / real_identity / parallel_identity / desire / fear / regret / final_choice`——**一个视觉字段都没有**。而 FFS 的 `# CHAR:` 要求「大写英文代号 + 名词性短语开头的 2–4 项外貌/服饰/标志物锚点」。

### 解决
Step 1 结束时，为每个角色补齐这四个字段，落 `bible/world-bible.json`：

```json
{
  "characters": [
    {
      "code": "LIN_YU",
      "name": "林宇",
      "age": 28,
      "personality": "理性、孤独、执拗",
      "arc": "从怀疑到相信，再到发现真相后选择沉默",
      "real_identity": "...",
      "parallel_identity": "...",
      "desire": "...", "fear": "...", "regret": "...",
      "visual_anchor": "28岁中国男性，清瘦，短黑发略乱，深灰针织衫内搭白衬衫，左腕戴一块走时不准的旧机械表",
      "palette": "主色冷灰蓝，点缀暖琥珀（表盘／台灯）",
      "voice": "低沉、语速偏慢，紧张时会停顿半拍"
    }
  ],
  "scenes": [
    {"code": "APT", "desc": "林宇的公寓，堆满论文与外卖盒，唯一光源是桌上的琥珀色台灯", "int_ext": "内景", "time": "夜"}
  ]
}
```

### 硬性要求
- `code`：大写英文 + 下划线（`LIN_YU` / `SU_WAN` / `ZHOU`）。同一人的两个版本用后缀区分（`LIN_YU` / `LIN_B`）。
- `visual_anchor`：**必填，不许猜，不许留空**。缺了 `import-bible` 直接抛错。
  格式 = 名词性开头 + 外貌 + 服饰 + 标志性道具/标记，2–4 项。
  范例：「28岁中国男性，清瘦，短黑发略乱，深灰针织衫内搭白衬衫，左腕戴一块走时不准的旧机械表」
- 同一人的平行版本，**差异要写在锚点里**（如「与林宇完全相同的脸，右眉尾有一道浅疤，黑色高领毛衣，无手表」），这样生成时不会撞脸。
- `palette` / `voice`：给分镜阶段的色卡锚定和音色锚定用。

---

## T2 下游：FFS → `seedance-storyboard` 可读剧本

### 问题
`seedance-storyboard` 的解析规则（`references/script-parsing-rules.md`）匹配的是：
- 分集标记：`第X集`
- 场标题：`场1｜内景 教室 - 下午`
- 台词：`老者："台词"` 或 `MAYA："台词"`

它**不认 FFS 的 `ACTION:` / `DIALOG:` 字段行**。直接把 FFS 喂进去，它的「角色视觉特征提取」和「台词提取」会双双失效，结果是角色长相逐镜漂移、台词错漏。

### 解决
Step 6 先跑 `python tools/cli.py convert --ep N`，把 FFS 渲染成普通剧本：

```
第1集：平行世界的另一个我

场1｜内景 林宇的公寓 - 夜
手机屏幕在黑暗中亮起，一条未读短信浮出来：「别按终止键。」LIN_YU 的睫毛动了一下
NARRATOR：林宇后来才知道，那条短信是他自己发的。

林宇从沙发上猛地坐起，小腿扫倒脚边的速溶咖啡杯，褐色液体漫过一叠论文
LIN_YU："谁？"
```

转换规则：
- 相邻同 `LOCATION` 的镜头合并为一个「场」，场标题取场景描述的第一段（≤12 字）+ 本镜 TIME。
- `ACTION` 直接作为动作段落输出。
- `DIALOG` 输出为 `{代号}："{原文}"`，**台词一字不改**。
- `VO` 输出为 `NARRATOR：{原文}`（VO 已带前缀时不重复添加）。
- 代号保留在文本里——seedance 会据此提取角色视觉特征。

### 自检
转换后统计台词行数，必须等于 FFS 中 `DIALOG` 非「无」的镜头数。对不上就是转换有 bug。

---

## T3 风格覆盖

`seedance-storyboard` 默认「禅意意境 + 赛博高级质感（万物生风格）」，与多数题材冲突。

调用它时必须：
1. 显式传**绝对路径**输出目录（它默认是相对路径 `seedance-storyboard/项目名/`，会落到奇怪的地方）。
2. 从 `bible/stylelock.md` 读取本项目 STYLE LOCK，**覆盖**它的默认风格。
3. 分镜输出的 `[STYLE LOCK]` 段必须**原样包含**项目英文常量——E011 会检查前 60 字符是否出现在分镜里。
