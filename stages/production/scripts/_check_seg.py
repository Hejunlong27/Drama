import json, os, pathlib

ws = pathlib.Path(os.environ.get('DRAMA_WORKSPACE') or r'<你的工作区>\video-pipeline')
shots = json.loads((ws / 'data' / 'shots.json').read_text(encoding='utf-8'))
h3 = json.loads((ws / 'data' / 'h3_prompts.json').read_text(encoding='utf-8'))
chars = json.loads((ws / 'data' / 'characters.json').read_text(encoding='utf-8'))

sid = 'ep03_seg01'
s = next((x for x in shots if x.get('shot_id') == sid), None)
print('=' * 72)
print('目标分镜:', sid)
if not s:
    print('!! shots.json 里找不到，前 5 个 id：', [x.get('shot_id') for x in shots[:5]])
else:
    for k in ('scene', 'scene_title', 'description', 'ratio'):
        print('  %-12s %s' % (k, s.get(k)))
    refs = s.get('character_refs') or []
    print('  %-12s %s  (共 %d 张)' % ('character_refs', refs, len(refs)))
    for r in refs:
        print('        %-10s -> %s' % (r, chars.get(r)))
    print('  %-12s %s' % ('scene_refs', s.get('scene_refs')))

p = h3.get(sid) or {}
print()
print('h3_prompts.json[%s]:' % sid)
print('  duration =', repr(p.get('duration')))
print('  ratio    =', repr(p.get('ratio')))
pl = p.get('prompt') or ''
print('  prompt 长度 =', len(pl), '字')
print('  prompt 开头 200 字:')
print('   ', pl[:200].replace('\n', ' '))

print()
print('=' * 72)
print('全库时长分布（确认 3 秒是否存在于数据中）')
from collections import Counter
c = Counter(v.get('duration') for v in h3.values() if isinstance(v, dict))
for k in sorted(c, key=lambda x: (x is None, x)):
    print('   %-6s x%d' % (k, c[k]))
print()
print('参考图数最多/最少的段：')
cnt = sorted(((len(x.get('character_refs') or []), x.get('shot_id')) for x in shots))
print('   最少:', cnt[:4])
print('   最多:', cnt[-4:])
print()
print('ep03 全部段的参考图数：')
for x in shots:
    if str(x.get('shot_id', '')).startswith('ep03'):
        print('   %-14s refs=%d  dur=%s' % (
            x.get('shot_id'), len(x.get('character_refs') or []),
            (h3.get(x.get('shot_id')) or {}).get('duration')))
