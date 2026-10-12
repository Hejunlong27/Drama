# 第四部分 · 剧本改写（LLM 提示词模板）

> 用途：指导会话内 LLM 按 `creation/03-script/blueprint.json`（④的结构规划，唯一指令来源）
> 与 `script-brief.md`，把源剧本改写成 **N 集（默认 60）** 的 FFS 正文。
>
> `script_rewrite.py` 已完成确定性部分：集数规划（每集的**核心冲突/反转/钩子**）、FFS 骨架、
> 结构与格式自检。LLM 负责把每集写成可拍的正文。

## 任务

> **四、剧本改写**：将一部「很颠」的西语短剧剧本改写为**完整 60 集规划**，
> 每集须包含**核心冲突、剧情反转与钩子设计**；整体**节奏紧凑、钩子密集、爽点高频**，
> 契合西语市场观看偏好与平台调性。

## 前置

1. 源剧本放到 `creation/03-script/source-script.md`（"很颠"的原稿）。
2. `python script_rewrite.py plan --episodes 60` 已生成 `blueprint.json`（含逐集冲突/反转/钩子）与 `script-brief.md`。
3. 单集骨架：`python script_rewrite.py build --ep N --emit` → `<工作区>/script/ep_XX.md`。

## 改写方式（源剧本 → 新规划）

1. **保留**源剧本的核心人设、设定魅力与最「颠」的爽点机制（这是它的价值所在）。
2. **重排**为 N 集结构（见 blueprint 的 `episodes`），**每集必须同时具备**：
   - **核心冲突**（`core_conflict`）
   - **一次剧情反转**（`reversal`）
   - **集尾钩子**（`hook`，7 型之一：身份悬念/危机逼近/关系反转/秘密揭露/选择困境/意外登场/代价预告）
3. **加密**钩子与爆点：按 `boom_cadence` 植入中型爆点（每 6 集）与大型爆点（每 15 集）。
4. **节奏**：单镜 4–15 秒、单集 6–15 镜、**单集总时长 90–120 秒**；开场 1–2 镜短促抓人，集尾短收。

## 正文格式（FFS，严格）

按 `docs/creation/ffs-spec.md`：

- **HEADER 六字段**：`# TITLE` / `# EP` / `# PLATFORM` / `# STYLE LOCK` / `# CHAR` / `# SCENE`；
  `STYLE LOCK` 逐字复制 blueprint 的 `style_lock`。
- **镜头块 8 字段**（顺序固定）：`[SC-xx]` + SHOT / LOCATION / TIME / CHAR / ACTION / DIALOG / VO / SFX（可选 DUR）。
- **禁止自定义字段**（MOOD/CAMERA/EMOTION/MUSIC/LIGHT 触发 E004）；情绪写进 ACTION 的**具体动作与表情**。
- `ACTION` 必须出现角色代号或名字；镜头块内只引用 HEADER 代号。

### 台词与旁白（西语口语）

- 每句台词先归类功能（推进/引爆/揭示/悬念/关系），**无功能的台词不写**。
- 口语西语（拉美通用），禁翻译腔；按语速（约 3.5–4 词/秒）换算匹配镜头时长。
- `VO` 带来源前缀（`NARRATOR:` / `角色名 (V.O.):`），每集 ≤5 处。
- 双语交付：西语正文 + 中文译稿。

## 差异化红线（违反即失败）

见 `script-brief.md` 的「四、差异化红线」，重点：不复刻源剧本/对标剧的台词与镜序；不与他人共用同一冲突结构；
反套路必须指明反的**具体**套路；本土化要可代入；每集三件套（冲突+反转+钩子）缺一不可。

## 交付自检（顺序执行，全绿才算完成）

```bash
python stages/creation/scripts/script_rewrite.py --workspace <工作区> script lint --ep 1
python <工作区>/tools/cli.py check --ep 1          # E001–E011
python <工作区>/tools/cli.py parse --ep 1
python <工作区>/tools/cli.py convert --ep 1
```

## 进入制作

剧本定稿后，按出片数据契约整理 `data/` 四文件，再到 `stages/production/scripts/drama.py` 走
**模型确认门禁 → 资产出图 → 参考图接线 → 出片 → 拼接/2K → 字幕 → 验收结账**。
