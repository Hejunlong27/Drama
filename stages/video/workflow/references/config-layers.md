# config-layers.md —— 配置分层与回填规则

## 1. 三层结构（优先级从低到高，高者覆盖低者）

| 层 | 位置 | 谁能写 | 内容 |
|---|---|---|---|
| ① 引擎默认 | `<skill>/scripts/engine/config.py` | **谁都不能改**（结构 + 默认值） | 注册表骨架、并发 / 重试 / 熔断参数 |
| ② 私有覆盖 | `<工作区>/config.local.json` | **Agent 唯一被允许写的层**（经 configure.py） | Key、workflow ID、节点映射、参数微调 |
| ③ 环境变量 | 进程环境 | 用户 / Agent 帮配 | **只放机密**：`RUNNINGHUB_API_KEY`、`RUNNINGHUB_BASE_URL` |

引擎加载顺序：`main.py` 解析工作区 → `config.apply_overlay(<工作区>/config.local.json)` → `--provider` 临时覆盖 → `missing_configs()`。

## 2. 工作区解析（高 → 低）

1. `--workspace <路径>`
2. 环境变量 `VIDEO_PIPELINE_WORKSPACE`
3. `<引擎>/.workspace` 指针文件（一行绝对路径，由 `configure.py init` 写入）
4. `~/video-pipeline-workspace`（兜底，**绝不在 Skill 包内**）

`--data-dir` / `--out-dir`：绝对路径原样用；相对路径**相对工作区**解析（⚠️ 这与旧版「相对引擎目录」不同）。

## 3. config.local.json 允许覆盖的键（白名单）

```
runninghub_api_key / runninghub_base_url / http_timeout
active_image_provider
image_providers.<name>.{label, kind, id, provider, instance_type, nodes.*}
h3.{kind, id, frame_source, nodes.*}
concurrency / batch_size / poll_interval / poll_channel
image_timeout_min / video_timeout_min
retry_max / retry_backoff / error_text_limit
fuse_consecutive_fails / fuse_fail_rate / fuse_min_samples
default_crop / upload_cache_ttl_hours / keep_raw_zip
data_dir / out_dir
```

**忽略**：下划线开头的键（注释约定，如 `_comment`）。
**拒绝并警告**（不崩溃、不静默，警告会出现在 doctor / show / main 的输出里）：未知键、类型不符、`poll_channel` 非 `v2|status|auto`、`frame_source` 非 `cell_01|grid_raw`、`kind` 非 `workflow|ai-app`。
**自动收敛**：`concurrency > 5` → 收敛为 5 并警告。
**Key 铁律**：`runninghub_api_key` 只在环境变量未设置时生效；`doctor.py` / `configure.py show` 只打印打码值。

## 4. config.local.json 示例

```json
{
  "_comment": "由 configure.py 维护；下划线开头的键视为注释",
  "active_image_provider": "banana",
  "image_providers": {
    "banana": {
      "kind": "workflow",
      "id": "<你的工作流id>",
      "nodes": {
        "prompt":    { "nodeId": "64", "fieldName": "text" },
        "ratio":     { "nodeId": "70", "fieldName": "aspectRatio" },
        "char_ref":  { "nodeId": "82", "fieldName": "image", "multi": true },
        "scene_ref": { "nodeId": "83", "fieldName": "image", "multi": true }
      }
    }
  },
  "h3": {
    "kind": "workflow",
    "id": "19xxxxxxxxxxxxxxxxx",
    "frame_source": "cell_01",
    "nodes": {
      "prompt":      { "nodeId": "12", "fieldName": "text" },
      "first_frame": { "nodeId": "20", "fieldName": "image" },
      "duration":    { "nodeId": "25", "fieldName": "duration" }
    }
  },
  "concurrency": 5,
  "fuse_consecutive_fails": 2,
  "poll_channel": "v2",
  "data_dir": "data",
  "out_dir": "output"
}
```

节点项字段：`nodeId`（非空字符串）、`fieldName`（非空字符串）、`multi`（布尔，true 时多值序列化成 JSON 数组字符串）。
图片模型节点角色：`prompt`（必填）/ `ratio`（可选）/ `char_ref` `scene_ref`（图生图必填）或二者合并为单个 `image`。
H3 节点角色：`prompt` `first_frame`（必填）/ `char_ref` `scene_ref` `grid_ref` `duration` `ratio`（可选，空则自动跳过）。

## 5. configure.py 子命令速查

| 命令 | 作用 |
|---|---|
| `init --workspace <路径>` | 建工作区 + 铺样例 + 写 `.workspace` 指针 |
| `set-workspace <路径>` | 只更新指针 |
| `set-key --value <key>` / `--from-env VAR` | 写 Key（兜底；环境变量优先） |
| `set-provider --provider banana --kind workflow --id <ID>` | 写图片工作流 ID |
| `set-h3 --kind workflow --id <ID> [--frame-source cell_01]` | 写 H3 工作流 ID |
| `apply-nodes --from probe.json --target banana\|h3 [--yes]` | 回填节点映射（不带 --yes 只展示） |
| `set --key <白名单键> --value <v>` | 任意白名单键 |
| `unset --key a.b.c` | 删除 |
| `show` | 生效配置 + 层级回执（Key 打码） |

## 6. 排障：「到底用的哪个 ID」

1. `configure.py show` —— 每个生效值都标了来源层级；
2. `doctor --json` 的 `engine_sha8` —— 确认跑的是哪份引擎代码；
3. `output/<batch>/summary.json` 的 `config` 段 —— 该批次实际用的 provider / ID / 并发 / 轮询通道快照。
