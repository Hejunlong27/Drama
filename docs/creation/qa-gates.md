# 三门质量检查

> 从 `overseas-ai-comic-script-master` 的四级复核裁剪而来。
> 放弃：一级（被 G1 覆盖）、四级片场可行性（AI 生成不适用）、双语交付、PWW 终检九项（小说维度）。

## G1 格式门 — Python 全自动，必须清零

```bash
python tools/cli.py check --ep N
```

| 码 | 检查项 | 典型修复 |
|---|---|---|
| E001 | HEADER 6 行齐全且顺序正确 | 补齐 TITLE/EP/PLATFORM/STYLE LOCK/CHAR/SCENE |
| E002 | CHAR 锚点与库内 hash 不一致 | 抄回 `bible/characters.md` 的原文；真要改走 `set-anchor` |
| E003 | 引用未登记代号 | 先在 world-bible 登记，再 `import-bible` |
| E004 | 出现非法字段（`MOOD:`/`CAMERA:` 等） | 删掉，只允许 8 个标准字段 + `DUR` |
| E005 | `SC-xx` 编号不连续/跳号 | 重排序号 |
| E006 | 时长越界（单镜 4–15 秒 / 单集 6–15 镜 / 总 90–120 秒） | 调 `DUR` 或增删镜头 |
| E007 | 对白 >2 句或单句 >20 字 | 砍短 |
| E008 | VO 缺说话者前缀 | 加 `NARRATOR:` 或 `角色名 (V.O.):` |
| E009 | ACTION 无主体代号/角色名，或含抽象词 | 写具体动作：把「气氛凝重」改成「他握紧钢笔，指节发白」 |
| E010 | 分镜台词与剧本**逐字**不一致 | 以剧本原文为准修正分镜 |
| E011 | 分镜 STYLE LOCK 未包含项目风格常量 | 把 `bible/stylelock.md` 的英文常量整段贴回分镜 |

报告落 `logs/validate_ep_XX.md` + `.json`。**有 ERROR 就不许进下一环。**

## G2 一致性门 — 脚本 + 人眼 5 分钟

| 检查 | 怎么看 |
|---|---|
| 角色锚点 | 打开 `bible/characters.md`，逐条确认本集 HEADER 的锚点一字不差 |
| STYLE LOCK | 本集 HEADER 与 `bible/stylelock.md` 完全一致（E011 只查分镜，HEADER 要人眼确认） |
| 台词逐字 | 分镜里的台词与 `script/ep_XX.md` 的 `DIALOG` 逐字比对（E010 已自动查，这里抽查语气标点） |
| 跨集代号 | 本集新出现的角色/场景，是否已回写 world-bible 并 `import-bible` |
| 道具造型 | 关键道具（如旧机械表、钢笔）描述是否与首集一致 |

**注意**：脚本只能查代号，查不出语义漂移（比如「这集林宇穿了红外套」）。靠锚点冻结 + 分镜阶段注入锚定行来防，人眼兜底。

## G3 剧情门 — LLM 三问，一页纸，落 `bible/qa_ep_XX.md`

1. **因果链**：本集每个角色的行为，动机是否在上文有铺垫？有没有「为了推进剧情而降智」？
2. **钩子**：集尾钩子是否真的让观众想看下一集？它与上一集的钩子是否形成闭环（上集落点是否已回收）？
3. **声线/OOC**：把本集所有对白遮住角色名朗读，能否分辨出是谁在说话？有没有角色说了不符合其身份/处境的话？

三问都过才交付。任一不过，回 Step 4 改剧本，不靠改分镜补救。

## 批量生产时的抽检节奏

- 每集必跑 G1（自动，成本为零）。
- 每 3 集做一次完整 G2。
- 每 5 集做一次 G3，外加一次「全剧 recheck」：

```bash
python tools/cli.py reindex
for i in $(seq 1 N); do python tools/cli.py check --ep $i; done
```
