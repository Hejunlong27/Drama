# 流水线与落盘规范

## 七步与产物

| Step | 谁产出 | 产物 | 落盘位置 | `state.step` |
|---|---|---|---|---|
| 0 建项目 | `cli.py init` | project.json + drama.db + 目录树 | 项目根 | 0 |
| 1 世界观+人物 | `parallel-world-writer` 前两环 | world-bible.json（补视觉字段） | `bible/` | 1 |
| 2 锚点登记 | `cli.py import-bible` | characters.md / scenes.md / 角色卡 | `bible/`、`assets/char/<CODE>/` | 2 |
| 3 逐集大纲 | `plot-master` 五幕 | ep_outline.md | `bible/` | 3 |
| 4 FFS 剧本 | `overseas-ai-comic-script-master` 裁剪版 | ep_XX.md | `script/` | 4 |
| 5 机器校验 | `cli.py parse` + `check` | validate_ep_XX.md / .json | `logs/` | 5 |
| 6 分镜提示词 | `cli.py convert` + `seedance-storyboard` | ep_XX_seedance_input.md / ep_XX_storyboard.md | `storyboard/` | 6 |
| 7 回灌终检 | `cli.py parse --type storyboard` | 更新 db | `logs/` | 7 |

## 接力契约（不用粘贴大文本）

`project.json` 是唯一交接媒介：

```json
{
  "meta": {"slug","title","platform","total_ep","ep_seconds","style_lock"},
  "artifacts": {"world_bible","outline","script_dir","storyboard_dir"},
  "state": {"step": 6, "last_ep": 1, "ep_status": {"1": "storyboarded"}}
}
```

`ep_status` 取值：`draft`（大纲已定）→ `scripted`（剧本已校验）→ `storyboarded`（分镜已出）。

**断点续跑**：重开会话第一件事是 `python tools/cli.py report`，读 `state.step` 与 `ep_status`，从断点继续，不重头来过。

## 目录结构

```
<项目根>/
├─ project.json
├─ bible/        world-bible.json  characters.md  scenes.md  ep_outline.md  stylelock.md  qa_ep_XX.md
├─ script/       ep_01.md …
├─ storyboard/   ep_01_seedance_input.md  ep_01_storyboard.md
├─ assets/char/<CODE>/{sheet.md, ref_*.png, lora.txt}
│        scene/<CODE>/
├─ gen/images/   gen/video/
├─ prompts/      workflows/
├─ logs/         validate_ep_XX.md  validate_ep_XX.json  anchor_changes.log
├─ db/drama.db
└─ tools/        cli.py + core/    ← 从第一个项目复制过来，工具与内容分离
```

## 新建第二部剧

```bash
cp -r <旧项目>/tools  <新项目根>/
python tools/cli.py init --slug <新短名> --title "<新剧名>" --ep <集数> --sec <秒数> --style-lock "<新的STYLE LOCK>"
```

工具链无状态、与内容解耦，一部剧一个目录。

## 命令速查

```bash
PY=<python 3.10+ 解释器路径，建议已装 httpx/Pillow 的 venv>
export PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8

$PY tools/cli.py init --slug X --title "Y" --ep 20 --sec 110
$PY tools/cli.py import-bible
$PY tools/cli.py parse --ep 1
$PY tools/cli.py check --ep 1
$PY tools/cli.py convert --ep 1
$PY tools/cli.py parse --ep 1 --type storyboard
$PY tools/cli.py reindex            # 从 Markdown 全量重建索引
$PY tools/cli.py report             # 项目总览 + 断点状态
$PY tools/cli.py set-anchor --code LIN_YU --anchor "新锚点" --reason "原因"
$PY tools/cli.py gen --engine runninghub --ep 1 --shot SC-03   # stub
```

## 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `FOREIGN KEY constraint failed` | db 被删过但 project 行没重建 | 重跑一次 `cli.py init` |
| `UnicodeEncodeError: gbk` | 脚本输出含 emoji | 已在代码中改用 `[OK]`/`[FAIL]`；新增 print 也禁止 emoji |
| `PermissionError` / `unable to open database file` | 沙箱禁止写该盘 | 复制到会话工作区跑，产物再同步回目标盘 |
| 分镜解析少一镜 | 最后一个镜头后没有 `HARD CUT` 且紧接 `[音色锚定]` | 解析器已 flush；若仍少，确认 `镜头N（约X秒）` 格式正确 |
