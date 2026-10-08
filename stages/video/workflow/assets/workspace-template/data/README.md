# data/ —— 输入数据

四个输入文件，全部是纯 JSON。流水线**不调用任何大模型**，所有提示词与参数都来自这里。

## shots.json —— 分镜列表

一条记录 = **一张九宫格 = 一段 H3 视频**。

| 字段 | 必填 | 说明 |
|---|---|---|
| `shot_id` | ✅ | 唯一 ID，同时用作输出子目录名与裁剪文件前缀。建议 `shot_001` 这样补零 |
| `scene` / `scene_title` | — | 场景编号与标题，仅用于汇总表，方便你对账 |
| `description` | — | 中文画面描述，仅供人阅读；不参与提交 |
| `banana_prompt` | ✅ | **图生图**提示词（送进图片工作流的提示词节点）。也兼容写 `prompt` |
| `ratio` | — | 画幅，如 `"16:9"`。送进图片工作流的比例节点（若你配了该节点） |
| `crop` | — | 裁剪网格。写 `{"rows":3,"cols":3}` 或 `"3x3"`；不写则用 `config.DEFAULT_CROP`（默认 3×3） |
| `character_refs` | — | 角色参考图 key 数组，指向 `characters.json` |
| `scene_refs` | — | 场景参考图 key 数组，指向 `scenes.json` |

裁剪编号规则：**行优先**，从 `01` 开始 → `shot_001_01.png`（左上）、`_02`、`_03`（右上）、`_04`（第二行左）… `_09`（右下）。与九宫格的 `position` `1-1 → 3-3` 顺序一致。

## characters.json / scenes.json —— 参考图字典

`{ "key": "值" }`，值支持三种形态：

| 形态 | 例子 | 行为 |
|---|---|---|
| 公开 URL | `"https://cdn.example.com/char01.png"` | 直接透传，**不占上传开销**（推荐） |
| 内联 base64 | `"data:image/png;base64,iVBOR..."` | 直接透传 |
| 本地文件 | `"./refs/char01.png"` | 相对本目录（也可写绝对路径），自动上传换 `download_url` 再提交 |

> 样例文件里放的是 **1×1 占位图**，请替换成你的真实参考图。
> `_` 开头的键（如 `_comment`）不会被引用，纯粹用来写注释。

## h3_prompts.json —— H3 视频提示词

按 `shot_id` 索引，一 shot 一条：

```json
{ "shot_001": { "prompt": "……", "duration": 5, "ratio": "16:9" } }
```

- `prompt` **必填**。缺失即该分镜直接判 `FAILED`（流水线不会自己编提示词）
- `duration` / `ratio` 可选；只有当你在 `config.H3["nodes"]` 里配了对应节点时才会被送出去
- 运行时会为该 shot 落一份 `h3_prompt.json` 快照，方便日后回溯「当时到底喂了什么」
