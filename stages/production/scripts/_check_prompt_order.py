import json,re,sys
sys.stdout.reconfigure(encoding="utf-8")
D="video-pipeline/data/"
shots=json.load(open(D+"shots.json",encoding="utf-8"))
pr=json.load(open(D+"h3_prompts.json",encoding="utf-8"))
NAME={"char_01":"陆鸣","char_02":"大姨","char_03":"二舅","char_04":"表哥","char_05":"陆母",
      "char_06":"三婶","char_07":"林晚","char_08":"陆族长","char_09":"陆大伯","scene_01":"场景1"}
def subs(p):
    out=[]
    for m in re.finditer(r"<Subject (\d+)> 是 ([^\n]*)",p):
        t=m.group(2)
        mn=re.search(r"<Picture (\d+)>",t)
        nm=re.search(r"中的(.+?)形象",t)
        out.append((int(m.group(1)), int(mn.group(1)) if mn else 0, nm.group(1) if nm else None, t))
    return out
bad=[]; okc=0
for s in shots:
    sid=s["shot_id"]; p=pr.get(sid,{}).get("prompt","")
    ss=subs(p)
    charsubs=[x for x in ss if x[2]]
    refs=s.get("character_refs") or []
    exp=[NAME.get(k,k) for k in refs]
    got=[x[2] for x in charsubs]
    pidx=[x[1] for x in charsubs]
    prob=[]
    if got!=exp: prob.append("顺序/数量不符 exp=%s got=%s"%(exp,got))
    if pidx!=list(range(1,len(pidx)+1)): prob.append("Picture序号非连续1..N: %s"%pidx)
    if prob: bad.append((sid,prob))
    else: okc+=1
print("== 全库一致性 ==")
print("一致 %d / 共 %d"%(okc,len(shots)))
print()
for sid,prob in bad:
    print(sid,"|","; ".join(prob))
