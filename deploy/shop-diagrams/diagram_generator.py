#!/usr/bin/env python3
"""Generate P&ID and electrical one-line SVGs for Raging Buffalo Glass Studio.
Schematic (not to scale). Pressures/ampacities marked TBD where unknown.
Regenerate: python3 gen.py"""

# ---- palette ----
NG="#B9770E"; PROP="#2E7D32"; OX="#C2185B"; AIR="#1565C0"; FAIR="#00897B"
EXH="#E64A19"; ELEC="#37474F"; INK="#1A1A1A"; BOXF="#F4F2EC"; STA="#9E9E9E"

def esc(s): return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

class SVG:
    def __init__(self,w,h,title):
        self.w=w; self.h=h; self.title=title; self.e=[]
    def txt(self,x,y,s,size=13,anchor="middle",color=INK,weight="normal",style=""):
        self.e.append(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" '
                      f'fill="{color}" font-weight="{weight}" font-family="Helvetica,Arial,sans-serif" '
                      f'style="{style}">{esc(s)}</text>')
    def box(self,x,y,w,h,label,sub=None,fill=BOXF,stroke=INK):
        self.e.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{fill}" stroke="{stroke}" stroke-width="1.3"/>')
        if sub:
            self.txt(x+w/2,y+h/2-2,label,13,"middle",INK,"bold")
            self.txt(x+w/2,y+h/2+13,sub,11,"middle","#555")
        else:
            self.txt(x+w/2,y+h/2+4,label,13,"middle",INK,"bold")
    def pipe(self,pts,color,w=2.2,dash=None,arrow=False):
        d=f' stroke-dasharray="{dash}"' if dash else ""
        a=' marker-end="url(#ah)"' if arrow else ""
        p=" ".join(f"{x},{y}" for x,y in pts)
        self.e.append(f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="{w}"{d}{a}/>')
    def tee(self,x,y,color=INK):
        self.e.append(f'<circle cx="{x}" cy="{y}" r="3.2" fill="{color}"/>')
    def valve(self,cx,cy,color=INK,tag=None,horiz=True):
        r=11
        if horiz:
            self.e.append(f'<path d="M{cx-r} {cy-r} L{cx-r} {cy+r} L{cx} {cy} Z" fill="#fff" stroke="{color}" stroke-width="1.6"/>')
            self.e.append(f'<path d="M{cx+r} {cy-r} L{cx+r} {cy+r} L{cx} {cy} Z" fill="#fff" stroke="{color}" stroke-width="1.6"/>')
        else:
            self.e.append(f'<path d="M{cx-r} {cy-r} L{cx+r} {cy-r} L{cx} {cy} Z" fill="#fff" stroke="{color}" stroke-width="1.6"/>')
            self.e.append(f'<path d="M{cx-r} {cy+r} L{cx+r} {cy+r} L{cx} {cy} Z" fill="#fff" stroke="{color}" stroke-width="1.6"/>')
        if tag: self.txt(cx,cy-r-5,tag,10,"middle","#333")
    def reg(self,cx,cy,color=INK,tag=None):
        self.valve(cx,cy,color)
        # spring/adjust stem on top = pressure regulator
        self.e.append(f'<line x1="{cx}" y1="{cy-11}" x2="{cx}" y2="{cy-24}" stroke="{color}" stroke-width="1.4"/>')
        self.e.append(f'<path d="M{cx-7} {cy-24} L{cx+7} {cy-24} M{cx-7} {cy-24} L{cx+7} {cy-30} M{cx-7} {cy-30} L{cx+7} {cy-30}" stroke="{color}" stroke-width="1.4" fill="none"/>')
        if tag: self.txt(cx,cy+22,tag,10,"middle","#333")
    def needle(self,cx,cy,color=INK,tag=None):
        self.valve(cx,cy,color)
        self.e.append(f'<line x1="{cx}" y1="{cy-16}" x2="{cx}" y2="{cy}" stroke="{color}" stroke-width="1.4"/>')
        self.e.append(f'<path d="M{cx} {cy} L{cx-3} {cy-6} L{cx+3} {cy-6} Z" fill="{color}"/>')
        if tag: self.txt(cx,cy+20,tag,10,"middle","#333")
    def footvalve(self,cx,cy,color=INK,tag=None):
        self.valve(cx,cy,color)
        self.e.append(f'<rect x="{cx-13}" y="{cy+11}" width="26" height="7" rx="2" fill="none" stroke="{color}" stroke-width="1.4"/>')
        self.e.append(f'<line x1="{cx}" y1="{cy+11}" x2="{cx}" y2="{cy+18}" stroke="{color}" stroke-width="1"/>')
        if tag: self.txt(cx,cy-16,tag,10,"middle","#333")
    def smallbox(self,cx,cy,label,w=54,h=28,color=INK):
        self.e.append(f'<rect x="{cx-w/2}" y="{cy-h/2}" width="{w}" height="{h}" rx="3" fill="#fff" stroke="{color}" stroke-width="1.3"/>')
        self.txt(cx,cy+4,label,10,"middle",INK)
    def inst(self,cx,cy,tag,field=True,r=16,color=INK):
        self.e.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#fff" stroke="{color}" stroke-width="1.4"/>')
        if field: self.e.append(f'<line x1="{cx-r}" y1="{cy}" x2="{cx+r}" y2="{cy}" stroke="{color}" stroke-width="1"/>')
        parts=tag.split("-")
        if len(parts)==2 and field:
            self.txt(cx,cy-3,parts[0],10,"middle",INK,"bold")
            self.txt(cx,cy+11,parts[1],9,"middle","#444")
        else:
            self.txt(cx,cy+4,tag,10,"middle",INK,"bold")
    def damper(self,cx,cy,color=INK,tag=None,w=30,h=22):
        self.e.append(f'<rect x="{cx-w/2}" y="{cy-h/2}" width="{w}" height="{h}" fill="none" stroke="{color}" stroke-width="1.4"/>')
        self.e.append(f'<line x1="{cx-w/2+3}" y1="{cy+h/2-3}" x2="{cx+w/2-3}" y2="{cy-h/2+3}" stroke="{color}" stroke-width="1.6"/>')
        if tag: self.txt(cx,cy-h/2-5,tag,10,"middle","#333")
    def motor(self,cx,cy,tag="M",r=17,color=INK):
        self.e.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#fff" stroke="{color}" stroke-width="1.4"/>')
        self.txt(cx,cy+5,"M",14,"middle",INK,"bold")
        if tag and tag!="M": self.txt(cx,cy-r-5,tag,10,"middle","#333")
    def breaker(self,cx,cy,label=None,color=ELEC):
        # square with hinged switch line
        s=16
        self.e.append(f'<rect x="{cx-s/2}" y="{cy-s/2}" width="{s}" height="{s}" fill="#fff" stroke="{color}" stroke-width="1.4"/>')
        self.e.append(f'<line x1="{cx}" y1="{cy+s/2}" x2="{cx+7}" y2="{cy-s/2-4}" stroke="{color}" stroke-width="1.6"/>')
        if label: self.txt(cx+s,cy+4,label,10,"start","#333")
    def doorswitch(self,cx,cy,color=ELEC):
        self.e.append(f'<line x1="{cx}" y1="{cy-10}" x2="{cx}" y2="{cy-4}" stroke="{color}" stroke-width="2"/>')
        self.e.append(f'<line x1="{cx}" y1="{cy+10}" x2="{cx}" y2="{cy+4}" stroke="{color}" stroke-width="2"/>')
        self.e.append(f'<circle cx="{cx}" cy="{cy-4}" r="2" fill="{color}"/>')
        self.e.append(f'<line x1="{cx}" y1="{cy-4}" x2="{cx+9}" y2="{cy+6}" stroke="{color}" stroke-width="2"/>')
        self.e.append(f'<circle cx="{cx+9}" cy="{cy+6}" r="2.6" fill="none" stroke="{color}" stroke-width="1.4"/>')
    def heater(self,cx,cy,color=ELEC,label=None,n=4,w=64,h=16):
        # resistor-ish zigzag
        x0=cx-w/2; step=w/(n*2)
        pts=[(x0,cy)]
        for i in range(n*2):
            pts.append((x0+step*(i+0.5), cy-h/2 if i%2==0 else cy+h/2))
        pts.append((x0+w,cy))
        p=" ".join(f"{x:.1f},{y:.1f}" for x,y in pts)
        self.e.append(f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="1.6"/>')
        if label: self.txt(cx,cy+h/2+14,label,10,"middle","#333")
    def render(self):
        head=(f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
              f'viewBox="0 0 {self.w} {self.h}" font-family="Helvetica,Arial,sans-serif">'
              f'<defs><marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" '
              f'orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" '
              f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>'
              f'<rect width="{self.w}" height="{self.h}" fill="#ffffff"/>')
        return head+"".join(self.e)+"</svg>"
    def save(self,path):
        open(path,"w").write(self.render())

# ============================= P&ID =============================
s=SVG(1200,1060,"pid")
s.txt(600,34,"Raging Buffalo Glass Studio — Piping & Instrumentation Diagram",19,"middle",INK,"bold")
s.txt(600,54,"McGuffey Art Center basement, Charlottesville VA · not to scale · pressures TBD · rev F",12,"middle","#666")

# --- NG band ---
yn=120
s.box(40,yn-22,150,44,"city natural gas")
s.pipe([(190,yn),(250,yn)],NG); s.valve(250,yn,NG,"HV-101"); s.pipe([(261,yn),(325,yn)],NG)
s.reg(350,yn,NG,"PCV-101"); s.inst(350,yn-52,"PI-101",color=NG); s.pipe([(350,yn-36),(350,yn-11)],NG)
s.pipe([(361,yn),(470,yn)],NG); s.tee(470,yn,NG)
# branch to furnace burner
s.pipe([(470,yn),(470,84),(545,84)],NG); s.valve(560,84,NG,"HV-102"); s.pipe([(571,84),(700,84)],NG,arrow=True)
s.box(700,64,200,40,"furnace burner")
# branch to glory hole burner
s.pipe([(470,yn),(470,170),(545,170)],NG); s.valve(560,170,NG,"HV-103"); s.pipe([(571,170),(700,170)],NG,arrow=True)
s.box(700,150,220,40,"glory-hole burner")
# furnace temp control loop
s.inst(820,120,"TE-101"); s.pipe([(800,90),(820,90),(820,104)],ELEC,1.2,"3,3")
s.inst(905,120,"TIC-101"); s.txt(905,150,"analog PID",10,"middle","#444")
s.pipe([(836,120),(889,120)],ELEC,1.2,"3,3")
s.pipe([(921,120),(985,120)],ELEC,1.2,"3,3",arrow=True); s.txt(1068,124,"→ MicroFusion SCR (one-line)",11,"middle",ELEC)

# --- Propane band ---
yp=250
s.box(40,yp-22,150,44,"propane tank")
s.pipe([(190,yp),(250,yp)],PROP); s.valve(250,yp,PROP,"HV-201"); s.pipe([(261,yp),(325,yp)],PROP)
s.reg(350,yp,PROP,"PCV-201"); s.inst(350,yp-52,"PI-201",color=PROP); s.pipe([(350,yp-36),(350,yp-11)],PROP)
s.pipe([(361,yp),(540,yp)],PROP)
# --- Oxygen band ---
yo=360
s.box(40,yo-22,150,44,"oxygen")
s.pipe([(190,yo),(250,yo)],OX); s.valve(250,yo,OX,"HV-301"); s.pipe([(261,yo),(325,yo)],OX)
s.reg(350,yo,OX,"PCV-301"); s.inst(350,yo+52,"PI-301",color=OX); s.pipe([(350,yo+11),(350,yo+36)],OX)
s.pipe([(361,yo),(560,yo)],OX)

# --- torch station ---
s.e.append('<rect x="510" y="205" width="505" height="212" rx="8" fill="none" stroke="'+STA+'" stroke-width="1.2" stroke-dasharray="6,4"/>')
s.txt(524,223,"torch station",11,"start",STA,"bold")
# propane vertical header x540 (taps 232 & 332), O2 vertical header x560 (taps 272 & 372)
s.pipe([(540,232),(540,332)],PROP); s.pipe([(560,272),(560,372)],OX)
def torch(name,pyv,oyv,bx_w,tag_p,tag_o,fa_p,fa_o,box_lbl):
    s.pipe([(540,pyv),(596,pyv)],PROP); s.needle(620,pyv,PROP,tag_p); s.pipe([(644,pyv),(680,pyv)],PROP); s.smallbox(712,pyv,fa_p,color=PROP); s.pipe([(739,pyv),(800,pyv)],PROP,arrow=True)
    s.pipe([(560,oyv),(596,oyv)],OX); s.needle(620,oyv,OX,tag_o); s.pipe([(644,oyv),(680,oyv)],OX); s.smallbox(712,oyv,fa_o,color=OX); s.pipe([(739,oyv),(800,oyv)],OX,arrow=True)
    cy=(pyv+oyv)//2
    s.box(800,cy-24,bx_w,48,box_lbl)
torch("reheat",232,272,170,"NV-201","NV-301","FA-201","FA-301","reheat torch")
torch("precision",332,372,180,"NV-202","NV-302","FA-202","FA-302","precision torch")

# --- Compressed air band ---
ya=520
s.box(40,ya-22,150,44,"air compressor")
s.pipe([(190,ya),(250,ya)],AIR); s.smallbox(285,ya,"receiver",w=70,color=AIR); s.pipe([(320,ya),(360,ya)],AIR)
s.smallbox(400,ya,"FRL-401",w=70,color=AIR); s.pipe([(435,ya),(500,ya)],AIR)
s.inst(500,ya-50,"PI-401",color=AIR); s.txt(500,ya-74,"~90 psi",10,"middle",AIR); s.pipe([(500,ya-34),(500,ya)],AIR)
s.tee(500,ya,AIR); s.pipe([(500,ya),(540,ya)],AIR); s.tee(540,ya,AIR)
# leg to furnace crucible lift
s.pipe([(540,ya),(540,470),(596,470)],AIR); s.footvalve(620,470,AIR,"FV-401"); s.pipe([(644,470),(760,470)],AIR,arrow=True)
s.box(760,450,230,40,"crucible lift cylinder")
# leg to glory-hole doors
s.pipe([(540,ya),(540,575),(596,575)],AIR); s.footvalve(620,575,AIR,"FV-402"); s.pipe([(644,575),(760,575)],AIR,arrow=True)
s.box(760,555,250,40,"glory-hole door cylinders")

# --- Forced / combustion air band ---
yf=680
s.box(40,yf-22,160,44,"forced-air blower")
s.pipe([(200,yf),(280,yf)],FAIR); s.damper(305,yf,FAIR,"DMP-501"); s.pipe([(320,yf),(700,yf)],FAIR,arrow=True)
s.box(700,yf-20,240,40,"glory-hole combustion air")

# --- Exhaust band ---
ye=780
s.box(40,ye-22,170,44,"canopy hood")
s.pipe([(210,ye),(300,ye)],EXH); s.damper(325,ye,EXH,"DMP-601"); s.pipe([(340,ye),(520,ye)],EXH)
s.motor(548,ye,"FAN-601"); s.pipe([(565,ye),(700,ye)],EXH,arrow=True)
s.box(700,ye-20,170,40,"discharge to outside")

# --- legend / key ---
ly=880
s.e.append(f'<line x1="40" y1="{ly-16}" x2="1160" y2="{ly-16}" stroke="#ccc" stroke-width="1"/>')
s.txt(40,ly,"line service",12,"start",INK,"bold")
items=[("nat gas",NG),("propane",PROP),("oxygen",OX),("shop air",AIR),("forced air",FAIR),("exhaust",EXH),("control",ELEC)]
x=150
for lbl,c in items:
    dash='4,3' if lbl=="control" else None
    s.pipe([(x,ly-4),(x+34,ly-4)],c,3,dash)
    s.txt(x+40,ly,lbl,12,"start",c); x+=140
s.txt(40,ly+28,"symbols",12,"start",INK,"bold")
sx=150; sy=ly+24
s.valve(sx,sy,INK); s.txt(sx+16,sy+4,"manual valve",11,"start")
sx=300; s.reg(sx,sy,INK); s.txt(sx+16,sy+4,"regulator (PCV)",11,"start")
sx=470; s.needle(sx,sy,INK); s.txt(sx+16,sy+4,"needle valve",11,"start")
sx=610; s.footvalve(sx,sy,INK); s.txt(sx+16,sy+4,"foot valve",11,"start")
sx=740; s.inst(sx,sy,"PI-",r=14); s.txt(sx+18,sy+4,"gauge / instrument",11,"start")
sx=930; s.smallbox(sx,sy,"FA",w=34,h=22); s.txt(sx+22,sy+4,"flashback arrestor",11,"start")
s.txt(40,ly+58,"TBD: propane/O2/forced-air pressures · door & lift cylinder counts · FRL & flashback part numbers",11,"start","#888")
s.save("/mnt/user-data/outputs/raging_buffalo_glass_PID.svg")

# ========================= ONE-LINE =========================
o=SVG(1180,900,"oneline")
o.txt(590,34,"Raging Buffalo Glass Studio — Electrical one-line",19,"middle",INK,"bold")
o.txt(590,54,"208 V, 1Ø, 3-wire (L1-L2-G) · McGuffey basement, Charlottesville VA · ampacities TBD · rev F",12,"middle","#666")

# service + main
o.box(500,80,180,40,"utility 208 V 1Ø")
o.pipe([(590,120),(590,150)],ELEC,2.4)
o.inst(590,168,"kWh",field=False,r=16); o.pipe([(590,184),(590,205)],ELEC,2.4)
o.breaker(590,220,"main breaker  (A: TBD)")
o.pipe([(590,236),(590,262)],ELEC,2.4)
o.box(505,262,170,30,"panelboard","(breaker box, east wall)")
# bus
busy=310
o.pipe([(120,busy),(1080,busy)],ELEC,2.6)
o.pipe([(590,292),(590,busy)],ELEC,2.4)

drops=[
    (170,"furnace"),
    (330,"annealer 1"),
    (470,"annealer 2"),
    (610,"annealer 3"),
    (760,"blower"),
    (890,"compressor"),
    (1010,"exhaust fan"),
]
for x,_ in drops:
    o.pipe([(x,busy),(x,busy+30)],ELEC,2.0)

# furnace branch: 100 A feeder -> subpanel -> two 60 A breakers -> MicroFusion SCR -> door interlock -> SiC
fx=170
o.txt(fx+10,busy+22,"100 A feeder",10,"start","#444")
o.box(fx-78,busy+30,156,34,"furnace subpanel","behind furnace")
o.pipe([(fx,busy+64),(fx,busy+78)],ELEC,2.0)
o.pipe([(fx-40,busy+78),(fx+40,busy+78)],ELEC,2.0)
o.pipe([(fx-40,busy+78),(fx-40,busy+90)],ELEC,2.0); o.breaker(fx-40,busy+100,"60 A")
o.pipe([(fx+40,busy+78),(fx+40,busy+90)],ELEC,2.0); o.breaker(fx+40,busy+100,"60 A")
o.pipe([(fx-40,busy+108),(fx-40,busy+122),(fx,busy+122)],ELEC,2.0)
o.pipe([(fx+40,busy+108),(fx+40,busy+122),(fx,busy+122)],ELEC,2.0)
o.pipe([(fx,busy+122),(fx,busy+134)],ELEC,2.0)
o.box(fx-78,busy+134,156,44,"MicroFusion SCR","1Ø · 100 A")
o.pipe([(fx,busy+178),(fx,busy+196)],ELEC,2.0)
o.doorswitch(fx,busy+206)
o.txt(fx+16,busy+202,"door interlock",10,"start","#444")
o.txt(fx+16,busy+215,"(cuts elements)",9,"start","#888")
o.pipe([(fx,busy+216),(fx,busy+264)],ELEC,2.0)
ry=busy+264
for i in range(4):
    rx=fx-54+i*36
    o.e.append(f'<line x1="{rx}" y1="{ry}" x2="{rx}" y2="{ry+38}" stroke="{ELEC}" stroke-width="3"/>')
o.pipe([(fx-54,ry),(fx+54,ry)],ELEC,2.0)
o.pipe([(fx-54,ry+38),(fx+54,ry+38)],ELEC,2.0)
o.txt(fx+66,busy+258,"4 × SiC rods",11,"start",INK,"bold")
o.txt(fx,ry+56,"config TBD (series/parallel)",10,"middle","#888")
# furnace control: K-type -> analog PID -> SCR ; plus Novus N20K48
o.inst(fx-118,busy+156,"TIC-101"); o.txt(fx-118,busy+184,"analog PID",10,"middle","#444")
o.pipe([(fx-102,busy+156),(fx-78,busy+156)],ELEC,1.2,"3,3",arrow=True)
o.inst(fx-118,busy+236,"TE-101"); o.txt(fx-118,busy+261,"K-type",10,"middle","#444")
o.pipe([(fx-118,busy+220),(fx-118,busy+172)],ELEC,1.2,"3,3")
o.txt(fx-118,busy+300,"+ Novus N20K48",10,"middle","#444")

# annealers 1-3
for x,name in [(330,"annealer 1"),(470,"annealer 2"),(610,"annealer 3")]:
    o.breaker(x,busy+44,"60 A 2P")
    o.pipe([(x,busy+52),(x,busy+80)],ELEC,2.0)
    o.box(x-52,busy+80,104,40,"Hg relay","60 A mercury")
    o.pipe([(x,busy+120),(x,busy+146)],ELEC,2.0)
    o.heater(x,busy+160,label="coils")
    o.txt(x,busy+238,name,11,"middle","#333")
    o.inst(x+70,busy+100,"TIC",r=14); o.txt(x+70,busy+122,"N20K48",10,"middle","#444")
    o.pipe([(x+56,busy+100),(x+52,busy+100)],ELEC,1.2,"3,3",arrow=True)
# each device has its own Novus N20K48 (drawn per annealer above; furnace loop at left)

# motors
for x,name in [(760,"forced-air blower"),(890,"air compressor"),(1010,"exhaust fan")]:
    o.breaker(x,busy+44,"CB")
    o.pipe([(x,busy+52),(x,busy+90)],ELEC,2.0)
    o.motor(x,busy+108)
    o.txt(x,busy+140,name,11,"middle","#333")

# note
o.txt(120,860,"Also on panel: 120 V lighting & receptacles (L-N) — not shown. TBD: main & branch breaker sizes, SiC rod wattage/wiring, motor HP/FLA.",11,"start","#888")
o.save("/mnt/user-data/outputs/raging_buffalo_glass_oneline.svg")

print("wrote both SVGs")
