# 踩坑目录（现象 / 根因 / 修法）

> 每一条都是**真出过事**并定位到根因的。排查异常时先扫这张表。

## A. 环境与调用

| 现象 | 根因 | 修法 |
|---|---|---|
| `[FAIL] 未设置 RUNNINGHUB_API_KEY`（明明配过） | agent 的 shell **继承不到用户级环境变量**（`setx` 写的是 `HKCU\Environment`） | **走 `drama.py`**（内部 `hydrate_env()` 用 `winreg` 补齐）；不要直接跑 `gen_*_assets.py` |
| `找不到短剧工作区` | 没设 `DRAMA_WORKSPACE`，且脚本不在 `<WS>/scripts/` 下 | 设环境变量或用 `--workspace` |
| `subtitle.py` 报「找不到配置 `<skill>/video-pipeline/config.subtitle.json`」 | 引擎住在 Skill 包里，`__file__` 推出的是 Skill 目录 | 加 `--workspace "<WS>"` 或设 `DRAMA_WORKSPACE` |
| 上传接口报 `code=1 API Key不存在`（但 Key 是对的） | **站点用错**：上传接口在 `.cn` 会失败，账号在 `.ai` | `config.local.json` 的 `runninghub_base_url` 必须是 `https://www.runninghub.ai` |
| 双击 .bat 后 Python 抛 `UnicodeEncodeError` | 控制台是 GBK，打印 `[OK]`/`⇒`/`￥` 崩 | .bat 用 **GBK** 保存；脚本按 `isatty()` 分流编码 |
| 引擎 `doctor` 报 `image_providers['banana']` FAIL | 本线走 `--video-only`，不需要图片 provider | 归为**已知噪音**，不算真问题 |

## B. 资产与参考图

| 现象 | 根因 | 修法 |
|---|---|---|
| 整段人物/场景串位 | `character_refs` 顺序与 `<Picture N>` 不一致 | `wire_episode_refs.py <集> --apply` |
| `assets --submit --only 甲乙丙丁` 只出了 1 张（且退出码 0） | 旧版生成器把 `return 0` 写在**逐项循环内** | 已修（计数 + break + 聚合码）。**核对收尾汇总行**「本轮完成：成功 N / 失败 M」 |
| 生成了但引擎报「找不到 key」 | 产出躺在 `output/assets/`，`characters.json` 里没这个 key | `wire_new_assets.py --apply` |
| 参考图超上限没生效 | 6000×3376 > H3 上限 5760 | `wire_new_assets.py` 会瘦身到 2048×1152 再落 `refs_h3/` |

## C. 出片

| 现象 | 根因 | 修法 |
|---|---|---|
| 报「工作流运行失败」 | 平台侧一次性失败（实测 9.1%，**未计费**） | `--resume <批次> --only <段>` 补跑，**别改提示词** |
| 重复扣费 | 没带 `--resume` 就全量重跑 | 续跑必须 `--resume <批次>` |
| 以为「1 段 55 RH币」做小样预算 | 那是 3 秒档的旧数据 | 8–14 秒档实测 **78–225**，按 145/保守 210 估 |
| 想调高并发加速 | 消费级 API 同时调用上限就是 **5**（平台硬限） | 不得调高，也不得多开脚本绕过 |

## D. 字幕（层 2 引擎，2026-09-20 修了 4 个真缺陷）

| 现象 | 根因 | 修法 |
|---|---|---|
| **整集字幕做不出来**，堆栈报对齐器 `AssertionError: n != i` | CTC 需要「音频帧数 ≥ 目标 token 数」；罗马化后每汉字≈4–5 token ⇒ **短窗口装不下长句**。旧版直接抛异常**炸掉整集** | 新版改三级降级（整句 → 按标点切片 → 按剩余时长估算）。**先确认层 2 的 `subtitle.py` 是新版** |
| 整集被断言中止：`语速异常（>12 字/秒）` | 旧版把「>12 字/秒」当**致命**错误；实测 12.9 字/秒是**真实语速**（AI 配音把长台词压进短片长，whisper 独立转写证实） | 新版：>12 只**告警**；> `cps_impossible`（zh 20 / es 40）才判错位并中止 |
| **开头几秒有人声却没字幕** | CTC 的搜索窗口 `[cursor, 段尾]` 对**第一句**来说远长于台词真实跨度，模型自由挑子区间，**起点偏晚**（实测 0.00 被放到 1.84 / 3.96 / 5.44 / 5.76 秒） | 新版「段首覆盖修复」：把该段第一条字幕起点前移盖住段首人声 |
| 后几句被压成 250 字/秒、整集崩 | 同上病根发生在**非首句**：某句被放晚 ⇒ 吃光剩余音频 ⇒ 后面只能估算 | 新版「多档窗口择优 + 相邻台词连续性约束」 |
| 字幕对不上语音（整体偏移） | 集内**段偏移**算错 | 用 `nb_read_frames/24` 累加，别用容器 duration |
| `AssertionError: <star> != 好` | 中文没加 `--romanize` | 加 `--romanize` |
| `UnicodeDecodeError: 'gbk'` | 对齐器用系统默认编码读文本 | 设 **`PYTHONUTF8=1`** |
| 字幕变豆腐块 | 字体名错 / 字体缺该语言字符 | 中文 `Microsoft YaHei`；**西语禁用 `simhei`**（缺 `ñ Ñ ¿ ¡`） |
| 覆盖检查把音乐当语音 | 用 silencedetect 判「有语音」 | 改用 **whisper 转写结果**判语音 |

> 🔴 **旧文档「whisper 时间戳太粗、不用于字幕时间」是错的** ——
> `whisper-cli -ml 1` 的**逐字**时间戳在 CTC 出错的疑难段上**明显优于 CTC**（CTC 把首句推后 5.44 秒，
> whisper 给出正确的 0.00 起点）。**排查对齐问题先跑它取证。**

## E. 运维纪律（都是真事故）

| 事故 | 教训 |
|---|---|
| 用 `Remove-Item "...\*\ep0*_*subbed.mp4"` 清临时文件，**匹配并删掉了 7 个交付成片** | **清理不许用通配符**。要删写全名，或先 `-WhatIf`。（母版/ASS/对齐缓存完好，复用缓存重渲染 5 分钟复原） |
| 有 Skill 声明「本包的 engine 是真源」，但项目里那份被改过 ⇒ 两份分叉 | **改引擎先改声明的那份再同步**；分叉后 Skill 会产出坏结果。改完用 `Get-FileHash` 核对两侧一致 |
