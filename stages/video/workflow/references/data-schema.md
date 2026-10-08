# data-schema.md —— 输入数据字段契约

四个文件都在 `<工作区>/data/`，**一律只读**。样例见 `<skill>/assets/workspace-template/data/`。

## 1. shots.json —— 分镜列表（数组，或 `{"shots": [...]}`）

| 字段 | 必填 | 说明 |
|---|---|---|
| `shot_id` | ✅ | 唯一 ID，如 `shot_001`；重复会校验报错 |
| `banana_prompt` | ✅ | 九宫格图生图提示词（`prompt` 作为别名也认） |
| `ratio` | ➖ | 画幅，如 `"16:9"`；未填则不注入比例节点 |
| `crop` | ➖ | `{"rows": 3, "cols": 3}` 或 `"3x3"`；未填用 `default_crop`（3×3） |
| `character_refs` | ➖ | 角色参考图 key 数组，引用 characters.json |
| `scene_refs` | ➖ | 场景参考图 key 数组，引用 scenes.json |

```json
[
  {
    "shot_id": "shot_001",
    "banana_prompt": "cinematic storyboard, rainy rooftop, young man...",
    "ratio": "16:9",
    "crop": { "rows": 3, "cols": 3 },
    "character_refs": ["char_01"],
    "scene_refs": ["scene_01"]
  }
]
```

## 2. characters.json / scenes.json —— 参考图字典

值支持三种写法：
1. http(s) URL —— 直传给 RunningHub
2. `data:image/png;base64,...` —— 直传
3. 本地路径 —— 相对 `data/` 解析，引擎自动上传换 URL（约 1 天有效，缓存 20h）

```json
{ "char_01": "https://.../char01.png", "char_02": "refs/char02.png" }
```

## 3. h3_prompts.json —— H3 提示词，按 shot_id 索引

| 字段 | 必填 | 说明 |
|---|---|---|
| `prompt` | ✅ | H3 视频提示词（缺了该 shot 直接 FAILED，不猜） |
| `duration` | ➖ | 秒数，配了才注入 duration 节点 |
| `ratio` | ➖ | 画幅，配了才注入 ratio 节点 |

```json
{ "shot_001": { "prompt": "...", "duration": 5, "ratio": "16:9" } }
```

## 4. 裁剪编号规则

`crop {rows:3, cols:3}` → 香蕉输出整图按 3×3 均分 9 格，输出顺序**行优先、从左到右、从上到下**：
`shot_001_01.png` … `shot_001_09.png`（编号从 01 开始，两位补零）。
`crop {rows:1, cols:1}` → 只出一张 `shot_001_01.png`（即原图重命名为编号件）。

## 5. 校验规则（`--dry-run --strict` 会逐条报）

- shots.json 为空 / 缺 shot_id / shot_id 重复
- 缺 `banana_prompt`
- shot_id 在 h3_prompts.json 里没有对应条目
- `character_refs` / `scene_refs` 引用了字典里不存在的 key
- 本地参考图路径不存在

**修数据的原则**：缺关键字段直接报错让用户补，不许猜、不许编。
