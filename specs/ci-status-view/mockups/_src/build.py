import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from components import *
OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def w(name, html):
    open(os.path.join(OUT, name), "w").write(html)

# ======================= 1 · session-card =======================
def transcript(card_html, extra=""):
    return f'''<div class="col gap-16" style="padding:20px 24px 24px">
  <div class="msg"><span class="avatar av-user">JH</span><div class="body">
    <div class="who"><b>Jens Holze</b><span class="tm">14:10</span></div>
    <div class="text">Passt. Bitte pushen und die CI im Blick behalten.</div></div></div>
  <div class="msg"><span class="avatar av-coder">OC</span><div class="body">
    <div class="who"><b>omnigent-coder</b><span class="role">Agent</span><span class="tm">14:12</span></div>
    <div class="tool" style="margin:4px 0 8px"><span class="st sm pass"></span><span class="mono">git push -u origin HEAD</span><span class="faint">· 1,4 s</span></div>
    <div class="text">Gepusht: <code>cursor/remove-session-human-chat-f216</code> (<code>e752ad1</code>)</div>
    <div class="attach">{card_html}</div>{extra}</div></div>
</div>'''

def frame(title, inner):
    return f'''<div class="frame"><div class="frame-bar"><span class="dotb run" style="width:6px;height:6px;box-shadow:none"></span>Session · <span style="color:var(--tx-2)">{title}</span><span class="sp mono faint" style="font-size:11px">mac-studio · ~/omnigent</span></div>{inner}</div>'''

later = f'''<div style="margin-top:24px"><div class="state-label"><span class="n">1c</span>Eingeklappt · abgeschlossen (Pflicht grün, E2E rot nicht blockierend)</div>
  {card(PRS[34], 34, "collapsed")}</div>'''

html = head("Session-Karte") + f'''<div class="page">
 <div class="page-h"><div><div class="eyebrow">Variante 1 · session-card</div><h1>CI-Statuskarte im Session-Transkript</h1>
 <p>Erscheint direkt unter der Agenten-Nachricht nach einem Push. Dieselbe Komponente wird später im Team-Chat wiederverwendet.</p></div>
 <div class="sp row gap-16"><span class="annot">Segment je Check · Breite gleich, Füllung = Laufzeit / typische Dauer</span></div></div>
 <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start">
  <div><div class="state-label"><span class="n">1a</span>Eingeklappt · Standard</div>
   {frame("remove session human chat", transcript(card(PRS[30], 30, "collapsed")))}
   {later}</div>
  <div><div class="state-label"><span class="n">1b</span>Ausgeklappt · nach Klick auf „Details“</div>
   {frame("remove session human chat", transcript(card(PRS[30], 30, "expanded")))}</div>
 </div>
</div>''' + TAIL
w("session-card.html", html)

# ======================= 2 · sidebar-panel =======================
def pr_row(num, pr, hl=False):
    s = pr["status"]
    py = pr["checks"]["py"]
    if s == "run":   info = f'Pytest {py["t"].split(" /")[0]} / ~19 Min'
    elif s == "queued": info = "Warteschlange #1–2"
    else: info = "beendet 14:12 · 23:24 Min"
    eta = {"run": f'<span class="eta">{pr["eta"]}<br><span class="subtle" style="font-size:11px;font-weight:400">ETA {pr["eta_l"].split("ca. ")[-1]}</span></span>',
           "queued": '<span class="eta">Start<br><span class="subtle" style="font-size:11px;font-weight:400">ca. 14:36</span></span>',
           "soft": '<span class="pill xs soft"><span class="st sm soft"></span>E2E</span>'}[s]
    icon = status_icon("pass" if s=="soft" else s)
    return f'''<div class="pr {"hl" if hl else ""}">{icon}<div class="title ellipsis"><span class="num">#{num}</span>{pr["title"]}</div>{eta}
      <div class="r2">{chips(pr)}<span class="ellipsis">{info}</span></div></div>'''

def runner_card(name, pr, job, p, el, rem):
    return f'''<div class="runner"><div class="row gap-8"><span class="pulse" style="width:6px;height:6px"></span><span class="rn">{name}</span>
      <span class="sp pill xs run">beschäftigt</span></div>
      <div class="job"><span class="chip run">PY</span><span style="color:var(--tx)">#{pr}</span><span class="ellipsis">{job}</span></div>
      <div style="margin-top:8px" class="bar run"><b style="--p:{p}%"></b></div>
      <div class="tm"><span>{el} vergangen</span><span>{rem}</span></div></div>'''

panel = f'''<aside class="panel">
 <div class="panel-head"><span class="pulse"></span><b>CI-Status</b><span class="subtle" style="font-size:12px">omnigent/omnigent</span>
   <span class="sp row gap-4"><span class="live-tag">Live</span><span class="btn ghost sm" style="padding:0 6px">⤢</span><span class="btn ghost sm" style="padding:0 6px">✕</span></span></div>
 <div class="panel-tabs"><span class="on">Pull Requests <span class="subtle">4</span></span><span>Runner</span><span>Verlauf</span><span class="sp" style="color:var(--tx-3)">Nur meine</span></div>
 <div class="panel-body">
  <div class="grp"><div class="grp-h"><span class="st sm run"></span>Läuft<span class="c">2</span></div>
    {pr_row(30, PRS[30], True)}{pr_row(28, PRS[28])}</div>
  <div class="grp"><div class="grp-h"><span class="st sm queued"></span>Wartet<span class="c">1</span></div>{pr_row(29, PRS[29])}</div>
  <div class="grp"><div class="grp-h"><span class="st sm pass"></span>Fertig<span class="c">1</span><span class="sp" style="text-transform:none;letter-spacing:0;font-weight:500">letzte 2 h</span></div>{pr_row(34, PRS[34])}</div>
  <div style="padding:8px 16px 0"><div class="divider"></div>
   <div class="row" style="margin:8px 0"><span class="section-label">Runner</span><span class="sp subtle" style="font-size:11.5px">2/2 beschäftigt · self-hosted macOS</span></div>
   {runner_card(R1, 30, "Pytest (SQLite)", 58, "11:04", "~8 Min verbleibend")}
   {runner_card(R2, 28, "Pytest (SQLite)", 42, "8:02", "~11 Min verbleibend")}
   <div class="load" style="margin-top:8px"><span class="subtle" style="align-self:center;white-space:nowrap">Mac-Last</span>
     <div class="lv"><div class="row"><span>CPU</span><b>92 %</b></div><div class="bar run"><b style="--p:92%"></b></div></div>
     <div class="lv"><div class="row"><span>RAM</span><b>55 %</b></div><div class="bar accent"><b style="--p:55%"></b></div></div></div>
   <div class="row" style="margin:16px 0 4px"><span class="section-label">Warteschlange</span><span class="subtle" style="font-size:11.5px;margin-left:8px">2 Jobs</span><span class="sp subtle" style="font-size:11.5px">nächster Start ca. 14:36</span></div>
   <div class="q"><span class="pos">1</span><span class="chip queued">LINT</span><span style="color:var(--tx)">#29</span><span class="ellipsis">Python lint (ruff, pyrefly)</span><span class="sp subtle">wartet 4 Min</span></div>
   <div class="q"><span class="pos">2</span><span class="chip queued">WEB</span><span style="color:var(--tx)">#29</span><span class="ellipsis">Web (lint, typecheck, vitest, build)</span><span class="sp subtle">wartet 4 Min</span></div>
  </div>
 </div></aside>'''

main = f'''<main class="main">
 <div class="topbar"><span class="crumb">Sessions / <b>remove session human chat</b></span>
  <span class="sp row gap-8"><span class="pill sm run"><span class="st sm run"></span>CI läuft · 3/4</span><span class="btn sm">PR #30 <span style="font-size:11px">↗</span></span></span></div>
 <div class="transcript"><div class="tx-inner">
  <div class="msg"><span class="avatar av-user">JH</span><div class="body"><div class="who"><b>Jens Holze</b><span class="tm">14:10</span></div>
   <div class="text">Passt. Bitte pushen und die CI im Blick behalten.</div></div></div>
  <div class="msg"><span class="avatar av-coder">OC</span><div class="body"><div class="who"><b>omnigent-coder</b><span class="role">Agent</span><span class="tm">14:12</span></div>
   <div class="text">Gepusht: <code>cursor/remove-session-human-chat-f216</code> (<code>e752ad1</code>)</div>
   <div class="attach">{card(PRS[30], 30, "collapsed")}</div></div></div>
  <div class="msg"><span class="avatar av-coder">OC</span><div class="body"><div class="who"><b>omnigent-coder</b><span class="role">Agent</span><span class="tm">14:13</span></div>
   <div class="text">Während Pytest läuft, schaue ich mir die übrigen Referenzen auf <code>human_chat</code> im Frontend an. Bisher 3 Treffer in <code>web/src/session/</code>.</div></div></div>
 </div></div>
 <div class="composer"><div class="ph">Nachricht an omnigent-coder …</div><div class="tools"><span class="tag">omnigent-coder</span><span class="tag">mac-studio</span><span class="sp btn primary sm">Senden</span></div></div>
</main>'''
w("sidebar-panel.html", head("Sidebar-Panel") + f'<div class="shell with-panel">{sidebar(ci_open=True)}{main}{panel}</div>' + TAIL_LEFT)

# ======================= 3 · runner-timeline =======================
T0 = 13*60+58; SPAN = 40
def pct(h, m, s=0): return max(0.0, ((h*60+m+s/60) - T0) / SPAN * 100)
def bar(l, r, cls, label="", clip=False, extra=""):
    width = r - l
    return f'<div class="tl-bar {cls} {"clip" if clip else ""}" style="left:{l:.2f}%;width:{width:.2f}%" {extra}><span>{label}</span></div>'
NOW = pct(14,28)
r1 = [bar(0, pct(14,8,12), "pass", "#34 Pytest", True), bar(pct(14,8,20), pct(14,12,32), "soft", "#34 E2E"),
      bar(pct(14,12,40), pct(14,13,4), "pass"), bar(pct(14,13,10), pct(14,15,13), "pass", "#30 Web"),
      bar(pct(14,15,20), pct(14,15,42), "pass"), bar(pct(14,17), NOW, "run sel", "#30 Pytest (SQLite) · 11:04"),
      bar(NOW, pct(14,36), "fc run-fc", "ETA 14:36"), bar(pct(14,36,10), pct(14,36,35), "fc"), bar(pct(14,36,40), pct(14,38,40), "fc", "#29 Web")]
r2 = [bar(0, pct(14,16), "pass", "#33 Pytest (SQLite) · 18:30", True), bar(pct(14,16,10), pct(14,18,7), "pass", "#28 Web"),
      bar(pct(14,20), NOW, "run", "#28 Pytest · 8:02"), bar(NOW, 100, "fc run-fc", "ETA 14:39 →")]
ticks = ""
for i,(h,m) in enumerate([(14,0),(14,5),(14,10),(14,15),(14,20),(14,25),(14,30),(14,35)]):
    ticks += f'<span class="tick" style="left:{pct(h,m):.2f}%">{h}:{m:02d}</span>'
# CPU-Verlauf (0..NOW)
import math
pts = []
cpu = [38,42,55,61,58,64,60,57,63,70,66,62,58,72,78,74,69,88,91,93,90,94,92,95,91,93,92,94,92,92,92]
for i,v in enumerate(cpu):
    x = i/(len(cpu)-1)*NOW; pts.append(f"{x:.2f},{100-v:.1f}")
poly = " ".join(pts)
ram = [41,42,44,46,45,47,46,46,48,49,49,48,47,50,52,52,51,54,55,56,55,55,56,55,55,56,55,55,55,55,55]
rpts = " ".join(f"{i/(len(ram)-1)*NOW:.2f},{100-v:.1f}" for i,v in enumerate(ram))

tl_css = '''<style>
.tl{position:relative}
.tl-grid{display:grid;grid-template-columns:216px 1fr}
.tl-lab{padding:0 16px;display:flex;flex-direction:column;justify-content:center;border-bottom:1px solid var(--line);height:80px}
.tl-lab .rn{font:500 12px/16px var(--mono);color:var(--tx)}
.tl-lab .sub{font-size:11.5px;color:var(--tx-3);margin-top:2px;display:flex;align-items:center;gap:6px}
.tl-track{position:relative;height:80px;border-bottom:1px solid var(--line);
  background-image:linear-gradient(90deg,var(--line) 1px,transparent 1px);background-size:calc(100%/8) 100%;background-position:calc(100%*2/40*1) 0}
.tl-bar{position:absolute;top:24px;height:32px;border-radius:6px;display:flex;align-items:center;padding:0 8px;overflow:hidden;
  font:500 11.5px/1 var(--sans);white-space:nowrap;min-width:3px}
.tl-bar span{overflow:hidden;text-overflow:ellipsis}
.tl-bar.pass{background:rgba(62,207,142,.16);border:1px solid rgba(62,207,142,.38);color:#8be3b9}
.tl-bar.pass:not(:has(span:not(:empty))){padding:0}
.tl-bar.soft{background:repeating-linear-gradient(-45deg,rgba(224,123,83,.18) 0 5px,rgba(224,123,83,.07) 5px 10px);border:1px dashed var(--soft-line);color:#f0a585}
.tl-bar.run{background:repeating-linear-gradient(-45deg,rgba(245,165,36,.32) 0 6px,rgba(245,165,36,.18) 6px 12px);border:1px solid rgba(245,165,36,.6);color:#ffd58f;border-radius:6px 0 0 6px;border-right:0}
.tl-bar.sel{box-shadow:0 0 0 2px rgba(245,165,36,.35),0 0 18px rgba(245,165,36,.25)}
.tl-bar.fc{background:transparent;border:1px dashed var(--line-3);color:var(--tx-3)}
.tl-bar.run-fc{border:1px dashed rgba(245,165,36,.45);border-left:0;border-radius:0 6px 6px 0;color:rgba(249,197,110,.75);background:rgba(245,165,36,.05)}
.tl-bar.clip{border-top-left-radius:0;border-bottom-left-radius:0;border-left-style:dashed}
.tl-future{position:absolute;top:0;bottom:0;right:0;background:repeating-linear-gradient(135deg,rgba(255,255,255,.018) 0 6px,transparent 6px 12px);border-left:0}
.tl-now{position:absolute;top:-28px;bottom:0;width:2px;background:var(--accent);z-index:4;box-shadow:0 0 12px rgba(124,131,255,.6)}
.tl-now b{position:absolute;top:0;left:50%;transform:translateX(-50%);background:var(--accent);color:#0b0b1a;font:600 11px/1 var(--sans);padding:4px 6px;border-radius:4px;white-space:nowrap}
.tl-axis{position:relative;height:28px;border-bottom:1px solid var(--line)}
.tick{position:absolute;top:8px;transform:translateX(-50%);font:500 11px/1 var(--mono);color:var(--tx-3)}
.stat{padding:16px;border:1px solid var(--line);border-radius:var(--r-3);background:var(--bg-1)}
.stat .v{font:600 22px/28px var(--sans);letter-spacing:-.01em;font-variant-numeric:tabular-nums;margin-top:4px}
.stat .v small{font-size:13px;color:var(--tx-3);font-weight:500}
.leg{display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--tx-3)}
.leg i{width:16px;height:10px;border-radius:3px;display:inline-block}
</style>'''
tip = f'''<div class="tip below" style="left:calc(100% - 304px);top:64px;--ax:48px;width:296px">
  <div class="row gap-8"><span class="st run"></span><b style="font-weight:600">Pytest (SQLite)</b><span class="sp tag req">Pflicht</span></div>
  <div class="muted" style="margin-top:4px">#30 · remove session human chat</div>
  <div style="margin-top:10px" class="bar run"><b style="--p:58%"></b></div>
  <dl><dt>Gestartet</dt><dd>14:17:00</dd><dt>Vergangen</dt><dd>11:04 Min</dd><dt>Typisch (letzte 20)</dt><dd>~19 Min</dd>
  <dt>ETA</dt><dd style="color:#f9c56e">~8 Min · 14:36</dd><dt>Runner</dt><dd class="mono" style="font-size:11px">{R1}</dd></dl>
  <div class="row gap-8" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--line)"><a class="link log">Log</a><span class="faint">·</span><a class="link">Session öffnen</a><span class="sp kbd">L</span></div></div>'''

timeline = f'''<div class="card tl" style="overflow:visible">
 <div class="row gap-12" style="padding:16px 20px;border-bottom:1px solid var(--line)">
   <div><div class="row gap-8"><span class="pulse"></span><b style="font-weight:600;font-size:14px">Runner-Zeitleiste</b><span class="live-tag">Live</span></div>
   <div class="subtle" style="font-size:12px;margin-top:2px">Letzte 30 Min + 10 Min Prognose · self-hosted macOS · <span class="mono">mac-studio</span></div></div>
   <div class="sp row gap-16"><span class="leg"><i style="background:rgba(62,207,142,.4)"></i>erfolgreich</span><span class="leg"><i style="background:repeating-linear-gradient(-45deg,var(--run) 0 3px,#a8670a 3px 6px)"></i>läuft</span>
   <span class="leg"><i style="border:1px dashed var(--soft)"></i>rot, nicht blockierend</span><span class="leg"><i style="border:1px dashed var(--tx-3)"></i>Prognose</span>
   <span class="panel-tabs" style="border:0;padding:0;margin-left:8px"><span>15 Min</span><span class="on">30 Min</span><span>2 h</span></span></span></div>
 </div>
 <div class="tl-grid">
   <div class="tl-lab" style="height:28px;padding-top:0"></div>
   <div class="tl-axis">{ticks}</div>
   <div class="tl-lab"><span class="rn">{R1}</span><span class="sub"><span class="pill xs run">beschäftigt</span>#30 Pytest</span></div>
   <div class="tl-track"><div class="tl-future" style="left:{NOW}%"></div>{"".join(r1)}</div>
   <div class="tl-lab"><span class="rn">{R2}</span><span class="sub"><span class="pill xs run">beschäftigt</span>#28 Pytest</span></div>
   <div class="tl-track"><div class="tl-future" style="left:{NOW}%"></div>{"".join(r2)}</div>
   <div class="tl-lab"><span class="rn" style="font-family:var(--sans)">Warteschlange</span><span class="sub">2 Jobs · #29</span></div>
   <div class="tl-track"><div class="tl-future" style="left:{NOW}%"></div>
     {bar(pct(14,24), NOW, "fc", "")}<span style="position:absolute;right:{100-pct(14,24)+0.6:.2f}%;top:31px;font-size:11.5px;color:var(--tx-2);white-space:nowrap"><span class="chip queued">LINT</span> <span class="chip queued">WEB</span> #29 wartet seit 14:24 →</span></div>
   <div class="tl-lab" style="border-bottom:0;height:88px"><span class="rn" style="font-family:var(--sans)">Mac-Last</span><span class="sub"><span style="color:#f9c56e">CPU 92 %</span>·<span style="color:var(--accent-2)">RAM 55 %</span></span></div>
   <div class="tl-track" style="height:88px;border-bottom:0"><div class="tl-future" style="left:{NOW}%"></div>
     <svg viewBox="0 0 100 100" preserveAspectRatio="none" style="position:absolute;inset:10px 0 6px 0;width:100%;height:calc(100% - 16px)">
       <defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#f5a524" stop-opacity=".35"/><stop offset="1" stop-color="#f5a524" stop-opacity="0"/></linearGradient></defs>
       <line x1="0" x2="100" y1="10" y2="10" stroke="#3a3a42" stroke-dasharray="1 1" stroke-width=".4" vector-effect="non-scaling-stroke"/>
       <polygon points="0,100 {poly} {NOW:.2f},100" fill="url(#g)"/>
       <polyline points="{poly}" fill="none" stroke="#f5a524" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
       <polyline points="{rpts}" fill="none" stroke="#7c83ff" stroke-width="1.5" stroke-dasharray="3 2" vector-effect="non-scaling-stroke"/>
     </svg>
     <span style="position:absolute;left:{pct(14,20):.2f}%;top:2px;font-size:10.5px;color:#f9c56e;transform:translateX(-50%);background:var(--bg-1);padding:0 4px">2× Pytest parallel</span></div>
 </div>
 <div class="tl-now" style="left:calc(216px + (100% - 216px) * {NOW/100:.4f} - 1px);top:{73+0}px;bottom:0"><b>jetzt 14:28</b></div>
 {"" }
 <div style="position:absolute;left:216px;right:0;top:{73+28}px;bottom:0;pointer-events:none">{tip}</div>
</div>'''

why = '''<div class="card" style="padding:16px 20px">
  <div class="row gap-8"><span class="section-label">Warum ist der Mac ausgelastet?</span></div>
  <div style="margin-top:8px;font-size:13px;line-height:20px;color:#d6d6db">Beide Runner führen gleichzeitig <b>Pytest (SQLite)</b> aus (#30 und #28) – typisch je ~19 Min und CPU-lastig. Dadurch warten die zwei Jobs von <b>#29</b> seit 4 Min. Erster freier Runner voraussichtlich <b class="tnum">14:36</b>.</div>
  <div class="row gap-8" style="margin-top:12px"><span class="btn sm">#29 priorisieren</span><span class="btn ghost sm">Typische Dauern ansehen</span></div></div>'''
stats = f'''<div style="display:grid;grid-template-columns:repeat(3,1fr) 1.6fr;gap:16px;margin-top:16px">
 <div class="stat"><span class="section-label">Auslastung · 30 Min</span><div class="v">96 <small>%</small></div><div class="subtle" style="font-size:12px">{R1.split("-")[-1]}: 94 % · -2: 97 %</div></div>
 <div class="stat"><span class="section-label">Warteschlange</span><div class="v">2 <small>Jobs</small></div><div class="subtle" style="font-size:12px">ø Wartezeit heute 3:40 Min</div></div>
 <div class="stat"><span class="section-label">Nächster freier Runner</span><div class="v">14:36 <small>· in ~8 Min</small></div><div class="subtle mono" style="font-size:11px">{R1}</div></div>
 {why}</div>'''

main3 = f'''<main class="main">
 <div class="topbar"><span class="crumb">CI / <b>Runner</b></span><span class="sp row gap-8"><span class="pill sm run"><span class="st sm run"></span>3 PRs aktiv</span><span class="btn sm">Listenansicht</span></span></div>
 <div style="padding:24px">{timeline}{stats}</div></main>'''
w("runner-timeline.html", head("Runner-Zeitleiste") + tl_css + f'<div class="shell">{sidebar(ci_open=True)}{main3}</div>' + TAIL)

# ======================= 4 · chat-card-live =======================
F = PRS_FUTURE
fail_box = '''<div class="surface" style="margin-top:8px;padding:10px 12px;background:var(--bg-1);border-color:rgba(242,85,90,.28)">
  <div class="row gap-8" style="font-size:12px"><span class="st sm fail"></span><b style="font-weight:600">Pytest (SQLite)</b><span class="subtle">· #29 · 2 von 1.184 Tests rot · nach 6:12 Min</span><a class="sp link log" style="font-size:12px">Log</a></div>
  <div class="mono" style="margin-top:8px;font-size:11.5px;line-height:18px;color:#ffb3b5">
   FAILED tests/test_session_merge.py::<b>test_merge_keeps_workspace</b><br>
   FAILED tests/test_ci_hooks.py::<b>test_rebase_on_conflict</b></div>
  <div class="mono subtle" style="font-size:11.5px;line-height:18px;margin-top:4px">AssertionError: expected host_id='h-02', got None</div></div>'''
chip_row = '''<div class="row gap-8" style="margin-top:10px;flex-wrap:wrap">
  <span class="btn sm" style="border-color:var(--accent-line);background:var(--accent-bg);color:var(--accent-2)"><span class="avatar sm av-coder" style="width:16px;height:16px;font-size:8px;border-radius:4px">OC</span>An omnigent-coder übergeben ✓</span>
  <span class="btn sm">Log öffnen</span><span class="btn sm">Fehlgeschlagene neu starten</span><span class="btn ghost sm">Stumm bis grün</span></div>'''
chat = f'''<main class="main">
 <div class="topbar"><span class="crumb"><span class="faint">#</span> <b>ci-omnigent</b></span><span class="subtle" style="font-size:12px">PM HUB, omnigent-coder, Jens Holze</span>
  <span class="sp row gap-8"><span class="pill sm fail"><span class="st sm fail"></span>1 rot</span><span class="pill sm run"><span class="st sm run"></span>1 läuft</span></span></div>
 <div class="transcript" style="padding-top:16px"><div class="tx-inner" style="gap:16px">
  <div class="row gap-12 subtle" style="font-size:11.5px"><span style="flex:1;height:1px;background:var(--line)"></span>Heute<span style="flex:1;height:1px;background:var(--line)"></span></div>
  <div class="msg"><span class="avatar av-pm">PM</span><div class="body">
   <div class="who"><b>PM HUB</b><span class="role">Lead-Agent</span><span class="tm">14:36</span></div>
   <div class="text">#30 hat alle Pflicht-Checks grün. Ich halte die Karte live, bis E2E durch ist.</div>
   <div class="attach">{card(F[30], 30, "collapsed", live=True, live_txt="vor 4 s")}</div></div></div>
  <div class="msg"><span class="avatar av-pm">PM</span><div class="body">
   <div class="who"><b>PM HUB</b><span class="role">Lead-Agent</span><span class="tm">14:45</span><span class="pill xs fail" style="margin-left:4px">CI rot</span></div>
   <div class="text"><b style="color:#ff9a9d">Pytest rot auf #29</b> – ich gebe das an <span style="color:var(--accent-2);background:var(--accent-bg);padding:1px 4px;border-radius:4px">@omnigent-coder</span></div>
   <div class="attach">{card(F[29], 29, "collapsed", live=False)}</div>
   {fail_box}{chip_row}</div></div>
  <div class="msg"><span class="avatar av-coder">OC</span><div class="body">
   <div class="who"><b>omnigent-coder</b><span class="role">Agent</span><span class="tm">14:46</span></div>
   <div class="text">Übernehme. Reproduziere lokal mit <code>pytest -k test_merge_keeps_workspace</code> – vermutlich fehlt nach dem Merge das Rebinding von <code>host_id</code>.</div>
   <div class="row gap-8 subtle" style="font-size:12px;margin-top:6px"><span class="pulse accent" style="width:6px;height:6px"></span>arbeitet in Session „CI merge fixes“ …</div></div></div>
 </div></div>
 <div class="composer"><div class="ph">Nachricht an #ci-omnigent …</div><div class="tools"><span class="tag">/ci</span><span class="tag">@PM HUB</span><span class="sp btn primary sm">Senden</span></div></div>
</main>'''

def snap(time, label, pr_states, icon, now=False):
    segs = "".join(f'<i class="{s}" style="--p:{p}%"></i>' for s,p in pr_states)
    return f'''<div class="row gap-12" style="align-items:flex-start;position:relative;padding-bottom:16px">
      <div class="col" style="align-items:center;width:16px">{status_icon(icon)}</div>
      <div class="grow"><div class="row gap-8" style="font-size:12px"><span class="tnum subtle mono" style="font-size:11px">{time}</span><span style="color:var(--tx)">{label}</span>{'<span class="sp live-tag"><span class="pulse" style="width:6px;height:6px"></span>jetzt</span>' if now else ''}</div>
      <div class="seg thin" style="margin-top:8px">{segs}</div></div></div>'''
right = f'''<aside class="panel" style="box-shadow:none">
 <div class="panel-head"><b>Live-Verlauf</b><span class="subtle" style="font-size:12px">Karten-Updates in diesem Kanal</span></div>
 <div style="padding:16px">
  <div class="section-label" style="margin-bottom:12px">#29 · CI merge fixes</div>
  {snap("14:36:10","Karte erstellt · alle wartend",[("queued",0)]*4,"queued")}
  {snap("14:36:35","Lint grün",[("pass",0),("run",40),("queued",0),("queued",0)],"pass")}
  {snap("14:38:41","Web grün · Pytest startet",[("pass",0),("pass",0),("run",5),("queued",0)],"pass")}
  {snap("14:45:20","Pytest rot → Follow-up gepostet",[("pass",0),("pass",0),("fail",0),("queued",0)],"fail")}
  <div class="divider" style="margin:4px 0 16px"></div>
  <div class="section-label" style="margin-bottom:12px">#30 · remove session human chat</div>
  {snap("14:36:02","Pflicht grün · Karte gepostet",[("pass",0),("pass",0),("pass",0),("queued",0)],"pass")}
  {snap("14:46:24","E2E läuft · 1:42 / ~6 Min",[("pass",0),("pass",0),("pass",0),("run",28)],"run", now=True)}
  <div class="divider" style="margin:4px 0 16px"></div>
  <div class="section-label" style="margin-bottom:8px">Regeln für PM HUB</div>
  <div class="col gap-8" style="font-size:12px;color:var(--tx-2)">
   <div class="row gap-8"><span class="st sm fail"></span>Pflicht-Check rot → Follow-up + Übergabe an Autor-Agent</div>
   <div class="row gap-8"><span class="st sm soft"></span>Nicht blockierend rot → nur Karte aktualisieren</div>
   <div class="row gap-8"><span class="st sm pass"></span>Alles grün → Karte einklappen, keine neue Nachricht</div>
  </div>
  <div class="subtle" style="font-size:11.5px;margin-top:16px;line-height:18px">Karten aktualisieren sich in-place (kein Nachrichten-Spam). Nur Statuswechsel auf <span style="color:#ff9a9d">rot</span> erzeugen eine neue Nachricht.</div>
 </div></aside>'''
w("chat-card-live.html", head("Team-Chat-Karte live") + '<style>.shell.chat{grid-template-columns:240px minmax(0,1fr) 360px}</style>' +
  f'<div class="shell chat">{sidebar(team=True)}{chat}{right}</div>' + TAIL)

# ======================= 5 · compact-badge =======================
variants = [
 ("pass","Alle grün","4/4", "pass","Alle Checks erfolgreich"),
 ("run","Läuft","2/4 · ~8 Min","run","Mind. ein Check läuft"),
 ("queued","Wartet","Queue #1","queued","Wartet auf freien Runner"),
 ("fail","Rot","Pytest","fail","Pflicht-Check fehlgeschlagen – blockiert Merge"),
 ("soft","Rot · nicht blockierend","E2E","soft","Nur E2E rot – Merge möglich"),
]
chipsets = {"pass":["pass"]*4,"run":["pass","pass","run","queued"],"queued":["queued"]*4,"fail":["pass","pass","fail","skip"],"soft":["pass","pass","pass","soft"]}
rows = ""
for s,label,n,dot,desc in variants:
    ch = "".join(f'<span class="chip {"queued" if c=="skip" else c}">{CHIP[k]}</span>' for c,(k,*_) in zip(chipsets[s],CHECKS))
    segs = "".join(f'<i class="{"queued" if c=="skip" else c}" style="--p:55%"></i>' for c in chipsets[s])
    ic = status_icon(s if s!="queued" else "queued", "sm")
    rows += f'''<tr><td><div class="row gap-8">{status_icon(s)}<span style="color:var(--tx);font-weight:500">{label}</span></div><div class="subtle" style="font-size:11.5px;margin-top:2px">{desc}</div></td>
     <td><span class="pill {s}">{status_icon(s,"sm")}{label}<span class="n">· {n}</span></span></td>
     <td><span class="pill sm {s}">{ic}{"E2E rot" if s=="soft" else label}</span></td>
     <td><span class="dotb {dot}"></span></td>
     <td><span class="chips">{ch}</span></td>
     <td style="width:120px"><div class="seg thin">{segs}</div></td></tr>'''
ctx_list = f'''<div class="surface" style="padding:8px;width:100%">
  <div class="sb-sess active"><span class="ellipsis">remove session human chat</span><span class="pill xs run"><span class="st sm run"></span>~8 Min</span></div>
  <div class="sb-sess"><span class="ellipsis">docs: import omnigent specs</span><span class="pill xs run"><span class="st sm run"></span>~11 Min</span></div>
  <div class="sb-sess"><span class="ellipsis">CI merge fixes</span><span class="pill xs queued"><span class="st sm queued"></span>Wartet</span></div>
  <div class="sb-sess"><span class="ellipsis">Rebind an idle session's…</span><span class="pill xs soft"><span class="st sm soft"></span>E2E</span></div>
  <div class="sb-sess"><span class="ellipsis">host heartbeat backoff</span><span class="pill xs pass"><span class="st sm pass"></span>Grün</span></div>
  <div class="sb-sess"><span class="ellipsis">pyrefly config cleanup</span><span class="faint" style="font-size:11px">kein PR</span></div></div>'''
pr_header = lambda num, pr, pill: f'''<div class="surface" style="padding:12px 16px;display:flex;align-items:center;gap:12px">
  <span style="color:{STATUS_COLOR[pr["status"]]};display:flex">{PR_ICON}</span><div class="grow"><div style="font-weight:600" class="ellipsis">{pr["title"]} <span class="subtle" style="font-weight:500">#{num}</span></div>
  <div class="mono subtle ellipsis" style="font-size:11px">{pr["branch"]} → main</div></div>{pill}{chips(pr)}</div>'''
html = head("Kompakte Badges") + f'''<style>
table.g{{width:100%;border-collapse:collapse}} .g th{{text-align:left;font:600 11px/16px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--tx-3);padding:0 16px 10px;border-bottom:1px solid var(--line)}}
.g td{{padding:14px 16px;border-bottom:1px solid var(--line);vertical-align:middle}} .g tr:last-child td{{border-bottom:0}}
</style><div class="page" id="shot" style="min-height:0;width:1200px;padding:32px">
 <div class="page-h"><div><div class="eyebrow">Variante 5 · compact-badge</div><h1>Status-Badges · Zustandsgalerie</h1>
  <p>Für Session-Listen, PR-Header und Team-Chat. Farbe nie allein: jede Variante hat eigenes Icon + Text.</p></div></div>
 <div class="card" style="padding:16px 0 4px"><table class="g"><thead><tr><th>Zustand</th><th>Pill · Standard (24 px)</th><th>Pill · kompakt (20 px)</th><th>Punkt</th><th>Check-Chips</th><th>Mini-Balken</th></tr></thead><tbody>{rows}</tbody></table></div>
 <div style="display:grid;grid-template-columns:320px 1fr;gap:24px;margin-top:24px">
  <div><div class="state-label">Im Kontext · Session-Liste</div>{ctx_list}</div>
  <div><div class="state-label">Im Kontext · PR-Header</div><div class="col gap-8">
   {pr_header(30, PRS[30], '<span class="pill run"><span class="st sm run"></span>Läuft<span class="n">· 2/4 · ~8 Min</span></span>')}
   {pr_header(29, PRS_FUTURE[29], '<span class="pill fail"><span class="st sm fail"></span>Rot<span class="n">· Pytest</span></span>')}
   {pr_header(34, PRS[34], '<span class="pill soft"><span class="st sm soft"></span>Rot · nicht blockierend<span class="n">· E2E</span></span>')}
  </div>
  <div class="row gap-16" style="margin-top:16px;font-size:11.5px;color:var(--tx-3)"><span class="row gap-4"><span class="kbd">gestreift</span> = läuft (statisch für Animation)</span><span class="row gap-4"><span class="kbd">gestrichelt</span> = wartet / nicht blockierend</span></div></div>
 </div>
 <div class="demo-flag inline">Beispieldaten</div>
</div></body></html>'''
w("compact-badge.html", html)

# ======================= index =======================
items = [("session-card","1 · Session-Karte","Inline-Statuskarte im Transkript, eingeklappt & ausgeklappt."),
 ("sidebar-panel","2 · Sidebar + Panel","Live-Indikator in der Sidebar, rechtes Panel mit PRs, Runnern und Queue."),
 ("runner-timeline","3 · Runner-Zeitleiste","Gantt-artige Zeitleiste der zwei Mac-Runner mit Jetzt-Linie und Tooltip."),
 ("chat-card-live","4 · Team-Chat live","Karte im Team-Chat vom PM HUB, Live-Update und Follow-up bei Rot."),
 ("compact-badge","5 · Kompakte Badges","Zustandsgalerie der Status-Pills für Listen und PR-Header.")]
cards = "".join(f'''<a href="{s}.html" class="card" style="display:block;overflow:hidden"><div style="height:200px;background:var(--bg-2) url(png/{s}.png) top left/cover no-repeat;border-bottom:1px solid var(--line)"></div>
 <div style="padding:16px"><div style="font-weight:600">{t}</div><div class="subtle" style="font-size:12.5px;margin-top:4px">{d}</div>
 <div class="row gap-12" style="margin-top:12px;font-size:12px"><span class="link">HTML öffnen</span><span class="faint">·</span><span class="link" onclick="event.preventDefault();location.href='png/{s}.png'">PNG</span></div></div></a>''' for s,t,d in items)
w("index.html", head("Übersicht") + f'''<div class="page"><div class="page-h"><div><div class="eyebrow">Omnigent · Spezifikation</div><h1>CI-Statusansicht · GUI-Mockups</h1>
<p>Statische HTML/CSS-Mockups, gemeinsames Stylesheet <span class="code-inline">ci.css</span>. Self-hosted Runner: <span class="mono">{R1}</span>, <span class="mono">{R2}</span>.</p></div></div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:24px">{cards}</div></div>''' + TAIL)
print("built")
