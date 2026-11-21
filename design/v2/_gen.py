#!/usr/bin/env python3
"""Generates the v2 wireframes (static HTML, tokens only). Run: python3 design/v2/_gen.py"""
import math, random, os
random.seed(7)
OUT = os.path.dirname(os.path.abspath(__file__))

HEAD = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=1900">
<title>{title}</title><link rel="stylesheet" href="../tokens.css"><link rel="stylesheet" href="../wire.css"><link rel="stylesheet" href="v2.css"></head><body>'''

GLYPHS = '''<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<symbol id="g-silence" viewBox="0 0 20 20"><circle cx="7" cy="10" r="5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M13 10h6" stroke="currentColor" stroke-width="2" stroke-dasharray="2 2"/></symbol>
<symbol id="g-jump" viewBox="0 0 20 20"><path d="M10 3 L17.5 16.5 H2.5 Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></symbol>
<symbol id="g-clone" viewBox="0 0 20 20"><rect x="2.5" y="2.5" width="9" height="9" fill="none" stroke="currentColor" stroke-width="2"/><rect x="8.5" y="8.5" width="9" height="9" fill="var(--c-panel)" stroke="currentColor" stroke-width="2"/></symbol>
<symbol id="g-meeting" viewBox="0 0 20 20"><circle cx="10" cy="10" r="4" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="2"/></symbol>
</defs></svg>'''

def G(kind, cls="g"):
    return f'<svg class="{cls} k-{kind}"><use href="#g-{kind}"/></svg>'

def badge(kind, text):
    return f'<span class="badge {kind}"><span class="d"></span>{text}</span>'

def topbar(active, mode, present=True, pins=''):
    tabs = "".join(f'<a href="#"{" aria-current=page" if t==active else ""}>{t}</a>' for t in ("Global","Baltic","Edge unit"))
    p = '<span class="ctl present">Present <span class="kbd">P</span></span>' if present else ''
    return f'<header class="topbar"><div class="brand">Dark Fleet</div><nav class="nav">{tabs}</nav><div class="spacer"></div>{p}{mode}{pins}</header>'

LIVE = badge("live", "LIVE &nbsp;05:08 UTC")
REPLAY = badge("snap", "REPLAY &nbsp;21 Nov 03:40 UTC")
SNAP = badge("snap", "SNAPSHOT &nbsp;21 Nov 05:08 UTC")

def pn(n, style=''): return f'<i class="pn" style="{style}">{n}</i>'
def pin(n, x, y): return f'<div class="pin" style="left:{x}px;top:{y}px">{n}</div>'

def page(fname, title, cols, notes_title, notes, key=None):
    lis = "".join(f"<li>{n}</li>" for n in notes)
    keyhtml = f'<div class="ph-key">{key}</div>' if key else ''
    html = (HEAD.format(title=title) + GLYPHS +
        '<div class="stage"><div class="col">' + "".join(cols) + '</div>'
        f'<aside class="notes"><h2>{notes_title}</h2><div class="file">design/v2/{fname}</div><ol>{lis}</ol>{keyhtml}</aside></div></body></html>')
    open(os.path.join(OUT, fname), "w").write(html)

def frame(cap, inner):
    return f'<div><div class="cap">{cap}</div><div class="frame">{inner}</div></div>'

# ---------------------------------------------------------------- geometry
def pip(pt, poly):
    x, y = pt; inside = False; n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]; x2, y2 = poly[(i+1) % n]
        if (y1 > y) != (y2 > y) and x < (x2-x1)*(y-y1)/(y2-y1)+x1: inside = not inside
    return inside

# Baltic (stand-in coastline). 1000 x 788 px, 1:1 with the map area.
def bp(lon, lat): return ((lon-10.5)*51.5, (61.5-lat)*97.3)
def bpoly(pts): return [bp(*p) for p in pts]
B_LAND = {
 "swe": [(10.5,61.5),(17.4,61.5),(17.2,60.7),(18.3,60.2),(19.0,59.8),(18.9,59.3),(17.9,58.9),(16.9,58.6),(16.6,57.8),(16.4,56.7),(15.6,56.2),(14.3,55.4),(13.2,55.35),(12.95,55.6),(12.95,56.1),(12.5,56.6),(11.9,57.7),(11.2,58.9),(10.5,59.5)],
 "fin": [(29.9,61.5),(21.3,61.5),(21.4,61.0),(21.6,60.5),(22.3,60.3),(23.0,59.85),(24.0,59.95),(24.9,60.15),(25.7,60.4),(26.9,60.5),(28.0,60.55),(28.8,60.7),(29.9,60.6)],
 "south": [(10.5,53.4),(10.9,53.95),(11.6,54.0),(12.1,54.2),(12.9,54.4),(13.6,54.5),(14.25,53.95),(15.2,54.0),(16.4,54.4),(17.5,54.75),(18.4,54.8),(18.7,54.4),(19.7,54.45),(20.0,54.7),(20.9,55.3),(21.1,56.0),(21.0,56.6),(21.5,57.4),(22.6,57.75),(23.4,57.0),(24.1,57.0),(24.4,57.8),(24.5,58.4),(23.7,58.5),(23.4,59.0),(23.6,59.4),(24.7,59.5),(26.0,59.5),(27.5,59.45),(28.2,59.5),(29.9,59.8),(29.9,53.4)],
 "zealand": [(11.1,55.5),(11.8,55.3),(12.4,55.6),(12.4,56.0),(12.0,56.1),(11.3,55.9)],
 "lolland": [(11.0,54.75),(11.9,54.6),(12.2,54.9),(11.4,55.0)],
 "jutland": [(10.5,55.7),(10.5,57.7),(10.3,57.7),(10.2,56.5),(10.4,55.9)],
 "funen": [(10.0,55.2),(10.8,55.1),(10.8,55.6),(10.0,55.6)],
}
def ell(cx, cy, rx, ry, n=14):
    return [(cx+rx*math.cos(2*math.pi*i/n), cy+ry*math.sin(2*math.pi*i/n)) for i in range(n)]
B_ISL = [ell(14.95,55.13,.3,.14), ell(18.55,57.45,.4,.55), ell(16.65,56.7,.15,.62), ell(22.6,58.4,.55,.22), ell(22.7,58.95,.25,.18), ell(20.0,60.2,.5,.22)]
B_POLYS = [bpoly(v) for v in B_LAND.values()] + [bpoly([(a,b) for a,b in i]) for i in B_ISL]
def b_land(pt): return any(pip(pt, p) for p in B_POLYS)
def path(poly): return "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in poly) + "Z"

B_LANES = [
 ([(11.2,54.55),(12.8,54.9),(14.9,54.85),(17.2,55.5),(18.9,56.3),(19.8,57.5),(20.4,58.8),(22.0,59.6),(24.2,59.8),(26.5,59.9),(28.5,59.95)], 1000),
 ([(11.6,57.0),(12.3,56.2),(12.7,55.6),(12.75,55.0),(13.5,54.95)], 300),
 ([(18.9,54.9),(18.3,55.4),(17.3,55.5)], 90),
 ([(19.2,58.8),(19.4,59.5),(19.6,60.3),(19.9,61.0)], 90),
 ([(20.6,55.9),(20.2,56.8),(19.8,57.6)], 80),
 ([(24.7,59.62),(24.9,59.98)], 80),
 ([(22.2,57.9),(20.9,58.1),(20.2,58.6)], 40),
 ([(11.1,56.7),(11.6,57.0),(11.0,57.8)], 40),
]
B_CLUSTERS = [(19.0,54.95,"214"), (28.3,59.95,"187")]

def lane_dots():
    pts = []
    for lane, n in B_LANES:
        L = [bp(*p) for p in lane]
        segs = [(L[i], L[i+1], math.dist(L[i], L[i+1])) for i in range(len(L)-1)]
        tot = sum(s[2] for s in segs)
        for _ in range(n):
            r = random.random()*tot
            for a, b, d in segs:
                if r <= d:
                    t = r/d; x = a[0]+(b[0]-a[0])*t; y = a[1]+(b[1]-a[1])*t
                    nx, ny = (b[1]-a[1])/d, -(b[0]-a[0])/d
                    j = random.gauss(0, 5.5)
                    pts.append((x+nx*j, y+ny*j)); break
                r -= d
    for _ in range(260):
        pts.append((random.uniform(0,1000), random.uniform(0,788)))
    cl = [bp(a, b) for a, b, _ in B_CLUSTERS]
    return [p for p in pts if 0 < p[0] < 1000 and 0 < p[1] < 788 and not b_land(p) and all(math.dist(p, c) > 30 for c in cl)]

def grid(w, h, step=100):
    s = "".join(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}" stroke="var(--c-grid)"/>' for x in range(0, w, step))
    s += "".join(f'<line x1="0" y1="{y}" x2="{w}" y2="{y}" stroke="var(--c-grid)"/>' for y in range(0, h, step))
    return s

def baltic_base(dots=True, dim=1.0):
    land = "".join(f'<path d="{path(p)}" fill="var(--c-land)" stroke="var(--c-land-stroke)"/>' for p in B_POLYS)
    s = grid(1000, 788) + land
    if dots:
        d = lane_dots()
        s += f'<path d="{"".join(f"M{x:.1f} {y:.1f}h0" for x, y in d)}" stroke="var(--c-ais)" stroke-opacity="{.55*dim:.2f}" stroke-width="2.6" stroke-linecap="round" fill="none"/>'
        for a, b, c in B_CLUSTERS:
            x, y = bp(a, b)
            s += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="24" fill="rgba(79,209,176,.12)" stroke="var(--c-ais)" stroke-opacity=".5"/><text x="{x:.1f}" y="{y+5:.1f}" text-anchor="middle" font-family="var(--font-mono)" font-size="13" fill="var(--c-text-1)">{c}</text>'
    return s

def tag(x, y, n, anchor="start"):
    return f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="var(--font-mono)" font-size="12" font-weight="600" fill="var(--c-text-1)" stroke="var(--c-bg)" stroke-width="3" paint-order="stroke">{n}</text>'

def m_silence(a, b, n, sel=False):
    (x1, y1), (x2, y2) = bp(*a), bp(*b)
    s = f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="var(--c-gap)" stroke-width="2" stroke-dasharray="6 5"/>'
    s += f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="7" fill="var(--c-bg)" stroke="var(--c-gap)" stroke-width="2.5"/><circle cx="{x2:.1f}" cy="{y2:.1f}" r="4" fill="var(--c-gap)"/>'
    if sel: s += f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="15" fill="none" stroke="var(--c-text-1)" stroke-width="1.5"/>'
    return s + tag(x1-14, y1+4, n, "end")

def m_jump(a, b, n, sel=False):
    (x1, y1), (x2, y2) = bp(*a), bp(*b); mx, my = (x1+x2)/2, (y1+y2)/2
    s = f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="var(--c-text-1)" stroke-width="1.5"/>'
    s += f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="3" fill="var(--c-text-1)"/><circle cx="{x2:.1f}" cy="{y2:.1f}" r="3" fill="var(--c-text-1)"/>'
    s += f'<path d="M{mx:.1f} {my-9:.1f}L{mx+9:.1f} {my+7:.1f}H{mx-9:.1f}Z" fill="var(--c-bg)" stroke="var(--c-text-1)" stroke-width="2.2" stroke-linejoin="round"/>'
    if sel: s += f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="17" fill="none" stroke="var(--c-text-1)" stroke-width="1.5"/>'
    return s + tag(mx+16, my+4, n)

def m_clone(a, b, n, sel=False):
    (x1, y1), (x2, y2) = bp(*a), bp(*b)
    s = f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="var(--c-text-1)" stroke-width="1.5" stroke-dasharray="2 4"/>'
    for x, y in ((x1, y1), (x2, y2)):
        s += f'<rect x="{x-6:.1f}" y="{y-6:.1f}" width="12" height="12" fill="var(--c-bg)" stroke="var(--c-text-1)" stroke-width="2.2"/>'
    return s + tag(x1+12, y1-8, n)

def m_meet(a, n, sel=False):
    x, y = bp(*a)
    s = f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12" fill="rgba(255,79,154,.18)" stroke="var(--c-rdv)" stroke-width="2.2"/><circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="none" stroke="var(--c-rdv)" stroke-width="2.2"/>'
    return s + tag(x+17, y+4, n)

EVENTS = [  # placeholder data
 ("silence","NORDIC VEGA","PAN","Silent for 4 h 12 min in open water while 590 other ships were heard.","12 min ago",True),
 ("jump","ELBE TRADER","LBR","Moved 38 km in 6 min: 24 times faster than this ship can go.","1 h ago",False),
 ("meeting","KAPITAN R. + ESTRELLA","RUS, COM","Two ships stopped 300 m apart for 52 min, far from shore.","2 h ago",False),
 ("clone","MARIA K","CYP","One ship ID reported 142 km apart, flipping 6 times in 10 min.","3 h ago",False),
 ("silence","BALTIC SPIRIT","MHL","Silent for 2 h 05 min while 588 other ships were heard.","5 h ago",False),
]

def balt_markers():
    return (m_silence((18.6,56.0),(18.0,55.45),"1",True) if False else
        m_silence((17.9,55.55),(18.6,56.0),"1", True) +
        m_jump((13.4,54.95),(14.0,55.25),"2") +
        m_meet((21.6,59.3),"3") +
        m_clone((20.0,57.9),(21.9,58.7),"4") +
        m_silence((24.3,60.0),(25.6,59.9),"5") +
        m_silence((19.4,59.2),(19.6,60.0),"6") +
        m_jump((17.0,55.2),(17.5,55.45),"7") +
        m_meet((15.2,55.6),"8"))

# ---------------------------------------------------------------- 02 Baltic
def baltic_frame(mode_badge, selected=True):
    rows = ""
    for i, (k, nm, fl, tx, tm, wl) in enumerate(EVENTS):
        sel = " sel" if i == 0 and selected else ""
        tagh = '<div><span class="tag">On sanctions watchlist</span></div>' if wl else ''
        rows += f'<div class="erow{sel}">{pn(8,"right:8px;top:48px") if i==0 else ""}<div class="rk">{G(k)}{i+1}</div><div><div class="nm">{nm}<small>{fl}</small></div><div class="tx">{tx}</div>{tagh}</div><div class="tm">{tm}</div></div>'
    spark = '<svg width="96" height="20" viewBox="0 0 96 20"><path d="M0 8 L8 7 L16 9 L24 6 L32 7 L40 8 L48 6 L56 8 L64 7 L72 9 L80 6 L88 7 L96 7" fill="none" stroke="var(--c-ais)" stroke-width="1.5"/></svg>'
    strip_ticks = ""
    for k, pos in (("silence",5),("silence",21),("meeting",47),("clone",63),("jump",78),("silence",90),("jump",84),("meeting",36)):
        strip_ticks += f'<span style="position:absolute;left:{pos}%;top:7px" class="k-{k}">{G(k,"g")}</span>'
    track = f'''<div style="position:relative;flex:1;height:34px;border-bottom:1px solid var(--c-line-strong)">
      {strip_ticks}
      <span style="position:absolute;left:54%;width:3%;bottom:0;height:8px;background:repeating-linear-gradient(45deg,var(--c-line-strong) 0 3px,transparent 3px 6px)"></span>
      <span style="position:absolute;right:0;bottom:-5px;width:10px;height:10px;border-radius:50%;background:var(--c-live)"></span>
      <span style="position:absolute;left:0;top:36px;font:12px var(--font-mono);color:var(--c-text-3)">-24 h</span>
      <span style="position:absolute;left:25%;top:36px;font:12px var(--font-mono);color:var(--c-text-3)">-18 h</span>
      <span style="position:absolute;left:50%;top:36px;font:12px var(--font-mono);color:var(--c-text-3)">-12 h</span>
      <span style="position:absolute;left:75%;top:36px;font:12px var(--font-mono);color:var(--c-text-3)">-6 h</span>
      <span style="position:absolute;right:0;top:36px;font:12px var(--font-mono);color:var(--c-text-3)">now</span></div>'''
    mx, my = bp(17.9, 55.55)
    inner = (topbar("Baltic", mode_badge, pins=pn(10,'left:1257px;top:13px')) + '<div class="body"><div class="main">'
      '<div class="map"><svg viewBox="0 0 1000 788" preserveAspectRatio="xMidYMid slice">'
      + baltic_base() + balt_markers() + '</svg>' + pn(3,'left:196px;top:596px') + pn(4,'left:936px;top:96px') + pn(5,'left:126px;top:586px') + pn(7,'left:156px;top:508px') +
      '<div class="bhero">'+pn(1,'left:-11px;top:-11px')+'<div class="row"><span class="n">8</span><span class="l">unusual events in the last 24 hours</span></div>' +
      f'<div class="cov"><span class="dot"></span>Receivers hearing <b>591</b> ships/min {spark}'+pn(2,'right:-11px;top:-11px')+'</div></div>' +
      f'<div class="lab" style="left:{mx-210:.0f}px;top:{my-52:.0f}px"><b style="font-weight:500">NORDIC VEGA</b> <small>went silent here</small></div>' +
      '<div class="attr">Positions: our AIS recording since Fri 20 Nov 23:18 &nbsp;&middot;&nbsp; Watchlist: OpenSanctions</div></div>' +
      f'<div class="strip" style="height:76px">'+pn(9,'left:150px;top:-11px')+f'<span class="btn">Replay last 24 h</span>{track}</div></div>'
      '<aside class="rail"><div class="rail-h" style="flex-direction:column;align-items:stretch"><div style="display:flex;justify-content:space-between;align-items:baseline"><h2>Worth a look</h2><span>highest first</span></div>'
      f'<div class="fchips">'+pn(6,'right:0;top:-12px')+f'<span class="fchip">{G("silence")}Silence <b>3</b></span><span class="fchip">{G("jump")}Jump <b>2</b></span><span class="fchip">{G("clone")}Clone <b>1</b></span><span class="fchip">{G("meeting")}Meeting <b>2</b></span></div></div>'
      + rows + '<div class="more"><span>Show 3 more</span><span style="color:var(--c-text-3)">Select a row to see the proof</span></div></aside></div>')
    return inner

def build_baltic():
    inner = baltic_frame(LIVE)
    notes = [
     "<b>One question: what is happening right now?</b> The only large number on screen is <span class=mono>8</span>, top-left of the map. It answers the question before anyone reads a word.",
     "<b>Coverage sits under the headline.</b> A silence only means something if we were listening, so \"receivers hearing 591 ships/min\" (real: Baltic probe, F5) is part of the first read, with a 24 h sparkline. It turns amber if it drops.",
     "<b>Ships are quiet, events are loud.</b> Thousands of ships are one dim teal field (2.6 px, 55% opacity). Event markers are the only strong shapes on the map.",
     "<b>Density-aware.</b> Where dots would merge (Gdansk anchorage, Gulf of Finland) they collapse into one counted bubble; zooming in splits it back into dots. Never more than ~2,000 individual dots at once.",
     "<b>Kind = shape, family = colour.</b> Silence: amber ring with dashed tail (went quiet). Jump and clone: white triangle / twin squares (position or identity cannot be true). Meeting: pink double ring. Works in greyscale.",
     "<b>The legend is the filter.</b> The kind chips above the list explain every marker and filter it (all on by default; tap one to isolate it). No separate legend box on this map.",
     "<b>Only the selected event is labelled</b> on the map. The other seven are just numbered markers, so the picture stays calm.",
     "<b>Ranked list, five rows, plain sentences.</b> Each row is one sentence a non-expert reads at 1 m, never a field dump. Rank number links row and marker. Red is used once: a sanctions-watchlist hit.",
     "<b>Time strip.</b> Events as ticks on the last 24 h, hatched = a stretch where our recorder had a hole (we say so). V1: ticks and a \"Replay\" button. V2: drag to scrub.",
     "<b>Mode badge in the same slot as every screen.</b> LIVE green and pulsing only while the stream is fresh; REPLAY and SNAPSHOT use the violet-white. Attribution bottom-left, always.",
    ]
    page("02-baltic.html", "02 Baltic live", [frame("02 Baltic live (V1) &middot; 1440 x 900 &middot; placeholder data: ships, events, names. Real: 591 ships/min (Baltic probe).", inner)],
         "Baltic live", notes, "Placeholder: all vessel names, event counts and times. Coastline is a stand-in. Real: 591 ships/min, recording start Fri 20 Nov 23:18.")

# ---------------------------------------------------------------- 03 Event detail
def zoom_jump():
    s = grid(1000, 788, 80) + '<path d="M0 560 C 120 520, 220 600, 330 640 S 560 700, 700 788 L0 788Z" fill="var(--c-land)" stroke="var(--c-land-stroke)"/>'
    ax, ay, bx, by = 300, 520, 700, 250   # 400 px ~ 38 km
    s += f'<circle cx="{ax}" cy="{ay}" r="32" fill="var(--c-prism-fill)" stroke="var(--c-prism-stroke)" stroke-width="1.5" stroke-dasharray="4 4"/>'
    s += f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="var(--c-text-1)" stroke-width="2"/>'
    s += f'<circle cx="{ax}" cy="{ay}" r="7" fill="var(--c-text-1)"/><circle cx="{bx}" cy="{by}" r="7" fill="var(--c-bg)" stroke="var(--c-text-1)" stroke-width="2.5"/>'
    return s
def zoom_silence():
    s = grid(1000, 788, 80) + '<path d="M0 700 C 200 660, 380 720, 520 788 L0 788Z" fill="var(--c-land)" stroke="var(--c-land-stroke)"/>'
    ax, ay, bx, by = 270, 470, 700, 300
    s += f'<ellipse cx="{(ax+bx)/2}" cy="{(ay+by)/2}" rx="300" ry="170" transform="rotate(-22 {(ax+bx)/2} {(ay+by)/2})" fill="var(--c-prism-fill)" stroke="var(--c-prism-stroke)" stroke-width="1.5"/>'
    d = []
    for _ in range(330):
        x, y = random.uniform(40, 960), random.uniform(60, 740)
        if y > 700 - x*0.28 + 40 and y > 640: continue
        d.append((x, y))
    s += f'<path d="{"".join(f"M{x:.0f} {y:.0f}h0" for x, y in d)}" stroke="var(--c-ais)" stroke-opacity=".5" stroke-width="3" stroke-linecap="round"/>'
    s += f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="var(--c-gap)" stroke-width="2.5" stroke-dasharray="7 6"/>'
    s += f'<circle cx="{ax}" cy="{ay}" r="9" fill="var(--c-bg)" stroke="var(--c-gap)" stroke-width="3"/><circle cx="{bx}" cy="{by}" r="6" fill="var(--c-gap)"/>'
    return s

def detail_head(kind, label, extra="", p=""):
    return f'<div class="dh">{p}<div class="back">&lsaquo; Baltic &nbsp;/&nbsp; Events</div><div class="kindline">{G(kind)}{label}{extra}</div>'

def bar(label, w, color, val, sub=""):
    return (f'<div style="margin-bottom:12px"><div style="display:flex;justify-content:space-between;font-size:13px;color:var(--c-text-2);margin-bottom:4px"><span>{label}</span><span class="mono" style="color:var(--c-text-1)">{val}</span></div>'
            f'<div style="height:14px;background:var(--c-panel-raised);border-radius:2px;position:relative"><div style="width:{w}%;height:14px;background:{color};border-radius:2px"></div></div></div>')

def build_event():
    # --- jump
    proof_j = ('<div class="proof">'+pn(4)+'<h3>The physics check</h3>'
      + bar("Speed needed to cover 38 km in 6 min", 100, "var(--c-text-1)", "380 kn")
      + bar("Fastest a tanker this size can go", 4.2, "var(--c-text-3)", "16 kn")
      + '<div style="font-size:13px;color:var(--c-text-3)">The ship cannot have made this trip. The two positions came from different transmitters, or one was false.</div></div>')
    jump = (topbar("Baltic", LIVE) + '<div class="body"><div class="main"><div class="map"><svg viewBox="0 0 1000 788" preserveAspectRatio="xMidYMid slice">' + zoom_jump() + '</svg>'
      +pn(5,'left:250px;top:476px')+pn(7,'left:430px;top:354px')+'<div class="lab" style="left:300px;top:556px">14:02 UTC &nbsp;<small>last normal position</small></div>'
      '<div class="lab" style="left:718px;top:236px">14:08 UTC &nbsp;<small>next position, 6 min later</small></div>'
      '<div class="lab" style="left:455px;top:362px;border-color:var(--c-text-2)"><span class="mono">38 km</span> apart</div>'
      '<div class="lab" style="left:60px;top:480px;max-width:210px;white-space:normal"><svg width="14" height="14" style="vertical-align:-2px;margin-right:6px"><circle cx="7" cy="7" r="6" fill="var(--c-prism-fill)" stroke="var(--c-prism-stroke)" stroke-dasharray="3 3"/></svg>Farthest it could get in 6 min: <span class="mono">3 km</span></div>'
      '<div class="attr">Positions: our AIS recording &nbsp;&middot;&nbsp; Map: stand-in coastline</div></div></div>'
      '<aside class="rail">' + detail_head("jump", "Impossible jump", '<span style="margin-left:auto" class="tm mono">1 h ago</span>', pn(1,'top:8px;right:100px')+pn(2,'top:54px'))
      + '<div class="nm" style="margin-top:12px;font-size:16px;color:var(--c-text-1)">ELBE TRADER <small style="color:var(--c-text-3);font-size:12px">MMSI 000000000 &middot; LBR &middot; tanker</small></div>'
      '<div class="big">'+pn(3,'top:110px')+'24&times;<small>too fast</small></div>'
      '<div class="sent">ELBE TRADER moved 38 km in 6 minutes. A ship like this tops out at 16 knots.</div></div>'
      + proof_j +
      '<div class="proof"><h3>The numbers</h3><dl class="dl"><dt>Distance between positions</dt><dd>38 km</dd><dt>Time between them</dt><dd>6 min</dd><dt>Speed needed</dt><dd>380 kn</dd><dt>Class maximum (tanker)</dt><dd>16 kn</dd></dl></div>'
      '<div class="disc">'+pn(6)+'<div class="h">Sources <span>1 &nbsp;+</span></div></div>'
      '<div class="disc"><div class="h">What we cannot tell you <span>3 &nbsp;+</span></div></div>'
      '<div style="padding:16px;margin-top:auto"><span class="btn primary" style="width:100%;justify-content:center">Replay this event</span></div></aside></div>')

    # --- silence
    def row(label, inner): return f'<div style="display:grid;grid-template-columns:120px 1fr;align-items:center;gap:8px;margin-bottom:10px;font-size:13px;color:var(--c-text-2)"><span>{label}</span>{inner}</div>'
    ticks_a = '<div style="position:relative;height:22px"><div style="position:absolute;left:0;width:28%;top:9px;height:4px;background:repeating-linear-gradient(90deg,var(--c-ais) 0 2px,transparent 2px 9px)"></div><div style="position:absolute;left:28%;width:46%;top:2px;height:18px;background:rgba(255,176,32,.22);border:1px dashed var(--c-gap);display:flex;align-items:center;justify-content:center;font:12px var(--font-mono);color:var(--c-gap)">4 h 12 min</div><div style="position:absolute;left:74%;right:0;top:9px;height:4px;background:repeating-linear-gradient(90deg,var(--c-ais) 0 2px,transparent 2px 9px)"></div></div>'
    ticks_b = '<div style="height:22px;position:relative"><div style="position:absolute;inset:3px 0;background:repeating-linear-gradient(90deg,var(--c-text-3) 0 1px,transparent 1px 4px);opacity:.7"></div></div>'
    proof_s = ('<div class="proof"><h3>The listening check</h3>'
      + row("NORDIC VEGA", ticks_a) + row("Other ships in range", ticks_b)
      + '<div style="display:grid;grid-template-columns:120px 1fr;font:12px var(--font-mono);color:var(--c-text-3)"><span></span><span style="display:flex;justify-content:space-between"><span>11:20</span><span>15:32 UTC</span></span></div>'
      '<div style="font-size:13px;color:var(--c-text-3);margin-top:10px">Our receivers kept hearing about <span class="mono" style="color:var(--c-text-2)">590</span> other ships per minute. Only this one stopped.</div></div>')
    silence = (topbar("Baltic", LIVE) + '<div class="body"><div class="main"><div class="map"><svg viewBox="0 0 1000 788" preserveAspectRatio="xMidYMid slice">' + zoom_silence() + '</svg>'
      '<div class="lab" style="left:120px;top:478px">11:20 UTC &nbsp;<small>last heard, 11.4 kn</small></div>'
      '<div class="lab" style="left:716px;top:286px">15:32 UTC &nbsp;<small>heard again</small></div>'
      ''+pn(9,'left:536px;top:84px')+'<div class="lab" style="left:560px;top:90px;max-width:250px;white-space:normal"><svg width="14" height="14" style="vertical-align:-2px;margin-right:6px"><rect width="14" height="14" fill="var(--c-prism-fill)" stroke="var(--c-prism-stroke)"/></svg>Where it could have been: up to <span class="mono">89 km</span> from its last position</div>'
      '<div class="attr">Positions: our AIS recording &nbsp;&middot;&nbsp; Watchlist: OpenSanctions &nbsp;&middot;&nbsp; Stand-in coastline</div></div></div>'
      '<aside class="rail">' + detail_head("silence", "Silence", '<span style="margin-left:auto" class="tm mono">12 min ago</span>')
      + '<div class="nm" style="margin-top:12px;font-size:16px;color:var(--c-text-1)">NORDIC VEGA <small style="color:var(--c-text-3);font-size:12px">IMO 0000000 &middot; PAN &middot; tanker</small> <span class="tag" style="margin-top:0;margin-left:6px;position:relative">On sanctions watchlist'+pn(11,'right:-12px;top:-12px')+'</span></div>'
      '<div class="big">'+pn(8,'top:110px')+'4<small>h</small> 12<small>min silent</small></div>'
      '<div class="sent">NORDIC VEGA stopped broadcasting in open water while about 590 other ships were still heard.</div></div>'
      + proof_s +
      '<div class="proof"><h3>The numbers</h3><dl class="dl"><dt>Last heard, then heard again</dt><dd>11:20 &rarr; 15:32 UTC</dd><dt>Speed before going quiet</dt><dd>11.4 kn</dd><dt>Could have sailed up to</dt><dd>89 km</dd></dl></div>'
      '<div class="disc">'+pn(10)+'<div class="h">Sources <span>&minus;</span></div><ul><li>Our AIS recording, 3 receivers</li><li>OpenSanctions list: IMO match</li></ul></div>'
      '<div class="disc" style="border-bottom:0"><div class="h">What we cannot tell you <span>&minus;</span></div><ul><li>Silence is not proof: transmitters fail.</li><li>We count only silences where we were listening.</li><li>Top speed is a class default, not measured.</li></ul></div>'
      '</aside></div>')
    notes = [
     "<b>Opens in the same screen.</b> Click a list row or a marker: the rail swaps to the detail, the map zooms to the event. No new page, so the context stays and \"Back\" is one click. Breadcrumb under the bar, not a tab.",
     "<b>Same four blocks, always the same order</b>, so after one event the second is already readable: kind and ship, one big number, one sentence, the proof, the numbers.",
     "<b>One big number is the evidence</b> (24&times; too fast / 4 h 12 min silent). It is the most important fact about this event, at 56 px so it reads at 1 m.",
     "<b>The physics proof is one small picture, not a chart.</b> Jump: two bars on one scale, needed 380 kn vs maximum 16 kn. The tiny second bar is the argument.",
     "<b>The map repeats the proof</b>: the violet circle is how far the ship could get in 6 min; the second position is far outside it.",
     "<b>Progressive disclosure.</b> Numbers are visible, sources and caveats start collapsed with a count (\"3\"). Primary action: Replay this event (V2 explainer); in V1 the button is absent.",
     "<b>Map labels carry times and one fact each</b>, never a table. Positions marked with filled (known) vs hollow (the other end).",
     "<b>Silence leads with the sentence, then proves we were listening.</b> Two tracks on one time axis: this ship goes quiet while the other row (all other ships heard) never does. That is the whole claim.",
     "<b>Violet zone on the map = where it could have been</b> (up to 89 km), the same lens meaning as the Global case file. One visual language across theatres.",
     "<b>Disclosures shown open here</b> so the content is reviewable. Caveats are plain statements, not legalese: this is where honesty lives, one click away, never hidden.",
     "<b>Red appears once</b>: the sanctions-watchlist tag, next to the ship's name where an analyst looks first.",
    ]
    page("03-event.html", "03 Event detail", [
        frame("03a Event detail, impossible jump (V1) &middot; placeholder data: ship, MMSI, positions, times, speeds. Real: none.", jump),
        frame("03b Event detail, silence (V1) &middot; same layout, disclosures expanded &middot; placeholder data", silence)],
        "Event detail", notes, "Placeholder: ship names, MMSI/IMO, every number and time in both frames. Speed cap (16 kn tanker) is a class default, per docs.")

# ---------------------------------------------------------------- world map
def wp(lon, lat): return ((lon+180)*2.78, (84-lat)*2.78)
W_LAND = [
 [(-168,66),(-140,70),(-110,72),(-80,73),(-62,62),(-56,52),(-67,45),(-76,38),(-81,31),(-80,25),(-90,30),(-97,26),(-97,19),(-88,21),(-83,9),(-78,8),(-92,15),(-105,20),(-117,32),(-124,40),(-125,49),(-135,58),(-150,60),(-165,60)],
 [(-78,8),(-62,10),(-50,0),(-35,-6),(-39,-15),(-48,-26),(-58,-38),(-65,-42),(-68,-55),(-74,-50),(-72,-30),(-76,-14),(-81,-5),(-78,2)],
 [(-17,21),(-10,32),(10,37),(32,31),(43,12),(51,11),(40,-5),(40,-15),(33,-27),(20,-35),(14,-22),(12,-5),(9,4),(-8,4),(-17,14)],
 [(-10,36),(-9,44),(-1,49),(8,54),(10,58),(20,70),(40,68),(70,73),(110,77),(140,72),(180,68),(180,62),(160,58),(142,52),(135,43),(127,38),(122,30),(110,20),(105,10),(100,2),(98,16),(92,22),(80,15),(77,8),(72,20),(66,25),(57,26),(50,30),(56,18),(44,13),(35,28),(35,36),(26,38),(20,40),(12,44),(6,43),(-2,36)],
 [(130,32),(136,35),(141,39),(144,44),(141,42),(135,36),(130,33)],
 [(114,-22),(122,-18),(131,-12),(137,-12),(142,-11),(146,-19),(153,-26),(150,-37),(141,-38),(131,-31),(115,-34)],
 [(-55,60),(-45,60),(-20,70),(-22,82),(-60,82),(-70,76)],
 [(172,-35),(178,-38),(175,-41),(171,-45),(167,-46),(172,-41)],
]
W_POLYS = [[wp(*p) for p in poly] for poly in W_LAND]
def world_land(): return "".join(f'<path d="{path(p)}" fill="var(--c-land)" stroke="var(--c-land-stroke)"/>' for p in W_POLYS)
def w_is_land(p): return any(pip(p, poly) for poly in W_POLYS)

UNEXPL = [(-38,-12,6),(-36,-8,1),(-62,11,4),(-79,-9,3),(140,36,3),(165,-17,2)]  # 19 dots in 6 spots (placeholder positions)
def world_cases(show_cleared=False):
    s = ""
    if show_cleared:
        for _ in range(90):
            while True:
                lon, lat = random.uniform(-100, 170), random.uniform(-40, 55)
                p = wp(lon, lat)
                if not w_is_land(p): break
            s += f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="3" fill="var(--c-text-3)" fill-opacity=".5"/>'
    for lon, lat, n in UNEXPL:
        x, y = wp(lon, lat)
        for i in range(n):
            a = i*2.4; r = 3+ (i//2)*5 if n > 1 else 0
            cx, cy = x+r*math.cos(a), y+r*math.sin(a)
            s += f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="var(--c-gap)" stroke="var(--c-bg)" stroke-width="1.5"/>'
        if n >= 3:
            s += f'<text x="{x+18:.1f}" y="{y-14:.1f}" font-family="var(--font-mono)" font-size="13" font-weight="600" fill="var(--c-text-1)" stroke="var(--c-bg)" stroke-width="3" paint-order="stroke">&times;{n}</text>'
    return s

ARROW = '<svg viewBox="0 0 34 10"><path d="M0 5H30M26 1l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>'
def funnel(lit=3, pins=False):
    def st(i, n, l, hero=False):
        c = "fs hero-n" if hero else "fs"
        if lit < i: c += " dim"
        return f'<div class="{c}"><div class="n">{n}</div><div class="l">{l}</div></div>'
    pp = (pn(1,'left:-11px;top:-11px') + pn(2,'left:300px;top:-11px') + pn(3,'right:-11px;top:-11px')) if pins else ''
    return ('<div class="funnel">' + pp + st(1, "5,624", "times a ship went silent at sea") + f'<div class="fa{" fs dim" if lit<2 else ""}">{ARROW}</div>'
            + st(2, "109", "had radar nearby") + f'<div class="fa{" fs dim" if lit<3 else ""}">{ARROW}<span>90 cleared by physics and satellite timing</span></div>'
            + st(3, "19", "we cannot explain", True) + '</div>')

# ---------------------------------------------------------------- 05 overview v2
def build_overview():
    cases = [("MARIA","BRA &middot; other non fishing","55 radar sightings","60.8"),("IRENES WISDOM","CYP &middot; other non fishing","37 radar sightings","80.8"),
             ("ASSO VENTISEI","BRA &middot; other non fishing","30 radar sightings","49.3"),("710130057","BRA &middot; fishing","21 radar sightings","52.8"),
             ("EAGLE PAULINIA","SGP &middot; other non fishing","21 radar sightings","22.8"),("ANFONG 156","TWN &middot; fishing","20 radar sightings","85.6")]
    rows = "".join(f'<div class="crow">{pn(7,"left:3px;top:10px") if n=="MARIA" else ""}<div class="nm">{n}</div><div class="hrs">{h} h<small>dark</small></div><div class="sub">{s}</div><div class="rd" style="grid-column:1">{r}</div></div>' for n, s, r, h in cases)
    inner = (topbar("Global", SNAP) + '<div class="body"><div class="main"><div class="map"><svg viewBox="0 -150 1000 700" preserveAspectRatio="xMidYMid slice">'
      + grid(1000, 700, 100).replace('y1="0"', 'y1="-150"') + world_land() + world_cases() + '</svg>' + funnel(3, True) + pn(5,'left:340px;top:462px') + pn(6,'left:16px;top:166px') +
      '<div class="attr">Powered by Global Fishing Watch &nbsp;&middot;&nbsp; Natural Earth</div>'
      '<div class="lab" style="right:16px;bottom:16px;background:rgba(16,25,35,.9)"><svg width="12" height="12" style="vertical-align:-1px;margin-right:6px"><circle cx="6" cy="6" r="5" fill="var(--c-gap)"/></svg>Ship we cannot explain &nbsp;&nbsp;<span style="color:var(--c-text-3)">Cleared ships are hidden</span></div></div></div>'
      '<aside class="rail"><div class="rail-h" style="flex-direction:column;align-items:stretch"><div style="display:flex;justify-content:space-between;align-items:baseline"><h2>Ships to review</h2><span>most radar sightings first</span></div>'
      '<div class="seg">'+pn(4,'left:-11px;top:6px')+'<span class="on"><i class="dot" style="background:var(--c-gap)"></i>Unexplained <b>19</b></span><span><i class="dot" style="background:var(--c-text-3)"></i>Cleared <b>90</b></span></div></div>'
      + rows + '<div class="more"><span>Show 13 more</span><span style="color:var(--c-text-3)">How sure are we? &rsaquo;</span></div></aside></div>')
    notes = [
     "<b>Is the current one cluttered? Yes, in four ways:</b> the same three numbers appear twice (KPI strip and the rail bars), six colours compete (amber, blue, grey, white, plus map), every row repeats an identical \"Unexplained\" chip, and the map is mostly empty ocean under two chips and a legend that lists things that are not drawn.",
     "<b>Merge: KPI strip + rail bar chart become one funnel</b> on the map: 5,624 &rarr; 109 &rarr; 19 (all real). One component says \"how much is suspicious, how much is cleared\". The cleared 90 sit on the arrow, in words.",
     "<b>One hero.</b> 19 at 72 px in amber, the only coloured number. 5,624 and 109 are context, grey, 28 px. The eye lands on 19, then the map shows where those 19 are.",
     "<b>Remove the per-row \"Unexplained\" chip.</b> It carries no information when every row has it. State becomes a two-way switch above the list (Unexplained 19 / Cleared 90), which also doubles as the legend.",
     "<b>Show only the 19 on the map.</b> The 90 cleared ships are hidden until you switch lists (no grey noise). Six clusters, counts on the three biggest. No legend box: one line, bottom right.",
     "<b>Cut</b> the \"Region\" and \"Showing all 109\" chips (the bar already has context; filters belong in the list), the explanatory paragraph (moves behind \"How sure are we?\", where validation lives: AUC 0.87, radar-near-dark 1.07&times; = chance) and the tracked-caps section labels.",
     "<b>Rows get lighter:</b> name, flag and type in grey, one sentence of evidence, one number (hours dark, amber). No rank digits: the sort is stated once above the list. Six rows visible, rest behind \"Show 13 more\".",
    ]
    page("05-overview-v2.html", "05 Overview v2", [frame("05 Global overview, calmer (V1 refinement) &middot; 1440 x 900 &middot; real: 5,624 / 109 / 90 / 19, region-scan numbers; placeholder: names, hours, positions (stand-in coastline)", inner)],
         "Global overview v2", notes, "Real: 5,624, 109, 90, 19 (docs/STATUS.md). Case names and hours are from the current console screenshot; cluster positions are stand-ins.")

# ---------------------------------------------------------------- 04 Present
def lens_path(x1, y1, x2, y2, r):
    return f"M{x1} {y1} A{r} {r} 0 0 1 {x2} {y2} A{r} {r} 0 0 1 {x1} {y1}Z"
def case_present():
    s = grid(1440, 900, 100) + '<path d="M0 160 C 120 150, 240 210, 300 330 S 360 470, 400 560 L0 560Z" fill="var(--c-land)" stroke="var(--c-land-stroke)"/>'
    s += '<circle cx="860" cy="330" r="270" fill="none" stroke="var(--c-prism-stroke)" stroke-opacity=".4" stroke-dasharray="3 6"/>'
    s += f'<path d="{lens_path(760, 250, 880, 450, 200)}" fill="var(--c-prism-fill)" stroke="var(--c-prism-stroke)" stroke-width="1.5"/>'
    s += '<line x1="1020" y1="170" x2="700" y2="470" stroke="var(--c-gap)" stroke-width="2" stroke-dasharray="6 5"/><circle cx="1020" cy="170" r="6" fill="var(--c-gap)"/><circle cx="700" cy="470" r="6" fill="var(--c-bg)" stroke="var(--c-gap)" stroke-width="2.5"/>'
    for dx, dy in ((0,0),(16,-10),(-14,12),(10,18),(-4,-18)):
        x, y = 1000+dx, 410+dy
        s += f'<path d="M{x} {y-8}L{x+8} {y}L{x} {y+8}L{x-8} {y}Z" fill="var(--c-sar)" stroke="var(--c-bg)"/>'
    s += '<circle cx="1000" cy="410" r="40" fill="none" stroke="var(--c-text-1)" stroke-width="2"/>'
    return s

def present_frame(step, total, hero, big, small, nxt, view, extra=""):
    bars = "".join(f'<i class="{"done" if i < step else "now" if i == step else ""}"></i>' for i in range(1, total+1))
    return (f'<div class="pbar">{bars}</div><div class="ptop">{SNAP}</div>' + view + '<div class="scrim"></div>'
      f'<div class="cap-big"><div class="step">Step {step} of {total}</div><h1>{big}</h1><p>{small}</p></div>'
      f'<div class="hints"><div class="nx">Next: {nxt}</div><div><span class="kbd">&rarr;</span> next step</div><div><span class="kbd">&larr;</span> back</div><div><span class="kbd">Esc</span> leave presentation</div></div>' + extra)

def build_present():
    dots = ""
    for _ in range(900):
        while True:
            lon, lat = random.gauss(-20, 70), random.gauss(15, 20)
            if -170 < lon < 175 and -50 < lat < 70:
                p = wp(lon, lat)
                if not w_is_land(p) and random.random() < .9: break
        dots += f"M{p[0]*1.44:.0f} {p[1]*1.44+60:.0f}h0"
    v1 = ('<svg style="position:absolute;inset:0;width:100%;height:100%" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice"><g transform="translate(0 60) scale(1.44)">'
          + grid(1000, 700, 100) + world_land() + '</g>'
          f'<path d="{dots}" stroke="var(--c-gap)" stroke-opacity=".55" stroke-width="3" stroke-linecap="round" fill="none"/></svg>'
          + funnel(1).replace('class="funnel"', 'class="funnel" style="left:64px;top:56px"'))
    f1 = present_frame(1, 7, True, "5,624 times a ship at sea went silent.",
         "Every ship is supposed to broadcast where it is. In three months, these went quiet for 6 to 96 hours.",
         "Two checks narrow it to 109, then 19.", v1)
    sc = ('<svg style="position:absolute;inset:0;width:100%;height:100%" viewBox="0 0 1440 900">' + case_present() + '</svg>'
      '<div class="lab" style="left:1050px;top:392px;max-width:230px;white-space:normal;border-color:var(--c-text-2)">Radar saw something here, outside the zone <small>(07:13 UTC)</small></div>'
      '<div class="lab" style="left:512px;top:330px">Where the ship could have been, when the satellite passed</div>'
      '<div class="lab" style="left:1040px;top:140px">Last ping <small>02 Sep 02:32</small></div><div class="lab" style="left:560px;top:482px">Back on air <small>03 Sep 11:20</small></div>'
      '<div style="position:absolute;z-index:21;left:64px;right:64px;top:556px"><div style="height:6px;background:var(--c-line-strong);border-radius:3px;position:relative">'
      '<span style="position:absolute;left:0;width:61%;height:6px;background:var(--c-text-3);border-radius:3px"></span>'
      '<span style="position:absolute;left:61%;top:-6px;width:18px;height:18px;border-radius:50%;background:var(--c-text-1)"></span>'
      '<span style="position:absolute;left:86%;top:-6px;width:16px;height:16px;background:var(--c-sar);transform:rotate(45deg) scale(.7)"></span></div>'
      '<div style="display:flex;justify-content:space-between;margin-top:14px;font:13px var(--font-mono);color:var(--c-text-3)"><span>Last ping</span><span style="margin-left:40%">Pass 1</span><span style="margin-left:auto;margin-right:8%;color:var(--c-sar)">Satellite passes: 4 h 46 min late</span><span>Back on air</span></div></div>')
    f3 = present_frame(3, 7, False, "Looked suspicious. Cleared.",
         "The satellite passed 4 h 46 min after the ship was back on air. It could not have seen this ship there.",
         "The best of the 19: one we cannot explain.", sc)
    pins1 = pin(1, 40, 702) + pin(2, 50, 44) + pin(3, 1206, 770) + pin(4, 700, 10) + pin(5, 1170, 18)
    notes = [
     "<b>One caption, bottom-left, always the same place.</b> One 44 px sentence the room reads, one 20 px line under it. The presenter says the script; the screen says the point.",
     "<b>The live view stays alive behind it.</b> Same screens, same data, scrim only where the caption sits. The presenter can still click. Steps are data (<span class=mono>route, focus, caption</span>), so reordering to the v3 Baltic-first story is an edit, not a rebuild.",
     "<b>Keys, shown once and quietly.</b> Right / left to move, Esc to leave. Bottom-right, 13 px, never over the view. P enters, from any screen.",
     "<b>Segmented progress bar</b> across the top edge: where we are in 7 steps with no number to read. The tiny \"Step 1 of 7\" and \"Next: ...\" are for the presenter.",
     "<b>Chrome is removed.</b> No nav, no rail, no filters; only the mode badge stays, so the audience always knows this is a snapshot, not live. Funnel numbers light up as each step reaches them (step 1: only 5,624).",
     "<b>Each step keeps its own action above the caption.</b> Step 3 keeps the timeline scrubber at y 556; the caption zone starts at 660 below it. Rule for step views: nothing interactive below 660 px.",
     "<b>Step 3 shows the verdict as a picture</b>: the radar diamond sits outside the violet zone, circled once in white. The word \"Cleared\" is the caption; the reason is the timing line.",
    ]
    page("04-present.html", "04 Presentation mode", [
        frame("04a Presentation mode, step 1 of 7 (V2) &middot; demo-script step 1: world map &middot; real: 5,624, 6 to 96 h; the dot field is a stand-in", f1 + pins1),
        frame("04b Presentation mode, step 3 of 7 (V2) &middot; demo-script step 3: a ruled-out case &middot; real: 4 h 46 min (script); positions and times are placeholders", f3 + pin(6, 40, 544) + pin(7, 954, 356))],
        "Presentation mode", notes, "Steps follow docs/demo-script.md (v0). The v3 draft in docs/plan.md opens on the Baltic instead; only the step list changes.")

# ---------------------------------------------------------------- 01 nav
def build_nav():
    def variant(cap, bar, extra=""):
        return f'<div style="margin-bottom:6px;padding:0 24px;font:12px var(--font-mono);color:var(--c-text-3)">{cap}</div>{bar}{extra}<div style="height:22px"></div>'
    crumb = '<div style="height:36px;display:flex;align-items:center;padding:0 24px;gap:8px;font-size:13px;color:var(--c-text-2);background:var(--c-bg);border-bottom:1px solid var(--c-line)">&lsaquo; Baltic &nbsp;/&nbsp; NORDIC VEGA, silence</div>'
    deg = badge("deg", "LIVE &middot; DELAYED 4 MIN")
    off = badge("err", "FEED LOST &middot; SHOWING 04:51")
    states = ('<div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:0 24px">'
        f'{LIVE}{REPLAY}{SNAP}{deg}{off}</div>'
        '<div style="display:flex;gap:16px;padding:8px 24px 0;font-size:12px;color:var(--c-text-3)"><span style="width:176px">Fresh stream (under 10 s old)</span><span style="width:240px">Scrubbed back in time</span><span style="width:260px">Frozen file, with its capture time</span><span style="width:210px">Stream late (amber)</span><span>Stream gone (red)</span></div>')
    cols = ('<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:32px;padding:0 24px;font-size:14px;color:var(--c-text-2)">'
      '<div><h3 style="color:var(--c-text-1);font-size:16px;margin-bottom:8px">Always visible</h3><ul style="margin-left:18px"><li>Where you are: three places, current underlined</li><li>Present, one click or <span class="kbd">P</span></li><li>What the data is: one mode badge, top right</li><li>Where it came from: attribution, bottom-left of every map</li></ul></div>'
      '<div><h3 style="color:var(--c-text-1);font-size:16px;margin-bottom:8px">Moves out of the bar</h3><ul style="margin-left:18px"><li>Region and period controls: they belong to the Global screen, next to what they filter</li><li>\"Case file\", \"Rendezvous\", \"Live\": no longer tabs. They are the next level inside Global and Baltic</li></ul></div>'
      '<div><h3 style="color:var(--c-text-1);font-size:16px;margin-bottom:8px">Keys</h3><ul style="margin-left:18px;list-style:none"><li><span class="kbd">1</span> <span class="kbd">2</span> <span class="kbd">3</span> switch place</li><li style="margin-top:6px"><span class="kbd">P</span> presentation</li><li style="margin-top:6px"><span class="kbd">Esc</span> back one level</li><li style="margin-top:6px"><span class="kbd">&larr;</span> <span class="kbd">&rarr;</span> steps, replay</li></ul></div></div>')
    inner = ('<div style="padding-top:20px;overflow:hidden">'
      + variant("Baltic, live", topbar("Baltic", LIVE))
      + variant("Baltic, scrubbed back in replay", topbar("Baltic", REPLAY))
      + variant("Global, frozen snapshot", topbar("Global", SNAP))
      + variant("Edge unit", topbar("Edge unit", LIVE))
      + variant("Two levels deep: the bar does not change, a breadcrumb row appears under it", topbar("Baltic", LIVE), crumb)
      + '<div style="padding:0 24px 8px;font:12px var(--font-mono);color:var(--c-text-3)">Mode badge states (same slot, same shape)</div>' + states + '<div style="height:34px"></div>' + cols + '</div>')
    inner += pin(1, 102, 53) + pin(2, 326, 53) + pin(3, 1128, 53) + pin(4, 1256, 53) + pin(5, 262, 451) + pin(6, 1076, 527) + pin(7, 176, 610)
    notes = [
     "<b>Brand = home.</b> It returns to the last place you were in. No separate Home tab.",
     "<b>Three places, one bar.</b> Global (the 5,624 &rarr; 19 investigation), Baltic (our own live recording), Edge unit (the camera that sees what AIS does not). Five tabs become three. Current place: underline plus brighter text, not colour.",
     "<b>Present sits next to the mode badge</b>, outside the three places, because it is a way of showing the app, not a place in it. Always one click, always <span class=mono>P</span>.",
     "<b>Mode badge, one slot, always.</b> Judges must never wonder whether they see live data or a file. Same pill shape, five states; amber and red only when something is wrong.",
     "<b>Depth is a breadcrumb, not more tabs.</b> Event, case and ship pages add a one-line row under the bar; the bar itself never changes, so nobody gets lost. Esc goes back one level.",
     "<b>Controls live where they act.</b> Region and period leave the bar; the Global screen carries them. The bar stays app-level: where, present, status.",
     "<b>Sizes:</b> bar 48 px, labels 13 px, badge 12 px mono, tab hit area 48 px tall. Everything passes the existing AA pairs in tokens.css; nothing new in the palette.",
    ]
    page("01-nav.html", "01 Navigation", [frame("01 Navigation bar and its states &middot; 1440 x 900 &middot; no data; times in badges are placeholders", inner)],
         "Navigation", notes, "Placeholder: badge times. Everything else is structure.")

build_nav(); build_baltic(); build_event(); build_present(); build_overview()
print("ok")
