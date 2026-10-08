# troubleshooting.md —— 现象 → 原因 → 处理

> 处理完任何一项，都用 `run.py --resume <batch_id>` 续跑；不要不带 --resume 重跑全量。

## 1. `PIL.UnidentifiedImageError` / "not a valid image"（裁剪阶段）

**现象**：`shot_XXX/` 阶段 ② 报错，目录里 `banana_raw.png` 无法打开，或旁边出现了 `banana_raw_extracted.png`。
**原因**：香蕉工作流有时回传 zip 压缩包；引擎把 zip 解到了 `banana_raw_extracted.png`，但裁剪阶段仍读 `banana_raw.png`（此时是 zip 字节）→ **已知缺口，必崩**。
**临时处理**：把 `banana_raw_extracted.png` 复制覆盖 `banana_raw.png`，然后 `run.py --only <shot_id> --resume <batch_id>`。
**根治**：登记在「代码加固待办」，需要改 `pipeline.py` 让裁剪阶段使用解包后的实际路径。

## 2. 连续失败 5 个直接熔断，前面的错误还没看清

**原因**：`FUSE_MIN_SAMPLES=4` 意味着前 4 个 shot 即使全错也不参与「失败率」熔断；而 `FUSE_CONSECUTIVE_FAILS` 默认 5。
**处理**：首次跑新工作流时，在 `config.local.json` 里加 `"fuse_consecutive_fails": 2`（样例文件已预置），让第 2 个失败就熔断，省钱。

## 3. `summary.totals.total` 和日志条数对不上

**原因**：`total` 统计的是**过滤前**的全量分镜数；用了 `--limit` / `--only` 时，totals 的 total 仍是大盘数。
**处理**：以 `success/failed/skipped/pending` 四项之和为本次实际口径；汇报时说明用了过滤参数。

## 4. `--only shot_xxx` 显示成功但什么都没做

**原因**：`--only` 未命中任何 shot 时是静默成功（已知缺口）。
**处理**：先 `run.py --dry-run --strict` 确认 shot_id 拼写；注意 `--only` 与 `--limit` 的先后顺序（limit 先截断）。

## 5. probe 返回空节点列表

**原因**：① 工作流没暴露 API 输入节点；② ID 填错；③ 该工作流不属于当前 API Key 的账号；④ kind 用错（workflow vs ai-app）。
**处理**：按 ①→④ 排查；①需要在 RunningHub 工作流编辑器里把输入节点标记为 API 输入。**不许在未确认前猜 nodeId。**

## 6. 提交成功但轮询一直不结束 / 报网络错误

**原因**：瞬时网络抖动；或任务真的跑超时（图片 >15min / 视频 >60min）。
**处理**：直接 `Ctrl+C` 后 `run.py --resume <batch_id>`——引擎会复用原 taskId 只轮询，**不会重复扣费**。这是设计好的安全路径。

## 7. 401 / 403 / 余额不足 / 参数错

**处理**：**不重试**（引擎已按此实现）。401/403 → 检查 `RUNNINGHUB_API_KEY`（`configure.py show` 看打码值与来源层级）；参数错 → 回 G2 重新 probe 并核对 fieldName。

## 8. `state.json 无法解析，将作为新批次处理`

**原因**：上次进程被强杀在写盘瞬间（引擎用原子替换，概率极低）。
**处理**：该批次目录下已完成的 mp4 / 裁剪图仍在，`--resume` 会按产物存在性跳过已完成阶段，不重复扣费。

## 9. 中文路径乱码 / `.bat` 报"不是内部或外部命令"

**原因**：cmd 按 GBK 解析批处理文件，**.bat 内容里写了中文必乱**（已踩过的坑）。
**处理**：`run.bat` 内容必须 100% ASCII；命令行里含中文的路径用引号包住；Python 侧已强制 UTF-8 stdout。若仍乱，把工作区迁到纯 ASCII 路径（如 `C:/video-pipeline-ws`）。

## 10. 熔断已触发但 summary 里 `reason` 看不懂

**处理**：`fuse.reason` 原文直接转述给用户（「连续失败 N 个」或「失败率 x/y 超阈值」），再去 `output/<batch>/run.log` 里找第一条 error；错误文本已截断到 200 字符，完整原因看 `state.json` 对应 shot 的 `error` 字段。

## 11. 产物落到 Skill 包里了

**原因**：工作区解析失败（`.workspace` 指针不存在且未给 --workspace）时兜底到 `~/video-pipeline-workspace`，**不应**落到 Skill 包；若真发现产物在 `<skill>/` 下，说明手动改过配置。
**处理**：`configure.py init --workspace <正确路径>` 重写指针；把误生成的 data/output 移走；doctor 会 warn「工作区在 Skill 包内」。
