"""Gemeinsame HTML-Bausteine für die CI-Status-Mockups (statisch generiert)."""

CHECKS = [
    ("lint", "Python lint (ruff, pyrefly)", "Lint", True),
    ("web",  "Web (lint, typecheck, vitest, build)", "Web", True),
    ("py",   "Pytest (SQLite)", "Pytest", True),
    ("e2e",  "E2E UI (Playwright, non-blocking)", "E2E", False),
]
CHIP = {"lint": "LINT", "web": "WEB", "py": "PY", "e2e": "E2E"}
R1, R2 = "actions-runner-omnigent", "actions-runner-omnigent-2"

PR_ICON = ('<svg class="ci-gh" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 1 10 .854V2.5h1A2.5 2.5 0 0 1 13.5 5v5.628a2.251 2.251 0 1 1-1.5 0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 3.427a.25.25 0 0 1 0-.354ZM3.75 2.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm0 9.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm8.25.75a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Z"/></svg>')
STATUS_COLOR = {"run": "var(--run)", "pass": "var(--ok)", "fail": "var(--fail)", "queued": "var(--tx-3)", "soft": "var(--ok)"}

# ---------------- Beispieldaten ----------------
PRS = {
 30: dict(title="remove session human chat", branch="cursor/remove-session-human-chat-f216", sha="e752ad1",
          started="gestartet 14:12", status="run", eta="~8 Min", eta_l="fertig ca. 14:36", run_no="#412",
          summary="<b>2/3</b> Pflicht-Checks grün · Pytest läuft <b>11:04</b> von typ. ~19 Min",
          checks={
            "lint": dict(s="pass", p=100, t="24 s", sm="typ. ~25 s", r=R1, leg="Lint 24 s"),
            "web":  dict(s="pass", p=100, t="2:03 Min", sm="typ. ~2 Min", r=R1, leg="Web 2:03"),
            "py":   dict(s="run",  p=58,  t="11:04 / ~19:00", sm="ETA ~8 Min · 14:36", r=R1, leg="Pytest 11/~19 Min"),
            "e2e":  dict(s="queued", p=0, t="—", sm="startet nach Pytest", r=None, leg="E2E · ausstehend"),
          }),
 28: dict(title="docs: import omnigent specs", branch="cursor/import-omnigent-specs-a91c", sha="3fd09b2",
          started="gestartet 14:15", status="run", eta="~11 Min", eta_l="fertig ca. 14:39", run_no="#411",
          summary="<b>2/3</b> Pflicht-Checks grün · Pytest läuft <b>8:02</b> von typ. ~19 Min",
          checks={
            "lint": dict(s="pass", p=100, t="22 s", sm="typ. ~25 s", r=R1, leg="Lint 22 s"),
            "web":  dict(s="pass", p=100, t="1:57 Min", sm="typ. ~2 Min", r=R2, leg="Web 1:57"),
            "py":   dict(s="run",  p=42,  t="8:02 / ~19:00", sm="ETA ~11 Min · 14:39", r=R2, leg="Pytest 8/~19 Min"),
            "e2e":  dict(s="queued", p=0, t="—", sm="startet nach Pytest", r=None, leg="E2E · ausstehend"),
          }),
 29: dict(title="CI merge fixes", branch="cursor/ci-merge-fixes-7d20", sha="b41c7e9",
          started="eingereiht 14:24", status="queued", eta="~8 Min", eta_l="bis Start · ca. 14:36", run_no="#413",
          summary="Wartet auf freien Runner · Position <b>1–2</b> in der Warteschlange",
          checks={
            "lint": dict(s="queued", p=0, t="—", sm="Warteschlange #1", r=None, leg="Lint · wartet"),
            "web":  dict(s="queued", p=0, t="—", sm="Warteschlange #2", r=None, leg="Web · wartet"),
            "py":   dict(s="queued", p=0, t="—", sm="nach Lint + Web", r=None, leg="Pytest · ausstehend"),
            "e2e":  dict(s="queued", p=0, t="—", sm="nach Pytest", r=None, leg="E2E · ausstehend"),
          }),
 34: dict(title="Rebind an idle session's workspace and host", branch="cursor/rebind-idle-session-0c3e", sha="9a6f15d",
          started="beendet 14:12", status="soft", eta="23:24", eta_l="Gesamtdauer", run_no="#409",
          summary="<b>3/3</b> Pflicht-Checks grün · E2E rot, <b>blockiert nicht</b>",
          checks={
            "lint": dict(s="pass", p=100, t="21 s", sm="typ. ~25 s", r=R2, leg="Lint 21 s"),
            "web":  dict(s="pass", p=100, t="1:49 Min", sm="typ. ~2 Min", r=R2, leg="Web 1:49"),
            "py":   dict(s="pass", p=100, t="18:42 Min", sm="typ. ~19 Min", r=R1, leg="Pytest 18:42"),
            "e2e":  dict(s="soft", p=100, t="4:12 Min", sm="3 von 41 Specs rot", r=R1, leg="E2E rot · 4:12"),
          }),
}
# Zukunftsszenario Team-Chat (14:46): #29 Pytest rot, #30 E2E läuft live
PRS_FUTURE = {
 29: dict(PRS[29], started="gestartet 14:36", status="fail", eta="rot", eta_l="nach 9:12 Min",
          summary="<b>Pytest (SQLite)</b> fehlgeschlagen · 2 Tests rot · E2E übersprungen",
          checks={
            "lint": dict(s="pass", p=100, t="23 s", sm="typ. ~25 s", r=R2, leg="Lint 23 s"),
            "web":  dict(s="pass", p=100, t="2:01 Min", sm="typ. ~2 Min", r=R1, leg="Web 2:01"),
            "py":   dict(s="fail", p=100, t="6:12 Min", sm="2 Tests fehlgeschlagen", r=R1, leg="Pytest rot · 6:12"),
            "e2e":  dict(s="skip", p=0, t="—", sm="übersprungen", r=None, leg="E2E · übersprungen"),
          }),
 30: dict(PRS[30], status="run", eta="~4 Min", eta_l="E2E fertig ca. 14:50",
          summary="<b>3/3</b> Pflicht-Checks grün · E2E läuft <b>1:42</b> von typ. ~6 Min",
          checks={
            "lint": dict(s="pass", p=100, t="24 s", sm="typ. ~25 s", r=R1, leg="Lint 24 s"),
            "web":  dict(s="pass", p=100, t="2:03 Min", sm="typ. ~2 Min", r=R1, leg="Web 2:03"),
            "py":   dict(s="pass", p=100, t="19:12 Min", sm="typ. ~19 Min", r=R1, leg="Pytest 19:12"),
            "e2e":  dict(s="run",  p=28,  t="1:42 / ~6:00", sm="ETA ~4 Min · 14:50", r=R2, leg="E2E 1:42/~6 Min"),
          }),
}

def status_icon(s, size=""):
    cls = {"pass":"pass","run":"run","queued":"queued","fail":"fail","soft":"soft","skip":"skip"}[s]
    return f'<span class="st {size} {cls}" aria-hidden="true"></span>'

def overall_pill(pr):
    s = pr["status"]
    if s == "run":    return '<span class="pill sm run"><span class="st sm run"></span>Läuft</span>'
    if s == "queued": return '<span class="pill sm queued"><span class="st sm queued"></span>Wartet</span>'
    if s == "fail":   return '<span class="pill sm fail"><span class="st sm fail"></span>Pflicht rot</span>'
    if s == "pass":   return '<span class="pill sm pass"><span class="st sm pass"></span>Alle grün</span>'
    if s == "soft":   return ('<span class="pill sm pass"><span class="st sm pass"></span>Pflicht grün</span>')
    return ""

def seg(pr, cls="thick"):
    out = []
    for k, *_ in CHECKS:
        c = pr["checks"][k]; s = c["s"]
        s2 = "queued" if s == "skip" else s
        style = f' style="--p:{c["p"]}%"' if s == "run" else ""
        out.append(f'<i class="{s2}"{style}></i>')
    return f'<div class="seg {cls}">{"".join(out)}</div>'

def chips(pr):
    out = []
    for k, *_ in CHECKS:
        s = pr["checks"][k]["s"]; s = "queued" if s == "skip" else s
        out.append(f'<span class="chip {s}">{CHIP[k]}</span>')
    return f'<span class="chips">{"".join(out)}</span>'

def check_row(key, c, name, req):
    s = c["s"]
    rowcls = {"run":"running","fail":"failed"}.get(s, "")
    tag = '<span class="tag req">Pflicht</span>' if req else '<span class="tag nb">nicht blockierend</span>'
    runner = f'<span class="mono subtle ellipsis" style="font-size:11px">{c["r"]}</span>' if c["r"] else f'<span class="subtle">{c["sm"]}</span>'
    barcls = {"run":"run","fail":"fail","soft":"soft"}.get(s, "")
    bar = f'<div class="bar {barcls}"><b style="--p:{c["p"]}%"></b></div>' if s not in ("queued","skip") else '<div class="bar" style="background:repeating-linear-gradient(90deg,var(--bg-4) 0 4px,transparent 4px 7px)"></div>'
    small = c["sm"] if c["r"] else ("typ. ~6 Min" if key=="e2e" else "typ. ~19 Min" if key=="py" else "typ. ~2 Min" if key=="web" else "typ. ~25 s")
    log = '<a class="link log">Log</a>' if s not in ("queued","skip") else '<span class="faint" style="font-size:12px">Log</span>'
    tcolor = ' style="color:var(--tx)"' if s=="run" else (' style="color:#ff9a9d"' if s=="fail" else "")
    return f'''<div class="ci-check {rowcls}">{status_icon(s)}
      <div style="min-width:0"><div class="name ellipsis">{name}</div><div class="sub">{tag}{runner}</div></div>
      {bar}<div class="time"><span{tcolor}>{c["t"]}</span><small>{small}</small></div>{log}</div>'''

def card(pr, num, mode="collapsed", live=False, live_txt="aktualisiert vor 4 s", share=True, foot_extra=""):
    s = pr["status"]
    color = STATUS_COLOR.get(s, "var(--tx-3)")
    legend = "".join(f'<span class="{"on" if pr["checks"][k]["s"]=="run" else ""}">{pr["checks"][k]["leg"]}</span>' for k,*_ in CHECKS)
    livetag = f'<span class="live-tag"><span class="pulse {"ok" if s!="run" else ""}" style="width:6px;height:6px"></span>Live · {live_txt}</span>' if live else ""
    soft = '<span class="pill sm soft"><span class="st sm soft"></span>E2E rot · nicht blockierend</span>' if s=="soft" else ""
    eta_color = ' style="color:#ff9a9d"' if s=="fail" else ""
    toggle = '<span class="toggle">Details <span class="chev"></span></span>' if mode=="collapsed" else '<span class="toggle">Weniger <span class="chev up"></span></span>'
    html = f'''<div class="ci-card {"is-fail" if s=="fail" else ""} {"is-live" if live else ""}">
  <div class="ci-head"><span style="color:{color};display:flex;margin-top:0">{PR_ICON}</span>
    <div class="grow"><div class="t ellipsis">{pr["title"]}<span class="num">#{num}</span></div>
      <div class="meta"><span class="mono ellipsis" style="max-width:290px">{pr["branch"]}</span><span class="dotsep"></span><span class="mono">{pr["sha"]}</span><span class="dotsep"></span><span>{pr["started"]}</span></div></div>
    <div class="ci-eta"><span class="v tnum"{eta_color}>{pr["eta"]}</span><span class="l">{pr["eta_l"]}</span></div>
  </div>
  <div class="ci-progress">{seg(pr)}<div class="ci-legend">{legend}</div></div>
  <div class="ci-summary">{overall_pill(pr)}{soft}<span class="ellipsis">{pr["summary"]}</span><span class="sp row gap-12">{livetag}{toggle}</span></div>'''
    if mode == "expanded":
        rows = "".join(check_row(k, pr["checks"][k], n, req) for k, n, _, req in CHECKS)
        share_btn = '<span class="btn ghost sm">In Team-Chat teilen</span>' if share else ""
        html += f'''<div class="ci-checks">{rows}</div>
  <div class="ci-foot"><span>Workflow <span class="mono" style="color:var(--tx-2)">ci.yml</span></span><span class="dotsep"></span><span>Lauf {pr["run_no"]}</span>{foot_extra}
    <span class="sp row gap-4">{share_btn}<span class="btn sm">Auf GitHub <span style="font-size:11px">↗</span></span></span></div>'''
    html += "</div>"
    return html

def head(title):
    return f'''<!doctype html><html lang="de"><head><meta charset="utf-8"><title>{title} · Omnigent CI-Status</title>
<meta name="viewport" content="width=1440"><link rel="stylesheet" href="ci.css"></head><body>'''

DEMO = '<div class="demo-flag">Beispieldaten</div>'
TAIL = DEMO + "</body></html>"
TAIL_LEFT = '<div class="demo-flag left">Beispieldaten</div></body></html>'

def sidebar(ci_open=False, team=False, active_sess=0):
    if team:
        groups = '''<div class="sb-group"><span class="section-label">Team-Chat</span>
        <div class="sb-sess active"><span class="faint">#</span><span class="ellipsis">ci-omnigent</span><span class="pill xs fail" style="padding:0 6px">1</span></div>
        <div class="sb-sess"><span class="faint">#</span><span class="ellipsis">allgemein</span></div>
        <div class="sb-sess"><span class="faint">#</span><span class="ellipsis">releases</span></div></div>
        <div class="sb-group"><span class="section-label">Agenten</span>
        <div class="sb-sess"><span class="avatar sm av-pm">PM</span><span class="ellipsis">PM HUB</span><span class="dotb pass" style="width:6px;height:6px"></span></div>
        <div class="sb-sess"><span class="avatar sm av-coder">OC</span><span class="ellipsis">omnigent-coder</span><span class="dotb pass" style="width:6px;height:6px"></span></div>
        <div class="sb-sess"><span class="avatar sm" style="background:#2b3b52">RV</span><span class="ellipsis">omnigent-reviewer</span><span class="dotb" style="width:6px;height:6px;background:var(--tx-4)"></span></div></div>'''
    else:
        sess = [("remove session human chat","run"),("docs: import omnigent specs","run"),("CI merge fixes","queued"),
                ("Rebind an idle session's workspace…","soft"),("pyrefly config cleanup",None),("host heartbeat backoff","pass")]
        items = ""
        for i,(n,s) in enumerate(sess):
            b = f'<span class="dotb {s}" style="width:8px;height:8px"></span>' if s else '<span style="width:8px"></span>'
            items += f'<div class="sb-sess {"active" if i==active_sess else ""}"><span class="ellipsis">{n}</span>{b}</div>'
        groups = f'<div class="sb-group"><span class="section-label">Aktive Sessions</span>{items}</div>'
    return f'''<aside class="sb">
  <div class="sb-ws"><span class="sb-logo"></span><b>Omnigent</b><span class="sp faint" style="font-size:11px">⌄</span></div>
  <div class="sb-search">Suchen …<span class="kbd">⌘K</span></div>
  <div class="sb-item">Inbox<span class="cnt">3</span></div>
  <div class="sb-item {"" if team else "active"}">Sessions<span class="cnt">12</span></div>
  <div class="sb-item {"active" if team else ""}">Team-Chat</div>
  <div class="sb-item">Agenten</div>
  <div class="sb-item">Hosts</div>
  {groups}
  <div class="sb-bottom">
    <div class="sb-ci {"open" if ci_open else ""}"><span class="pulse"></span><span class="n">CI <b>3</b> aktiv</span><span class="s">2 Runner · 2 wartend</span></div>
    <div class="sb-user"><span class="avatar sm av-user">JH</span>Jens Holze</div>
  </div></aside>'''
