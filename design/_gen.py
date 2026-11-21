import math, random, os
OUT=__import__("os").path.dirname(__import__("os").path.abspath(__file__))+"/"
random.seed(7)

# ---------- svg helpers ----------
def dia(x,y,r,cls="sar"):
    pts=f"{x},{y-r} {x+r},{y} {x},{y+r} {x-r},{y}"
    if cls=="cand":
        return (f'<polygon points="{x},{y-r-5} {x+r+5},{y} {x},{y+r+5} {x-r-5},{y}" fill="none" stroke="var(--c-candidate-ring)" stroke-width="1.5" opacity=".6"/>'
                f'<polygon points="{pts}" fill="var(--c-candidate)" stroke="var(--c-candidate-ring)" stroke-width="2"/>')
    return f'<polygon points="{pts}" fill="none" stroke="var(--c-sar)" stroke-width="1.6"/>'
def ship(x,y,r=3.2): return f'<circle cx="{x}" cy="{y}" r="{r}" fill="var(--c-ais)"/>'
def gap(ax,ay,bx,by):
    return (f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="var(--c-gap)" stroke-width="2" stroke-dasharray="6 5"/>'
            f'<circle cx="{ax}" cy="{ay}" r="5" fill="var(--c-sea)" stroke="var(--c-gap)" stroke-width="2"/>'
            f'<circle cx="{bx}" cy="{by}" r="4.5" fill="var(--c-gap)"/>')
def rdv(x,y,s=1):
    return (f'<circle cx="{x}" cy="{y}" r="{20*s}" fill="var(--c-rdv-fill)" stroke="var(--c-rdv)" stroke-width="1.5" stroke-dasharray="3 3"/>'
            f'<circle cx="{x}" cy="{y}" r="{11*s}" fill="none" stroke="var(--c-rdv)" stroke-width="2.5"/>'
            f'<circle cx="{x}" cy="{y}" r="{4*s}" fill="var(--c-rdv)"/>')
_id=[0]
def uid(p="c"):
    _id[0]+=1; return f"{p}{_id[0]}"
def lens(ax,ay,bx,by,vt,f,fill="var(--c-prism-fill)",stroke="var(--c-prism-stroke)",inner=""):
    a,b=uid("a"),uid("b")
    ra,rb=vt*f,vt*(1-f)
    return (f'<defs><clipPath id="{a}"><circle cx="{ax}" cy="{ay}" r="{max(ra,.1):.1f}"/></clipPath></defs>'
            f'<g clip-path="url(#{a})"><circle cx="{bx}" cy="{by}" r="{max(rb,.1):.1f}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/></g>'
            f'<circle cx="{ax}" cy="{ay}" r="{max(ra,.1):.1f}" fill="none" stroke="{stroke}" stroke-width="1" stroke-dasharray="2 4" opacity=".55"/>'
            f'<circle cx="{bx}" cy="{by}" r="{max(rb,.1):.1f}" fill="none" stroke="{stroke}" stroke-width="1" stroke-dasharray="2 4" opacity=".55"/>')
def footprint(ax,ay,bx,by,vt,dash=True):
    cx,cy=(ax+bx)/2,(ay+by)/2; d=math.hypot(bx-ax,by-ay); ang=math.degrees(math.atan2(by-ay,bx-ax))
    a=vt/2; b=math.sqrt(max(a*a-(d/2)**2,1))
    return f'<ellipse cx="{cx}" cy="{cy}" rx="{a}" ry="{b}" transform="rotate({ang} {cx} {cy})" fill="none" stroke="var(--c-prism-stroke)" stroke-width="1.5" stroke-dasharray="1 5" stroke-linecap="round" opacity=".8"/>'
def txt(x,y,s,c="var(--c-text-3)",sz=12,anchor="start",fam="var(--font-sans)"):
    return f'<text x="{x}" y="{y}" fill="{c}" font-size="{sz}" text-anchor="{anchor}" font-family="{fam}">{s}</text>'

# stand-in Baltic coastline, 1000x760 canvas (not geographic; shapes only)
LAND=[
 "M0,0 L1000,0 L1000,60 L930,70 L880,130 L820,120 L790,60 L700,50 L650,95 L560,85 L520,60 L470,80 L420,70 L380,110 L330,100 L290,150 L240,140 L190,200 L150,330 L110,400 L130,470 L100,560 L60,640 L90,720 L0,760 Z",
 "M0,760 L90,720 L140,700 L210,730 L300,700 L400,720 L520,690 L600,720 L700,700 L780,740 L860,700 L1000,720 L1000,760 Z",
 "M860,700 L880,600 L930,520 L920,430 L960,340 L1000,300 L1000,720 Z",
 "M560,360 L610,340 L640,380 L660,450 L630,520 L590,500 L560,430 Z",   # island
 "M255,560 L300,545 L345,570 L335,610 L280,620 Z",                    # islet
]
def base(w=1000,h=760,vb=None,land=True,grid=True):
    g=''.join(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}" stroke="var(--c-grid)"/>' for x in range(0,w,100))
    g+=''.join(f'<line x1="0" y1="{y}" x2="{w}" y2="{y}" stroke="var(--c-grid)"/>' for y in range(0,h,100))
    l=''.join(f'<path d="{p}" fill="var(--c-land)" stroke="var(--c-land-stroke)" stroke-width="1.5"/>' for p in LAND) if land else ''
    return g+l
def pip(x,y,poly):
    pts=[tuple(map(float,p.split(','))) for p in poly.replace('M','').replace('Z','').strip().split('L')]
    ins=False;j=len(pts)-1
    for i in range(len(pts)):
        xi,yi=pts[i];xj,yj=pts[j]
        if (yi>y)!=(yj>y) and x<(xj-xi)*(y-yi)/(yj-yi)+xi: ins=not ins
        j=i
    return ins
def on_land(x,y): return any(pip(x,y,p) for p in LAND)
def ships(n,box=(60,60,940,700),avoid=()):
    out=[];t=0
    while len(out)<n and t<5000:
        t+=1
        x=random.uniform(box[0],box[2]);y=random.uniform(box[1],box[3])
        if on_land(x,y): continue
        if any(math.hypot(x-ax,y-ay)<r for ax,ay,r in avoid): continue
        out.append((round(x),round(y)))
    return out
def legend(items):
    rows={
     "ais":('<circle cx="13" cy="7" r="3.5" fill="var(--c-ais)"/>',"Ship broadcasting (AIS)"),
     "gap":('<line x1="1" y1="7" x2="25" y2="7" stroke="var(--c-gap)" stroke-width="2" stroke-dasharray="5 4"/>',"AIS gap: last ping to reappearance"),
     "prism":('<ellipse cx="13" cy="7" rx="11" ry="5.5" fill="var(--c-prism-fill)" stroke="var(--c-prism-stroke)" stroke-width="1.5"/>',"Where it could have been (prism)"),
     "sar":('<polygon points="13,1 20,7 13,13 6,7" fill="none" stroke="var(--c-sar)" stroke-width="1.6"/>',"Radar detection (SAR)"),
     "cand":('<polygon points="13,1 20,7 13,13 6,7" fill="var(--c-candidate)" stroke="var(--c-sar)" stroke-width="2"/>',"Detection inside a prism: candidate"),
     "rdv":('<circle cx="13" cy="7" r="6" fill="none" stroke="var(--c-rdv)" stroke-width="2.2"/><circle cx="13" cy="7" r="2" fill="var(--c-rdv)"/>',"Possible meeting: prisms overlap"),
    }
    return '<div class="legend">'+''.join(f'<div><svg viewBox="0 0 26 14">{rows[k][0]}</svg>{rows[k][1]}</div>' for k in items)+'</div>'
ATTR='<div class="attr">Powered by Global Fishing Watch</div>'

# ---------- page shell ----------
def topbar(active,mode="live",region="Baltic Sea",extra=""):
    navs=[("Overview","01-overview.html"),("Case file","02-case-file.html"),("Rendezvous","03-rendezvous.html"),("Live","04-live.html"),("Edge unit","05-edge.html")]
    n=''.join(f'<a href="{h}"{" aria-current=page" if t==active else ""}>{t}</a>' for t,h in navs)
    b={"snap":'<span class="badge snap"><span class="d"></span>SNAPSHOT 14 Nov 2026 18:00 UTC</span>',
       "live":'<span class="badge live"><span class="d"></span>LIVE</span>',
       "none":""}[mode]
    return (f'<header class="topbar"><div class="brand">Dark Fleet</div><nav class="nav">{n}</nav><div class="spacer"></div>'
            f'<div class="ctl">{region}<small>region</small></div><div class="ctl">1 to 30 Nov 2026<small>period</small></div>{b}{extra}</header>')
def page(fn,title,body,notes,filetag):
    ns=''.join(f'<li>{t}</li>' for t in notes)
    PINH=''.join(f'<span class="pin" style="left:{x}px;top:{y}px">{n}</span>' for n,x,y in PINS.get(fn,[]))
    html=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=1800">
<title>{title}</title><link rel="stylesheet" href="tokens.css"><link rel="stylesheet" href="wire.css"></head>
<body><div class="stage"><div class="frame">{body}{PINH}</div>
<aside class="notes"><h2>{title}</h2><div class="file">{filetag} · 1440 x 900 · placeholder data</div><ol>{ns}</ol></aside></div></body></html>'''
    open(OUT+fn,'w').write(html)
def pin(n,x,y): return ''
PINS={
 "01-overview.html":[(1,990,54),(2,190,54),(3,1330,58),(4,722,742),(5,222,868)],
 "02-case-file.html":[(1,222,352),(2,522,300),(3,630,650),(4,1300,150),(5,1412,224),(6,1412,822)],
 "03-rendezvous.html":[(1,330,150),(2,604,318),(3,584,654),(4,990,760),(5,1300,58)],
 "04-live.html":[(1,722,830),(2,376,404),(3,1410,100),(4,630,56)],
 "05-edge.html":[(1,986,54),(2,748,510),(3,1100,152),(4,1160,228),(5,1260,546)],
}

MT='<span class="chip rdv">meeting</span>'
def case_row(rk,nm,flag,hrs,risk,rp,sar,sel=False,mt=False):
    c={"high":"high","med":"med","low":"low"}[risk]
    return (f'<div class="case{" sel" if sel else ""}"><div class="rk">{rk}</div><div class="nm">{nm}</div><div class="hrs">{hrs}<small>hours dark</small></div>'
            f'<div class="sub"><span>{flag}</span><span class="chip {c}">{risk.capitalize()} {rp}</span><span class="mono">{sar} in prism</span>{MT if mt else ""}</div>'
            f'<div class="bar {c}"><i style="width:{rp}%"></i></div></div>')

# ================= 01 overview =================
W,H=1000,760-0
av=[(250,330,40),(700,250,40),(450,420,40),(790,560,40),(380,170,40)]
sh=ships(95)
gaps=[(250,330,330,300,),(700,250,610,215),(450,420,560,470),(800,430,730,480),(380,170,300,140),(520,250,470,300),(150,580,230,520)]
gp=''.join(gap(*g) for g in gaps)
lenses=lens(250,330,330,300,140,.5)+lens(700,250,610,215,150,.5)+lens(450,420,560,470,170,.5)+lens(800,430,730,480,120,.5)
fp=footprint(450,420,560,470,230)
sar_all=[(300,330),(640,230),(500,430),(540,445),(760,462),(240,600),(430,230),(840,300),(180,430),(330,520),(900,420),(580,640)]
cand=[(300,330),(640,230),(505,445)]
sars=''.join(dia(x,y,6,"cand" if (x,y) in cand else "sar") for x,y in sar_all)
mapsvg=f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid slice">{base()}{lenses}{"".join(ship(x,y) for x,y in sh)}{gp}{sars}{rdv(500,445)}{rdv(765,455,.8)}' \
       + txt(840,110,"SWEDEN","var(--c-text-3)",12) + txt(130,260,"SWEDEN","var(--c-text-3)",12)+txt(615,430,"GOTLAND","var(--c-text-3)",11)+txt(930,380,"BALTIC STATES","var(--c-text-3)",11,"end")+txt(500,745,"POLAND","var(--c-text-3)",12)+'</svg>'
kp=[("1,284","Ships tracked",""),("37","Dark events","k-gap"),("912","Hours dark","k-gap"),("9","Candidate sightings","k-sar"),("2","Possible meetings","k-rdv")]
kpis='<div class="kpis">'+''.join(f'<div class="kpi {c}"><div class="n">{n}</div><div class="l">{l}</div></div>' for n,l,c in kp)+'</div>'
cl=[("1","TANKER-0412","Flag A",'31',"high",86,2,True,True),("2","TANKER-1187","Flag B","27","high",79,1,False,True),("3","BULKER-0093","Flag C","44","med",64,0),("4","TANKER-2250","Flag A","19","med",58,1),("5","CARGO-0731","Flag D","22","med",51,0),("6","TANKER-0888","Flag B","12","low",33,0),("7","FISHING-0156","Flag E","9","low",27,0),("8","TANKER-3302","Flag C","7","low",21,0)]
rows=''.join(case_row(*c) if len(c)>7 else case_row(*c,False) for c in cl)
body=topbar("Overview","snap")+f'''<div class="body"><div class="main">{kpis}<div class="map">{mapsvg}<div class="maptag"><span class="chip">Region: Baltic Sea</span><span class="chip">Showing all 37 dark events</span></div>{legend(["ais","gap","prism","sar","cand","rdv"])}{ATTR}{pin(1,10,10)}{pin(2,16,34).replace('top:34px','top:-0px').replace('left:16px','left:966px')}</div></div>
<aside class="rail"><div class="rail-h"><h2>Cases, highest risk first</h2><span>37 total</span></div><div class="cases">{rows}</div>{pin(3,404,10)}</aside></div>{pin(4,980,56)}{pin(5,970,820)}'''
page("01-overview.html","01 Overview",body,[
 "<b>Grid.</b> 1000 px map + 440 px case rail. The map is the answer; the rail is the worklist. Both stay on every screen so the eye never relearns the layout.",
 "<b>KPI strip above the map, not beside it.</b> Five numbers at 44 px mono, ordered as the story: tracked, dark events, hours dark, candidates, meetings. Colour of the number = colour of its map symbol (amber, blue, pink).",
 "<b>Ranked case list.</b> Sorted by risk score. Each row: rank, id, hours dark (largest number, amber), risk chip with a text label and a bar (never colour alone), SAR candidates in prism, meeting chip. Click opens the case file (screen 02).",
 "<b>Symbols are redundant on purpose.</b> Gap = dashed line, SAR = diamond, meeting = double ring. Hue and shape both carry meaning, so a colour-blind judge still reads the map. Legend always visible.",
 "<b>Attribution</b> \"Powered by Global Fishing Watch\" bottom-left on every map. Mode badge (live / snapshot) in the top bar, never hidden.",
 "Wireframe shapes stand in for coastline; values are placeholders, not data."],"01-overview.html")
# fix pin 2 placement (map top-right)
s=open(OUT+"01-overview.html").read()
open(OUT+"01-overview.html",'w').write(s)

# ================= 02 case file =================
MW,MH=1000,520
A=(250,340);B=(720,230);D=math.hypot(B[0]-A[0],B[1]-A[1]);VT=D*1.22
def case_map(f,mini=False,scene=True):
    inner=base(MW,MH,land=False)
    if scene:
        inner+=f'<path d="M0,0 L1000,0 L1000,120 L880,110 L800,60 L690,90 L560,40 L420,70 L300,40 L180,90 L0,60 Z" fill="var(--c-land)" stroke="var(--c-land-stroke)" stroke-width="1.5"/>'
        inner+=f'<path d="M0,430 L120,470 L260,440 L420,500 L1000,470 L1000,520 L0,520 Z" fill="var(--c-land)" stroke="var(--c-land-stroke)" stroke-width="1.5"/>'
    inner+=footprint(*A,*B,VT)
    inner+=lens(*A,*B,VT,f)
    for x,y in [(420,330),(520,210),(610,300),(160,200),(800,400),(880,160),(330,230),(660,420)]: inner+=ship(x,y)
    sarpts=[(490,290),(580,255),(400,180),(820,320),(300,420)]
    for i,(x,y) in enumerate(sarpts): inner+=dia(x,y,6,"cand" if i<2 else "sar")
    inner+=gap(*A,*B)
    # ship position estimate marker at t (center of lens)
    cx=A[0]+(B[0]-A[0])*f; cy=A[1]+(B[1]-A[1])*f
    inner+=f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="4" fill="var(--c-text-1)"/>' 
    if not mini:
        inner+=txt(A[0]-8,A[1]+26,"Last ping 03 Nov 14:20","var(--c-text-1)",13,"end")+txt(B[0]+10,B[1]-14,"Reappears 04 Nov 21:20","var(--c-text-1)",13)
        inner+=txt(300,150,"Dotted outline: everywhere it could have been during the whole gap","var(--c-text-3)",12)
    return f'<svg viewBox="0 0 {MW} {MH}" preserveAspectRatio="xMidYMid slice">{inner}</svg>'
fr=''.join(f'<figure class="{"on" if i==2 else ""}"><svg viewBox="0 0 {MW} {MH}">{case_map(f,True)[case_map(f,True).index(">")+1:-6]}</svg><figcaption>t {int(f*100)}% · {int(f*31)} h</figcaption></figure>' for i,f in enumerate([0.0,.25,.5,.75,1.0]))
# timeline scrubber
def scrub(x0=0,w=968):
    t=f'<svg viewBox="0 0 {w} 64" width="100%" height="64"><rect x="0" y="26" width="{w}" height="12" rx="2" fill="var(--c-gap)" opacity=".28"/><rect x="0" y="26" width="{w*.55:.0f}" height="12" rx="2" fill="var(--c-gap)"/>'
    for i in range(4,32,4): 
        x=w*i/31; t+=f'<line x1="{x:.0f}" y1="42" x2="{x:.0f}" y2="50" stroke="var(--c-text-3)"/>'+txt(x,62,f"+{i}h","var(--c-text-3)",11,"middle","var(--font-mono)")
    for fx in (.40,.66): t+=dia(w*fx,14,5,"cand")
    t+=txt(w*.40+12,18,"radar pass 1","var(--c-text-3)",11)+txt(w*.66+12,18,"radar pass 2","var(--c-text-3)",11)
    t+=f'<line x1="{w*.55:.0f}" y1="8" x2="{w*.55:.0f}" y2="44" stroke="var(--c-text-1)" stroke-width="2"/><rect x="{w*.55-7:.0f}" y="20" width="14" height="24" rx="3" fill="var(--c-text-1)"/>'
    return t+'</svg>'
tl=f'''<div class="tl"><div class="tl-h"><span>Drag to move through the gap. The prism at time t is the overlap of two speed-limited circles.</span><span class="t">t = 03 Nov 14:20 + 17 h</span></div>{scrub()}<div class="frames" style="margin-top:var(--sp-2)">{fr}</div>{pin(3,-2,-4).replace('left:-2px','left:972px')}</div>'''
factors=[("Gap length","31 h","28",90,"Median gap in region: 4 h"),("Net distance needed","5.1 kn","14",34,"Ship max assumed 15 kn: gap is physically plausible"),("Radar detections in prism","2","24",70,"Both with no AIS ship within 3 nm"),("Prior gaps, last 90 days","3","12",40,"Same vessel, GFW gap events"),("Overlaps another prism","1","8",25,"Possible meeting with TANKER-1187")]
fh=''.join(f'<div class="factor"><span>{a}</span><span class="v">{b}</span><div class="bar high"><i style="width:{d}%"></i></div><small>+{c} points · {e}</small></div>' for a,b,c,d,e in factors)
rail=f'''<aside class="rail"><div class="rail-h"><h2>TANKER-0412</h2><span class="mono">MMSI 2xxxxxxxx · IMO 9xxxxxx</span></div>
<div class="sec" style="display:flex;justify-content:space-between;align-items:flex-end"><div class="hero" style="font-size:52px">86<small>risk score of 100</small></div><span class="chip high">High</span></div>
<div class="sec"><div class="match"><span class="hero" style="font-size:28px;color:var(--c-risk-high)">!</span><div><b>Possible sanctions list match</b><br>Name and IMO match 1 entry. Needs analyst confirmation.</div></div></div>
<div class="sec"><h3>Why it was flagged</h3>{fh}</div>
<div class="sec"><h3>Data sources</h3><div class="row"><span>Global Fishing Watch gap event<small>fetched 14 Nov 17:40 UTC</small></span><span class="v">v3</span></div><div class="row"><span>Global Fishing Watch radar<small>scenes 4 Nov 02:51, 14:08 UTC</small></span><span class="v">2 scenes</span></div><div class="row"><span>OpenSanctions vessels<small>updated 13 Nov 2026</small></span><span class="v">1 hit</span></div></div>
<div class="sec" style="border:0"><h3>Caveats</h3><ul class="cav"><li>Speed limit is a vessel-class default (15 kn), not measured.</li><li>An AIS gap can be a coverage hole, not a choice to hide.</li><li>Radar detections are grid cells; data is non-commercial.</li></ul></div>{pin(4,10,10).replace('left:10px','left:404px')}{pin(5,404,150)}{pin(6,404,242)}</aside>'''
body=topbar("Case file","snap")+f'''<div class="body"><div class="main"><div class="map">{case_map(.55)}<div class="maptag"><span class="chip">Cases</span><span class="chip">TANKER-0412</span><span class="chip gap">Gap 31 h</span><span class="chip sar">2 candidate sightings</span></div>{legend(["ais","gap","prism","cand"])}{ATTR}{pin(1,10,44)}{pin(2,454,300)}</div>{tl}</div>{rail}</div>'''
page("02-case-file.html","02 Case file",body,[
 "<b>Map zooms to the gap</b> and frames A (last ping, hollow) and B (reappearance, filled). Dashed amber line = the gap itself; dotted outline = every place the ship could have been.",
 "<b>The prism at time t</b> is the translucent violet lens. White dot = its centre at t. Radar diamonds inside the lens turn white with a blue ring: candidate sightings.",
 "<b>Scrubber.</b> One handle from t0 to t1, tick marks in hours, radar passes marked on the track. Large mono time readout. Five frames below show the lens at 0, 25, 50, 75, 100 % so a judge sees the lens move without touching anything; in the app the same lens animates at 1 h per 0.3 s.",
 "<b>Evidence rail.</b> Score first (72 px), then the sanctions badge, then factors with their numbers and points, then sources with fetch time, then caveats. The score is always decomposable.",
 "<b>Sanctions badge says \"possible\".</b> We match name and IMO; a human confirms. Red outline, text label, not colour alone.",
 "<b>Caveats are a first-class block</b>, not a footnote. Rigour you can see is a judging criterion."],"02-case-file.html")

# ================= 03 rendezvous =================
X=((230,390),(520,250));Y=((640,350),(860,440))
VX=math.dist(*X)*1.8; VY=math.dist(*Y)*2.4
WX=(0,.62); WY=(.34,1.0)
def rv_map(t,mini=False):
    fx=(t-WX[0])/(WX[1]-WX[0]); fy=(t-WY[0])/(WY[1]-WY[0])
    inner=base(MW,MH,land=False)
    inner+='<path d="M0,0 L1000,0 L1000,90 L850,80 L700,40 L500,70 L300,30 L0,60 Z" fill="var(--c-land)" stroke="var(--c-land-stroke)" stroke-width="1.5"/>'
    inner+=footprint(*X[0],*X[1],VX)+footprint(*Y[0],*Y[1],VY)
    inner+=lens(*X[0],*X[1],VX,fx)+lens(*Y[0],*Y[1],VY,fy)
    ids=[uid("o") for _ in range(4)]
    cl=[(X[0],VX*fx),(X[1],VX*(1-fx)),(Y[0],VY*fy),(Y[1],VY*(1-fy))]
    defs=''.join(f'<clipPath id="{i}"><circle cx="{c[0][0]}" cy="{c[0][1]}" r="{max(c[1],.1):.1f}"/></clipPath>' for i,c in zip(ids,cl))
    inner+=f'<defs>{defs}</defs><g clip-path="url(#{ids[0]})"><g clip-path="url(#{ids[1]})"><g clip-path="url(#{ids[2]})"><g clip-path="url(#{ids[3]})"><rect width="{MW}" height="{MH}" fill="var(--c-rdv)" opacity=".6"/></g></g></g></g>'
    for x,y in [(330,200),(420,440),(700,160),(900,270),(150,200),(930,470),(300,480),(760,250)]: inner+=ship(x,y)
    inner+=gap(*X[0],*X[1])+gap(*Y[0],*Y[1])
    inner+=dia(600,300,6,"cand")+dia(450,330,6)+dia(800,330,6)
    inner+=rdv(584,304,1)
    if not mini:
        inner+=txt(222,424,"TANKER-0412 last ping","var(--c-text-1)",12,"end")+txt(650,338,"TANKER-1187 last ping","var(--c-text-1)",12,"start")
        inner+=txt(700,292,"Possible meeting","var(--c-text-1)",14,"start")
    return f'<svg viewBox="0 0 {MW} {MH}" preserveAspectRatio="xMidYMid slice">{inner}</svg>'
fr=''.join(f'<figure class="{"on" if i==2 else ""}"><svg viewBox="0 0 {MW} {MH}">{rv_map(f,True)[rv_map(f,True).index(">")+1:-6]}</svg><figcaption>t {int(f*50)} h</figcaption></figure>' for i,f in enumerate([.40,.44,.48,.52,.56]))
def rtl():
    w=968; t=f'<svg viewBox="0 0 {w} 118" width="100%" height="118">'
    t+=txt(0,14,"TANKER-0412 gap","var(--c-text-2)",12)+f'<rect x="0" y="20" width="{w*.62:.0f}" height="14" rx="2" fill="var(--c-gap)" opacity=".85"/>'
    t+=txt(0,60,"TANKER-1187 gap","var(--c-text-2)",12)+f'<rect x="{w*.34:.0f}" y="66" width="{w*.66:.0f}" height="14" rx="2" fill="var(--c-gap)" opacity=".85"/>'
    t+=f'<rect x="{w*.39:.0f}" y="12" width="{w*.19:.0f}" height="74" rx="2" fill="var(--c-rdv)" opacity=".28" stroke="var(--c-rdv)" stroke-width="1.5"/>'
    t+=txt(w*.39+w*.095,104,"Both could be there: 9 h","var(--c-rdv)",13,"middle")
    for i in range(0,41,5): x=w*i/40; t+=f'<line x1="{x:.0f}" y1="86" x2="{x:.0f}" y2="92" stroke="var(--c-text-3)"/>'
    t+=f'<line x1="{w*.48:.0f}" y1="4" x2="{w*.48:.0f}" y2="92" stroke="var(--c-text-1)" stroke-width="2"/><rect x="{w*.48-7:.0f}" y="38" width="14" height="22" rx="3" fill="var(--c-text-1)"/></svg>'
    return t
tl=f'''<div class="tl"><div class="tl-h"><span>Overlap window: the time when both prisms can contain a ship at the same place</span><span class="t">t = 05 Nov 02:00 + 24 h</span></div>{rtl()}<div class="frames">{fr}</div>{pin(3,972,-4)}{pin(4,570,64)}</div>'''
rail=f'''<aside class="rail"><div class="rail-h"><h2>Possible meeting</h2><span class="mono">M-0007</span></div>
<div class="sec"><div class="hero" style="color:var(--c-rdv)">9 h<small>prisms overlap</small></div></div>
<div class="sec"><h3>Ships involved</h3><div class="row"><span><b style="color:var(--c-text-1)">TANKER-0412</b><small>gap 31 h · risk 86</small></span><span class="chip high">High</span></div><div class="row"><span><b style="color:var(--c-text-1)">TANKER-1187</b><small>gap 27 h · risk 79</small></span><span class="chip high">High</span></div></div>
<div class="sec"><h3>Why it was flagged</h3>
<div class="factor"><span>Overlap duration</span><span class="v">9 h</span><div class="bar high"><i style="width:65%"></i></div></div>
<div class="factor"><span>Overlap area</span><span class="v">640 km²</span><div class="bar med"><i style="width:45%"></i></div></div>
<div class="factor"><span>Neither ship broadcasting</span><span class="v">yes</span><div class="bar high"><i style="width:100%"></i></div><small>Standard encounter detection needs AIS from both</small></div>
<div class="factor"><span>Candidate radar sighting in overlap</span><span class="v">1</span><div class="bar high"><i style="width:55%"></i></div></div></div>
<div class="sec" style="border:0"><h3>Caveats</h3><ul class="cav"><li>Overlap means they could have met, not that they did.</li><li>Larger overlap area lowers confidence.</li><li>Speed limits are class defaults.</li></ul></div>{pin(5,404,60)}</aside>'''
body=topbar("Rendezvous","snap")+f'''<div class="body"><div class="main"><div class="map">{rv_map(.48)}<div class="maptag"><span class="chip">Cases</span><span class="chip rdv">Possible meeting M-0007</span></div>{legend(["gap","prism","cand","rdv"])}{ATTR}{pin(1,10,44)}{pin(2,640,215)}</div>{tl}</div>{rail}</div>'''
page("03-rendezvous.html","03 Rendezvous",body,[
 "<b>Two lenses, one overlap.</b> Each ship keeps its own violet lens; the intersection is filled magenta and marked with a double ring. This is the screen that sells the idea: a meeting nobody saw.",
 "<b>Marker shape</b> (double ring, dashed halo) differs from every other symbol, so the pink reads even in greyscale.",
 "<b>Two-track timeline.</b> One bar per ship; the overlap window is a pink band across both with its length in hours. The handle sets t for both lenses at once.",
 "<b>Filmstrip</b> of five moments shows the overlap growing and shrinking without interaction.",
 "<b>Right rail</b> mirrors the case file: size first (9 h), who, why with numbers, caveats. The words \"could have met\" are used on purpose; we never claim a meeting happened."],"03-rendezvous.html")

# ================= 04 live =================
LW,LH=1000,760-0
sh=ships(160)
silent=[(300,300,6),(640,200,4),(520,470,2),(760,620,5)]
lv=''.join(ship(x,y,3) for x,y in sh)
sil=''
for x,y,m in silent:
    sil+=f'<circle cx="{x}" cy="{y}" r="{10+m*9}" fill="none" stroke="var(--c-gap)" stroke-width="1.5" stroke-dasharray="3 4" class="pulse"/><circle cx="{x}" cy="{y}" r="5.5" fill="var(--c-sea)" stroke="var(--c-gap)" stroke-width="2.2"/>'+txt(x+12,y-10,f"silent {m} min","var(--c-gap)",12,"start","var(--font-mono)")
sil+=gap(150,580,230,520)+f'<circle cx="230" cy="520" r="5" fill="var(--c-ais)"/>'+txt(236,512,"back on AIS","var(--c-ais)",12,"start","var(--font-mono)")
lmap=f'<svg viewBox="0 0 {LW} {LH}" preserveAspectRatio="xMidYMid slice">{base()}{lv}{sil}</svg>'
kp=[("1,284","Ships broadcasting",""),("4","Silent now","k-gap"),("38/s","Messages received",""),("0:03","Seconds since last message",""),("11","Dark events this hour","k-gap")]
kpis='<div class="kpis">'+''.join(f'<div class="kpi {c}"><div class="n">{n}</div><div class="l">{l}</div></div>' for n,l,c in kp)+'</div>'
ev=[("14:52:09","TANKER-2250","went silent","Last seen 14:52 · 11.8 kn","gap"),("14:51:40","CARGO-0731","went silent","Last seen 14:51 · 9.0 kn","gap"),("14:49:15","TANKER-0888","went silent","Last seen 14:49 · 13.2 kn","gap"),("14:47:02","FISHING-0156","back on AIS","Silent 36 min","ais"),("14:44:31","TANKER-3302","went silent","Last seen 14:44 · 10.1 kn","gap")]
evh=''.join(f'<div class="case" style="grid-template-columns:78px 1fr"><div class="mono" style="color:var(--c-text-3);font-size:12px">{t}</div><div><span class="nm">{n}</span> <span style="color:var(--c-{"gap" if k=="gap" else "ais"})">{a}</span><div class="sub" style="margin-top:2px">{b}</div></div></div>' for t,n,a,b,k in ev)
body=topbar("Live","live")+f'''<div class="body"><div class="main">{kpis}<div class="map">{lmap}<div class="maptag"><span class="chip">Live AIS layer</span><span class="chip gap">Dashed ring grows with time silent</span></div>{legend(["ais","gap"])}{ATTR}{pin(1,10,44)}{pin(2,970,10)}</div></div>
<aside class="rail"><div class="rail-h"><h2>Silent now</h2><span>newest first</span></div><div class="cases">{evh}</div>{pin(3,404,10)}</aside></div>{pin(4,404,830).replace('left:404px','left:80px')}'''
page("04-live.html","04 Live layer",body,[
 "<b>Same grid, same legend.</b> Live is a mode of the overview, not a new app. The top-bar badge switches from SNAPSHOT to LIVE with a slow pulse (off under reduced motion).",
 "<b>A ship going silent</b> turns from a filled teal dot into a hollow amber ring with a dotted halo whose radius grows with minutes silent, the live version of the lens. Label is mono: \"silent 6 min\".",
 "<b>Event feed</b> in the rail, newest first, timestamp in mono. A ship that comes back draws a dashed line from last ping and returns to teal, which is the gap forming in front of the audience.",
 "<b>KPIs change.</b> Throughput and \"seconds since last message\" prove the feed is alive; if it exceeds 30 s the strip turns to the error state (screen 06)."],"04-live.html")

# ================= 05 edge =================
def cam():
    return '''<svg viewBox="0 0 640 500" preserveAspectRatio="xMidYMid slice" style="position:absolute;inset:0;width:100%;height:100%"><rect width="640" height="500" fill="#1A2B3C"/><rect y="230" width="640" height="270" fill="#12202E"/><g stroke="#1F3347" stroke-width="2"><line x1="0" y1="290" x2="640" y2="287"/><line x1="0" y1="350" x2="640" y2="356"/><line x1="0" y1="420" x2="640" y2="416"/><line x1="0" y1="470" x2="640" y2="474"/></g>
<line x1="0" y1="230" x2="640" y2="230" stroke="#34485F" stroke-width="2"/><path d="M0,230 L90,205 L160,222 L260,196 L330,226 Z" fill="#14202D" stroke="#26374B"/>
<path d="M300,340 L420,340 L452,312 L368,312 L350,290 L336,290 L336,312 L310,312 Z" fill="#2A3D52" stroke="#6B83A0" stroke-width="2"/>
<rect x="286" y="268" width="186" height="96" fill="none" stroke="var(--c-sar)" stroke-width="3"/><rect x="286" y="246" width="136" height="22" fill="var(--c-sar)"/><text x="294" y="263" fill="#0A1017" font-size="14" font-weight="600" font-family="var(--font-mono)">vessel 0.87</text>
<g stroke="#8A9BAD" stroke-width="1.5"><line x1="320" y1="0" x2="320" y2="14"/><line x1="320" y1="486" x2="320" y2="500"/><line x1="0" y1="250" x2="14" y2="250"/><line x1="626" y1="250" x2="640" y2="250"/></g></svg>'''
def bars():
    ms=[88,92,95,90,97,101,94,99,104,93,96,98,91,95,100,94,96,99,92,97]
    s='<svg viewBox="0 0 400 90" width="100%" height="90">'
    for i,m in enumerate(ms): s+=f'<rect x="{i*20+2}" y="{90-m*.62:.0f}" width="14" height="{m*.62:.0f}" fill="var(--c-text-2)"/>'
    s+='<line x1="0" y1="'+str(90-200*.31)+'" x2="400" y2="'+str(90-200*.31)+'" stroke="var(--c-risk-high)" stroke-dasharray="4 3" stroke-width="1.5"/>'
    return s+'</svg>'
inset=f'<div style="position:absolute;right:16px;bottom:16px;width:260px;height:170px;border:1px solid var(--c-line-strong);overflow:hidden;background:var(--c-sea)"><svg viewBox="0 0 260 170" width="100%" height="100%"><path d="M0,0 L260,0 L260,50 L170,40 L90,70 L0,50Z" fill="var(--c-land)" stroke="var(--c-land-stroke)"/>{ship(70,110)}{ship(180,130)}{ship(210,90)}<circle cx="120" cy="105" r="7" fill="none" stroke="var(--c-gap)" stroke-width="2"/><polygon points="120,99 126,105 120,111 114,105" fill="var(--c-candidate)" stroke="var(--c-sar)" stroke-width="1.5"/></svg><div class="attr" style="left:6px;bottom:6px;font-size:11px">Powered by Global Fishing Watch</div></div>'
body=topbar("Edge unit","none",region="Edge unit 01",extra='<span class="badge ok" style="margin-left:0"><span class="d"></span>SAFETY CORE OK</span>')+f'''<div class="body" style="grid-template-columns:1000px 440px"><div class="main"><div class="alert" style="font-size:32px;padding:18px 24px"><svg width="36" height="36" viewBox="0 0 36 36"><polygon points="18,3 34,32 2,32" fill="none" stroke="#0A1017" stroke-width="3"/><line x1="18" y1="13" x2="18" y2="22" stroke="#0A1017" stroke-width="3"/><circle cx="18" cy="27" r="1.8" fill="#0A1017"/></svg>NOT BROADCASTING<span style="margin-left:auto;font:var(--fw-medium) 20px var(--font-mono)">vessel in view, no AIS within 5 nm</span></div>
<div class="map" style="background:#12202E">{cam()}<div class="maptag"><span class="chip">Camera 1</span><span class="chip mono">frame 48,211 · 14:52:09.412</span></div>{inset}{pin(2,590,170)}{pin(1,10,10).replace("top:10px","top:-10px")}</div></div>
<aside class="rail"><div class="rail-h"><h2>Edge unit 01</h2><span>works with no network</span></div>
<div class="sec"><h3>Safety core</h3><div style="display:flex;justify-content:space-between;align-items:center"><span class="badge ok" style="font-size:20px;padding:6px 16px"><span class="d"></span>OK</span><span style="font-size:var(--fs-caption);color:var(--c-text-3)">DEGRADED shows when the detector is down</span></div></div>
<div class="sec"><h3>Deadline per frame</h3><div style="display:flex;align-items:baseline;gap:8px"><span class="hero" style="font-size:56px">96</span><span class="mono" style="color:var(--c-text-2)">ms of 200 ms</span></div>{bars()}<div style="font-size:var(--fs-caption);color:var(--c-text-3)">Last 20 frames · dashed line = 200 ms deadline</div></div>
<div class="sec"><h3>Detector (untrusted process)</h3><div class="row"><span>State</span><span class="v">running</span></div><div class="row"><span>Last restart<small>watchdog killed it, it came back</small></span><span class="v">1.8 s</span></div><div class="row"><span>Restarts this session</span><span class="v">2</span></div></div>
<div class="sec"><h3>Alert log</h3><div class="row"><span>14:52:09 Not broadcasting<small>synced to API</small></span><span class="chip high">alert</span></div><div class="row"><span>14:31:44 Alert kept while detector down<small>safety core only</small></span><span class="chip med">degraded</span></div></div>
{pin(3,404,60)}{pin(4,404,160)}{pin(5,404,300)}</aside></div>'''
page("05-edge.html","05 Edge unit",body,[
 "<b>Alert first.</b> A full-width red band reads NOT BROADCASTING at 32 px with an icon and a plain sentence. Dark text on red passes AA; the icon means it works in greyscale.",
 "<b>Camera frame with one box.</b> Blue (SAR hue) outline and score label, because blue means \"something radar-like saw\". The inset shows the map position with the mandatory attribution.",
 "<b>Safety core status</b> is the largest control in the rail: OK or DEGRADED, with the reason. This is the demo moment: we kill the detector and the badge turns amber while the alert stays.",
 "<b>Deadline per frame</b> as a number (56 px) and the last 20 frames against the 200 ms line.",
 "<b>Detector restart time</b> in seconds and a restart counter. Alerts are logged locally and show sync state."],"05-edge.html")

# ================= 06 states =================
def card(title,inner,tag):
    return f'<section style="background:var(--c-panel);border:1px solid var(--c-line-strong);display:flex;flex-direction:column"><header style="padding:10px 14px;border-bottom:1px solid var(--c-line);display:flex;justify-content:space-between;color:var(--c-text-1);font-weight:500"><span>{title}</span><span class="mono" style="color:var(--c-text-3);font-size:12px">{tag}</span></header><div style="position:relative;flex:1;min-height:0;overflow:hidden">{inner}</div></section>'
def mini_map(extra=""):
    return f'<div class="map" style="position:absolute;inset:0"><svg viewBox="0 0 1000 760" preserveAspectRatio="xMidYMid slice">{base()}{extra}</svg>{ATTR}</div>'
load=mini_map()+'<div style="position:absolute;inset:0;background:rgba(10,16,23,.55);padding:20px;display:grid;gap:12px;align-content:start"><div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px"><div class="skel" style="height:64px"></div><div class="skel" style="height:64px"></div><div class="skel" style="height:64px"></div><div class="skel" style="height:64px"></div><div class="skel" style="height:64px"></div></div><div style="color:var(--c-text-1);font-size:16px">Loading Baltic Sea, 1 to 30 Nov 2026</div><div style="color:var(--c-text-2)">Reading 37 dark events from the store. Skeletons keep the layout still.</div></div>'
empty=mini_map(''.join(ship(x,y) for x,y in ships(50)))+'<div style="position:absolute;left:50%;top:44%;transform:translate(-50%,-50%);width:360px;padding:20px;background:var(--c-panel);border:1px solid var(--c-line-strong);border-radius:4px;z-index:6"><div style="color:var(--c-text-1);font-size:18px;margin-bottom:6px">No dark events in this period</div><div style="color:var(--c-text-2);margin-bottom:12px">All 1,284 ships kept broadcasting between 1 and 3 Nov 2026. Try a longer period or another region.</div><div style="display:flex;gap:8px"><span class="ctl">Last 30 days</span><span class="ctl">Gulf of Oman</span></div></div>'
snap=mini_map(gap(250,330,330,300)+ship(450,300)+ship(520,400))+'<div style="position:absolute;left:14px;top:14px;z-index:6;display:grid;gap:8px"><span class="badge snap"><span class="d"></span>SNAPSHOT 14 Nov 2026 18:00 UTC</span><span class="chip">Offline: showing saved data, no network calls</span></div><div style="position:absolute;left:14px;top:84px;z-index:6;display:flex;gap:8px;align-items:center;color:var(--c-text-3);font-size:12px"><span class="badge live"><span class="d"></span>LIVE</span>how live looks, for comparison</div>'
err=mini_map()+'<div style="position:absolute;left:0;right:0;top:0;z-index:6;background:var(--c-panel-raised);border-bottom:2px solid var(--c-error);padding:14px 18px;display:flex;gap:14px;align-items:center"><span class="badge err">ERROR</span><div><div style="color:var(--c-text-1);font-size:16px">Could not reach the data service</div><div style="color:var(--c-text-2)">Showing the saved snapshot from 14 Nov 2026 18:00 UTC instead.</div></div><span class="ctl" style="margin-left:auto">Retry</span></div>'
partial=mini_map(gap(250,330,330,300)+lens(250,330,330,300,140,.5))+'<div style="position:absolute;left:14px;top:14px;z-index:6;padding:10px 14px;background:var(--c-panel);border:1px solid var(--c-gap);border-radius:4px;max-width:340px"><span class="badge deg" style="margin-bottom:6px">PARTIAL DATA</span><div style="color:var(--c-text-1)">Radar detections not loaded for 3 to 4 Nov</div><div style="color:var(--c-text-2)">Prisms are shown; candidate sightings may be missing.</div></div>'
nomatch=mini_map(gap(250,330,330,300)+lens(250,330,330,300,140,.5)+dia(600,500,6))+'<div style="position:absolute;right:14px;top:14px;z-index:6;padding:10px 14px;background:var(--c-panel);border:1px solid var(--c-line-strong);border-radius:4px;max-width:320px"><div style="color:var(--c-text-1);font-weight:500">No radar detection inside this prism</div><div style="color:var(--c-text-2)">1 detection nearby, outside the lens. Shown as an open diamond.</div></div>'
cards=card("Loading",load,"skeleton")+card("Empty",empty,"no events")+card("Snapshot / offline",snap,"badge in top bar")+card("Error",err,"fallback to snapshot")+card("Partial data",partial,"one source missing")+card("Case with no candidate",nomatch,"empty result, not an error")
body=f'<div style="padding:16px;height:100%;display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:repeat(2,1fr);gap:16px">{cards}</div>'
page("06-states.html","06 States",body,[
 "<b>Loading</b> uses skeleton blocks in the real KPI positions, so nothing jumps. The map attribution is present even while loading.",
 "<b>Empty</b> says what was searched and what to try, with two one-click alternatives. No illustration.",
 "<b>Snapshot / offline</b> is a permanent badge with the capture time, not an apology. The same badge style as LIVE, different hue (violet-white vs green) and a different word.",
 "<b>Error</b> never leaves a blank map: a banner names the problem, says what we show instead (the snapshot), and offers Retry. Red hue + the word ERROR.",
 "<b>Partial data</b> names the missing source and the consequence for the reading.",
 "<b>No candidate</b> is a valid result, shown as an ordinary message, not an error."],"06-states.html")
print("ok")
