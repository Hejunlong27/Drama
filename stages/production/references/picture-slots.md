# 参考图与 `<Picture N>` 口径（硬约束）

> 这一份决定了「补资产该怎么做」。**做错会让整段的画面串人、串场景，而且不报错。**

## 一、机制（实测归纳，2026-09-19）

提示词 `subject_definitions` 里的 `<Picture N>` 是对**全部 Subject 顺序编号**——角色、场景、道具**都占号**。
例子（ep07_seg01）：

```
<Subject 1> 是 <Picture 1> 中的陆鸣形象。      ← 角色
<Subject 2> 是 <Picture 2> 中的林晚形象。      ← 角色
<Subject 3> 是 <Picture 3> 中的老管家形象。    ← 角色
<Subject 4> 是 <Picture 4> 中的大伯形象。      ← 角色
<Subject 5> 是 <Picture 5> 中的堂弟陆浩形象。  ← 角色
<Subject 6> 是 <Picture 6> 中的三亚拘留所门外滨海公路…   ← 场景（也占号！）
<Subject 7..10> 是道具「黑金劳斯莱斯」等           ← 道具（一律文字锁定，不进参考图）
```

而引擎把 `shots.json` 的 `character_refs[i]` **按顺序**喂给第 i 个槽位
（H3 的 node 51 / 49 / 50 / 43 / 19 / 23，对应 image1..image6）。

## 二、三条硬推论

1. **缺一个角色的图，它后面的场景图会全部错位** —— 因为位置对应，不是 key 对应。
2. **不能留空号**（引擎无法跳过某个槽位）。
3. **H3 只有 6 个图片槽位**（工作流与 AI 应用都是 6 个 LoadImage，**硬上限，改不了**）。
   提示词点名到 `<Picture 7>` 以上时，**末尾的图会被丢掉并造成错位**。

> ⚠️ **不能「把场景图追加到末尾」蒙过去** —— 必须按 Picture 号逐个落位。

## 三、本项目的约定

- **场景图也放进 `character_refs`**（payload 里不写 `scene_refs`；`scenes.json` 一直是空表）。
- **道具不进参考图**（用户 2026-09-18 拍板）——一律用文字锁定形象。
- 因此 `character_refs` 的**顺序 = 提示词里 `<Picture 1..K>` 的顺序**，与角色/场景的类型无关。

## 四、标准作业（补资产 → 接线）

```powershell
# ① 先体检，看缺什么、哪几段被阻塞（零计费）
python <skill>\scripts\episode_preflight.py 7 8 9 10

# ② 补缺的资产
python <skill>\scripts\drama.py assets --kind char  --only 13,14,15,16,18,19,20     # 干跑
python <skill>\scripts\drama.py assets --kind char  --only 13,14,15,16,18,19,20 --submit
python <skill>\scripts\drama.py assets --kind scene --only 5,6 --submit

# ③ 瘦身 + 接进 characters.json（新图是 6000×3376，超 H3 上限 5760，必须瘦身）
python <skill>\scripts\wire_new_assets.py            # 干跑
python <skill>\scripts\wire_new_assets.py --apply

# ④ 按 <Picture N> 重排 20 段的 character_refs
python <skill>\scripts\wire_episode_refs.py 7 8 9 10          # 干跑
python <skill>\scripts\wire_episode_refs.py 7 8 9 10 --apply  # 自动备份 shots.json

# ⑤ 复检：应全部变成 OK
python <skill>\scripts\episode_preflight.py 7 8 9 10
```

三个工具**默认都是干跑**，只有 `--apply` / `--submit` 才写盘/计费。

## 五、体检输出怎么读

```
  shot           应有参考图                              maxP  实发    问题
  ep07_seg01     char_01,char_07,char_13,char_09,ch      6     6     OK
  ep07_seg05     char_01,char_12,❌沈二叔(第14节),...     5     2     P3 缺角色图:…
```

| 列 | 含义 |
|---|---|
| `应有参考图` | 按 `<Picture N>` 顺序解出来的 key 列表；`❌` 是缺资产 |
| `maxP` | 该段提示词点到的**最大 Picture 号** = 需要几张图 |
| `实发` | `shots.json` 里现在真的挂了几个 |
| `问题` | 逐条列出「P几 缺什么」；`实发 < maxP` 就是被阻塞 |

**`❌` 说明该资产还没生成**（或生成了但没接进 `characters.json`）。
体检还会报「★需要 N 张图，但只有 6 个槽位」——那是**提示词本身要改**，不是补图能解决的。

## 六、踩过的坑

| 坑 | 现象 | 真相 |
|---|---|---|
| 追加场景图到末尾 | 全段人物错乱 | 位置对应，追加 = 整体错位 |
| 生成完就当接入 | 引擎报「找不到 key」 | 产出在 `output/assets/` 躺着，`characters.json` 里还没有该 key |
| 直接跑 `gen_char_assets.py` | 报 `未设置 RUNNINGHUB_API_KEY` | Key 在用户级环境变量里，agent shell 读不到 ⇒ **必须走 `drama.py`** |
| 以为文档说「27 RH币/张」 | 报价偏差 | 实测 **16–24 RH币/张**，按提示词复杂度浮动 |
| 以为场景图也 2048 宽 | 尺寸预期错 | 场景空镜只出 **1376×768**（比人物图低一档） |
| 提示词点到 `<Picture 8>` | 末尾图被丢 | H3 硬上限 6 槽 ⇒ 改提示词 |
