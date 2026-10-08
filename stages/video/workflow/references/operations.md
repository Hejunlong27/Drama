# operations.md —— 关卡通关条件与逐字确认话术

> 每次执行前读本文件。核心纪律：**逐关卡推进，关末停下等用户确认，严禁一次跑完。**

## 🛑 G0 环境体检

**命令**：`run.py doctor --json`

**通过条件**：无 `level=FAIL` 项。`api_key / provider_id / h3_id / nodes` 允许 FAIL，但必须把 `next_action` 转述给用户并进入配置引导（SKILL.md 第四节）。

**汇报模板**：
```
[体检] Python / 依赖 / 引擎 / 工作区 / 输入数据：…
[缺失] <逐条列出 FAIL 项 + 修复方式>
[下一步] <next_action>
```

**确认话术**：不需要用户确认即可继续 G0→G1（全是免费操作），但必须先汇报结果。

---

## 🛑 G1 工作区就位

**命令**：
```
configure.py init --workspace <路径>     # 首次
run.py --dry-run --strict                # 预演，零网络请求
```

**通过条件**：工作区可写；`data/` 四个 JSON 齐且校验零 problem；dry-run 逐 shot 打印出 nodeInfoList。

**⚠️ 必做**：把 dry-run 打印的 nodeInfoList 给用户**肉眼核对**一遍（ nodeId、fieldName、prompt 内容），这是发现配置错误最便宜的一道关。

**确认话术**：
> 预演通过，X 个分镜将按以下方式提交（已列出 nodeInfoList 摘要）。确认无误我就进入节点探测。

---

## 🛑 G2 节点探测回填

**命令**：
```
run.py probe --target banana --emit probe.json
run.py probe --target h3     --emit probe.json
configure.py apply-nodes --from probe.json --target banana --yes
configure.py apply-nodes --from probe.json --target h3     --yes
run.py check
```

**通过条件**：probe 返回了节点；候选片段经用户**逐项确认**；`run.py check` 无缺失。

**红线**：`confidence=low / none` 的项一律要用户手填，不许猜。`probe --write` 已被停用，回填必须走 `apply-nodes`（它有 `--yes` 二次确认，且不带 `--yes` 时只展示不写入）。

**确认话术**：
> 探测到 N 个可填节点，候选映射如下（★ 标出的项置信度低，请确认）：
> `prompt -> nodeId=64 / text` …
> 确认无误我就回填 config.local.json。

---

## 🛑 G3 计费确认①（首次真实执行前）

**命令**：`check_billing.py --resume <batch_id>`（新批次则不带 --resume，并明确告知这是全新扣费）

**通过条件**：用户明确回复「同意 / 跑」之后，才允许执行任何不带 `--dry-run` 的命令。

**确认话术（必须完整告知，不许省略）**：
> ⚠️ 下一步将向 RunningHub 发起**真实计费任务**：
> - 将新建任务：图片 N 个 + 视频 M 个 = N+M 次调用
> - 将跳过：K 个（已完成）
> - 预计新批次 ID：`<batch_id>`（或续跑 `<batch_id>`，已完成阶段不会重复扣费）
> - 首次只跑 1 条（`--limit 1`）验证链路，跑通后我再找你确认全量
> 确认执行吗？

---

## 🛑 G4 小样验证（`--limit 1`，★ 首次计费）

**命令**：`run.py --limit 1`（续跑场景：`run.py --limit 1 --resume <batch_id>`）

**通过条件**：`output/<batch>/shot_001/final.mp4` 存在且 >0 字节；`report.py` 显示 `totals.success>=1`；**用户看过成片并认可**。

**汇报模板**：
```
[小样] shot_001 完成，耗时 …s，花费 …
[产物] <final.mp4 绝对路径>
[实际 usage] consumeMoney=… consumeCoins=…
[推算] 单条 ≈ …，全量 X 条 ≈ …
```

**确认话术**：
> 小样已生成（路径如上）。请看片确认质量。确认后我将执行剩余 X 条（预计花费 ≈ …），是否继续？

---

## 🛑 G5 计费确认② + 全量 / 续跑

**命令**：`run.py --resume <batch_id>`（默认必须带 --resume；新建批次必须显式确认并说明会重复扣费）

**通过条件**：用户在 G4 的确认话术后明确同意。

**执行中**：不要打断；出现熔断立即停下进入 G6 并转述 `fuse.reason`。

---

## 🛑 G6 交付汇报

**命令**：`report.py --latest --json`

**要求**：按 SKILL.md 第八节固定四段输出；失败项必须给重跑命令（SKILL.md 第九节）。
