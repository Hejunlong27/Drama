import json,re,sys
sys.stdout.reconfigure(encoding="utf-8")
D="video-pipeline/data/"
shots=json.load(open(D+"shots.json",encoding="utf-8"))
pr=json.load(open(D+"h3_prompts.json",encoding="utf-8"))
def sect(p,name):
    m=re.search(r"\n"+name+r":\n(.*?)(?=\n[a-z_]+:|\Z)",p,re.S)
    return m.group(1) if m else ""
print("== 每段：提示词引用的最大 <Picture N>  vs  实际会发送的参考图数 ==")
print("%-14s %-6s %-8s %-8s %s"%("shot","imgs","subj_maxP","ret_maxP","判定"))
dangling=[]
for s in shots:
    sid=s["shot_id"]; p=pr.get(sid,{}).get("prompt","")
    n=len(s.get("character_refs") or [])
    sd=sect(p,"subject_definitions"); ra=sect(p,"retention_analysis")
    sp=[int(x) for x in re.findall(r"<Picture (\d+)>",sd)]
    rp=[int(x) for x in re.findall(r"<Picture (\d+)>",ra)]
    mx=max(sp) if sp else 0
    flag=""
    if mx>n: flag="★ 引用 P%d 但只发 %d 张"%(mx,n); dangling.append((sid,mx,n))
    if sid.startswith("ep03"):
        print("%-14s %-6d %-8s %-8s %s"%(sid,n,(max(sp) if sp else "-"),(max(rp) if rp else "-"),flag or "OK"))
print()
print("== 全库悬空引用（提示词要的图 > 实际提供）共 %d 段 =="%len(dangling))
for sid,mx,n in dangling: print("   %-14s 需要 P%d，实发 %d"%(sid,mx,n))
