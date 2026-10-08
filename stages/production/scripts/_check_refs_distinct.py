import sys,os,itertools
sys.stdout.reconfigure(encoding="utf-8")
try:
    from PIL import Image
except Exception as e:
    print("no PIL:",e); sys.exit(0)
D="video-pipeline/data/refs_h3"
files=sorted(f for f in os.listdir(D) if f.endswith(".jpg"))
sig={}
for f in files:
    im=Image.open(os.path.join(D,f)).convert("L").resize((8,8))
    sig[f]=list(im.getdata())
def dist(a,b): return sum(abs(x-y) for x,y in zip(a,b))/64
print("== 参考图两两差异（8x8 灰度平均绝对差，0=完全相同）==")
worst=[]
for a,b in itertools.combinations(files,2):
    d=dist(sig[a],sig[b]); worst.append((d,a,b))
worst.sort()
print("最接近的 5 对：")
for d,a,b in worst[:5]: print("   %.2f  %s <-> %s"%(d,a,b))
print("差异最大的一对： %.2f  %s <-> %s"%(worst[-1][0],worst[-1][1],worst[-1][2]))
import hashlib
h={}
for f in files:
    h[f]=hashlib.sha256(open(os.path.join(D,f),"rb").read()).hexdigest()[:12]
dup=[k for k,v in h.items() if list(h.values()).count(v)>1]
print("完全相同的文件（应为空）:", dup or "无")
