# RunningHub AI 应用 · 提交契约与成本台账（实测）

> 来源：2026-10-05《贤妻不装了》G4.5 参考图实战（81 张全量跑通）。
> 站点：本账号在**国际站** `https://www.runninghub.ai`（`.cn` 上传会报 `code=1 API Key不存在`）。

---

## 一、Key 纪律（铁律）

| Key | 环境变量 | 用途 |
|---|---|---|
| **消费级** | `RUNNINGHUB_API_KEY` | ✅ **AI 应用只能用它** |
| 企业级-共享 | `RUNNINGHUB_ENT_API_KEY` | 留给**标准模型 API**（如 `rh-free-h3-video` 那条免费出片线） |

> 反过来也一样严格：**标准模型 API 只认企业级-共享 Key**，消费级会报 `errorCode 1014`。
> 两条线各用各的，不要混。

---

## 二、AI 应用（webapp）节点契约

**探测（2026-10-07 更新 · 旧的 apiCallDemo 通道已失效）**：

> ⚠️ `GET /api/webappapi/apiCallDemo?apiKey=<key>&webappId=<id>` **现在返回 `{"code":403,"msg":"TOKEN_MISSION"}`**
> （该接口改为要求网页登录态，不再是开放探测口）。**不要再依赖它探测节点**。
>
> ✅ 现行最省事的探测法：**故意传一个非法枚举值提交一次**，ComfyUI 的校验错误会把**全部合法取值**回显出来
> （`node_errors.<id>.errors[].details` 里带完整 options 数组），且**不计费**：
> ```json
> # 提交 aspect_ratio="3:4 (Portrait)"  →  回显：
> "aspect_ratio: '3:4 (Portrait)' not in ['1:1 (Square)', '2:3 (Portrait Photo)', '3:2 (Photo)',
>  '3:4 (Portrait Standard)', '4:3 (Standard)', '9:16 (Portrait Widescreen)', '16:9 (Widescreen)',
>  '21:9 (Ultrawide)']"
> ```

本例实测节点（AI 应用 `<your_webapp_id>`，账号绑定资源，勿外泄）：

| nodeId | fieldName | 类型 | 取值 |
|---|---|---|---|
| `517` | `value` | STRING(multiline) | **提示词全文** |
| `512` | `aspect_ratio` | LIST | **合法枚举共 8 个**：`1:1 (Square)` / `2:3 (Portrait Photo)` / `3:2 (Photo)` / `3:4 (Portrait Standard)` / `4:3 (Standard)` / `9:16 (Portrait Widescreen)` / `16:9 (Widescreen)` / `21:9 (Ultrawide)` |
| `512` | `megapixels` | FLOAT | `2.2` |

> ★ **竖版海报要的是 `3:4 (Portrait Standard)`**（不是 `3:4 (Portrait)`，写错会报 `value_not_in_list`）。
> ★ 实测 3:4 + 2.2MP 出图为 **1312×1760**，正好 3:4，长边 1760 > 1080，可直接压到 1080×1440 上传。

**提交**：`POST /task/openapi/ai-app/run`（apiKey 放 **body**）
```json
{ "apiKey": "<consumer key>", "webappId": <your_webapp_id>,
  "nodeInfoList": [ {"nodeId":"517","fieldName":"value","fieldValue":"<提示词>"},
                    {"nodeId":"512","fieldName":"aspect_ratio","fieldValue":"3:4 (Portrait Standard)"},
                    {"nodeId":"512","fieldName":"megapixels","fieldValue":"2.2"} ] }
```
**查询**：`POST /openapi/v2/query`，body `{"taskId": "..."}`
> ⚠️ **查询的 apiKey 走请求头 `Authorization: Bearer <key>`，不放 body**。
> 只放 body 会返回 `{"code":1602,"msg":"HEADER_API_KEY_NOT_FOUND"}` ——
> 这个错**长得不像鉴权错**，极易被误判成「任务不存在 / 状态未知」，从而把轮询写成死循环。


> ⚠️ **AI 应用 ≠ 工作流**：工作流走 `/task/openapi/create` + `workflowId`；两条通道不要混用。
> `ai-video-pipeline` 技能里的 `runninghub_client.py` 已同时封装了两者，可直接 import 复用。

---

## 三、单价与成本（实测）

| 类 | 单价 | 说明 |
|---|---|---|
| 人物设定板 | ~7.9 币/张 | 16:9 + 2.2MP |
| 场景设定板 | ~7.0 币/张 | 同上 |
| 道具图 | ~5.7 币/张 | 1:1 方图 |
| **81 张成品** | **556 币**（均价 6.9） | 台账累计口径 |

**台账字段建议**（每张图一条）：`task_id / status / prompt / ratio / mp / url / file / usage.consumeCoins / submitted_at / finished_at`。

---

## 四、断点续跑的四种分支（★ 这是省钱的全部秘密）

按顺序判断，**每种分支都不得重提交**（重提交 = 重复扣费）：

| # | 台账状态 | 磁盘文件 | 动作 |
|---|---|---|---|
| 1 | `SUCCESS` | 存在且 **>50KB** | **SKIP** |
| 2 | `SUCCESS` | 缺失 / 过小 | **只补下载**（复用台账里已存的 `url`，**零额外扣费**） |
| 3 | 非终态 + 有 `task_id` + **`prompt` 未变** | — | **复用 taskId 续轮询**（`RESU`） |
| 4 | 非终态但 **`prompt` 已变** | — | **必须重提交**（旧图作废） |
| — | `--dry-run` | — | **不跳过任何项**，全部重写提示词（否则模板改了也刷不出来） |

**踩过的三个坑**：

1. **`--dry-run` 曾跳过已完成项** → 改了模板，`prompts/` 里还是旧措辞，白白用旧提示词出图。
2. **SKIP 判定只看"文件存在"** → 下载超时留下的 **0 字节残 file** 被当成已完成（实测 CH17）。
   现在判定是 **存在 + `>50KB`**；**事后校验还必须用 PIL `Image.open().load()` 真解码**——
   0 字节和**截断 PNG** 都能骗过 `exists()` 和 `size>50KB`。
3. **先下载再写台账** → 下载超时后台账没有 URL，只能重新提交（多花 9 币）。
   现在**先把 `url` 落台账、再下载**。

**并发**：`asyncio.Semaphore(5)` —— 平台硬限，不是可调优参数。

---

## 五、产物校验（提交后必做）

```python
from PIL import Image
im = Image.open(p); im.load()      # 真解码；截断文件会在这里抛异常
```

三项产物校验：① PIL 真解码 81/81；② 无 `<50KB` 小图，长边 `≥1024`；③ 台账全 `SUCCESS` 且 `file` 指向真实存在的文件。

**QA 目检**：用脚本拼三张 contact sheet（人物/场景/道具各一张网格图）再一次性目检，
比逐张打开快得多——**道具的"跑偏"（画成真马、游戏套装）就是这样一眼看出来的**。
