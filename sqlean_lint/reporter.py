"""Multi-format reporting: Rich terminal, air-gapped HTML, JSON, Markdown.

The HTML artifact is a *single self-contained file*: inline CSS, vanilla JS,
inline SVG icons, zero external requests, zero fonts fetched, zero telemetry.
Terminal output is sanitized to CP1252 so Windows consoles never crash.
"""
from __future__ import annotations

import io
import json
from typing import Any, Dict, List, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ._version import __version__
from .types import LintResult, RiskLevel, Severity, severity_rank

SEVERITY_STYLES = {
    Severity.CRITICAL: "bold white on red",
    Severity.HIGH: "bold red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "cyan",
}

_SEVERITY_VALUES = [s.value for s in Severity]


# --------------------------------------------------------------------------
# shared summary
# --------------------------------------------------------------------------

def build_summary(results: Sequence[LintResult]) -> Dict[str, Any]:
    by_severity = {value: 0 for value in _SEVERITY_VALUES}
    total = 0
    max_risk = 0
    max_level = RiskLevel.LOW.value
    for result in results:
        for violation in result.violations:
            total += 1
            key = violation.severity.value if isinstance(violation.severity, Severity) else str(violation.severity)
            by_severity[key] = by_severity.get(key, 0) + 1
        if result.cost.risk_score > max_risk:
            max_risk = result.cost.risk_score
            max_level = (
                result.cost.risk_level.value
                if isinstance(result.cost.risk_level, RiskLevel)
                else str(result.cost.risk_level)
            )
    return {
        "files": len(results),
        "total_violations": total,
        "by_severity": by_severity,
        "max_risk_score": max_risk,
        "max_risk_level": max_level,
        "has_critical": by_severity.get("CRITICAL", 0) > 0,
    }


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------

def to_json(results: Sequence[LintResult], indent: int = 2) -> str:
    """CI/CD-friendly structured report (deterministic ordering, no timings)."""
    payload = {
        "tool": "sqlean-lint",
        "version": __version__,
        "summary": build_summary(results),
        "results": [
            result.to_dict(include_sql=True, include_duration=False)
            for result in results
        ],
    }
    return json.dumps(payload, indent=indent)


# --------------------------------------------------------------------------
# Markdown (GitHub-flavored)
# --------------------------------------------------------------------------

def _md_cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def to_markdown(results: Sequence[LintResult]) -> str:
    summary = build_summary(results)
    lines: List[str] = [
        "# SQLean-Lint Report",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Files scanned | {summary['files']} |",
        f"| Total violations | {summary['total_violations']} |",
        f"| Max risk score | {summary['max_risk_score']} / 100 ({summary['max_risk_level']}) |",
        f"| Critical violations | {summary['by_severity'].get('CRITICAL', 0)} |",
        "",
    ]
    if summary["total_violations"] == 0:
        lines.append("_No violations detected._")
        lines.append("")

    for result in results:
        lines.append(f"### `{_md_cell(result.file_path)}`")
        lines.append("")
        lines.append(
            f"**Risk:** {result.cost.risk_score}/100 "
            f"({result.cost.risk_level.value if isinstance(result.cost.risk_level, RiskLevel) else result.cost.risk_level})"
            f" - complexity `{result.cost.scan_complexity}`"
        )
        lines.append("")
        if result.violations:
            lines.extend([
                "| Rule | Severity | Location | Message |",
                "| --- | --- | --- | --- |",
            ])
            for violation in result.violations:
                location = f"{violation.line}:{violation.col}"
                sev = violation.severity.value if isinstance(violation.severity, Severity) else str(violation.severity)
                lines.append(
                    f"| `{_md_cell(violation.rule_id)}` | {sev} | {location} "
                    f"| {_md_cell(violation.message)} |"
                )
            lines.append("")
        else:
            lines.append("_Clean._")
            lines.append("")
        if result.optimized_sql:
            lines.extend([
                "#### Auto-Optimized SQL",
                "",
                "```sql",
                result.optimized_sql,
                "```",
                "",
            ])
        lines.extend(["<details><summary>Original SQL</summary>", "", "```sql", result.raw_sql, "```", "</details>", ""])
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------
# Terminal (Rich + CP1252 fallback)
# --------------------------------------------------------------------------

def to_terminal(results: Sequence[LintResult]) -> str:
    """Render a Rich report and guarantee CP1252/ASCII encodability."""
    console = Console(
        file=io.StringIO(),
        width=110,
        no_color=True,
        highlight=False,
        emoji=False,
        legacy_windows=False,
        record=True,
    )
    summary = build_summary(results)
    console.print(Panel(
        Text(
            f"sqlean-lint v{__version__}  |  100% local analysis  |  "
            f"files={summary['files']}  violations={summary['total_violations']}  "
            f"critical={summary['by_severity'].get('CRITICAL', 0)}  "
            f"max risk={summary['max_risk_score']}/100",
            justify="center",
        ),
        title="SQLean-Lint Semantic SQL Quality Report",
        border_style="cyan",
    ))
    for result in results:
        table = Table(box=None, pad_edge=False, expand=True)
        table.add_column("Rule", style="bold", width=14)
        table.add_column("Sev", width=10)
        table.add_column("Loc", width=9)
        table.add_column("Finding")
        for violation in result.violations:
            sev_value = (
                violation.severity.value
                if isinstance(violation.severity, Severity)
                else str(violation.severity)
            )
            table.add_row(
                violation.rule_id,
                Text(sev_value, style=SEVERITY_STYLES.get(violation.severity, "white")),
                f"{violation.line}:{violation.col}",
                violation.snippet or violation.title,
            )
        level = (
            result.cost.risk_level.value
            if isinstance(result.cost.risk_level, RiskLevel)
            else str(result.cost.risk_level)
        )
        header = (
            f"[bold]{result.file_path}[/bold]   "
            f"risk {result.cost.risk_score}/100 ({level})   "
            f"complexity {result.cost.scan_complexity}   "
            f"{result.duration_ms:.1f} ms"
        )
        if result.violations:
            console.print(header)
            console.print(table)
            if result.optimized_sql:
                console.print("[green]Auto-optimized SQL available (--fix applies it).[/green]")
        else:
            console.print(f"{header}   [green]CLEAN[/green]")
        explanation = result.cost.explanation
        if explanation and result.violations:
            console.print(f"[dim]{explanation}[/dim]")
        console.print()

    text = console.export_text(clear=True, styles=False)
    # Windows CP1252 consoles: hard-guarantee encodability.
    return text.encode("cp1252", errors="replace").decode("cp1252")


# --------------------------------------------------------------------------
# Air-gapped single-file HTML dashboard
# --------------------------------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SQLean-Lint Report</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--border:#30363d;--fg:#e6edf3;--dim:#8b949e;
--crit:#f85149;--high:#ff7b72;--med:#d29922;--low:#58a6ff;--ok:#3fb950;--accent:#bc8cff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font-family:'Segoe UI',system-ui,-apple-system,Arial,sans-serif;padding:24px;line-height:1.5}
a{color:var(--accent)}
header{display:flex;align-items:center;gap:14px;margin-bottom:20px}
header h1{font-size:22px;font-weight:600}
header .ver{color:var(--dim);font-size:12px}
.badge{margin-left:auto;display:flex;gap:8px}
button{background:var(--panel);color:var(--fg);border:1px solid var(--border);border-radius:6px;
padding:6px 12px;cursor:pointer;font-size:13px}
button:hover{border-color:var(--accent)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:20px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px;
display:flex;align-items:center;gap:12px}
.card .num{font-size:26px;font-weight:700}
.card .lbl{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
.card svg{flex-shrink:0}
.toolbar{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.toolbar button.on{border-color:var(--accent);color:var(--accent)}
details.file{background:var(--panel);border:1px solid var(--border);border-radius:10px;margin-bottom:14px;overflow:hidden}
details.file>summary{cursor:pointer;padding:14px 18px;display:flex;align-items:center;gap:10px;list-style:none}
details.file>summary::-webkit-details-marker{display:none}
.path{font-weight:600;font-size:14px;word-break:break-all}
.chip{font-size:11px;font-weight:700;border-radius:999px;padding:2px 10px;letter-spacing:.04em}
.chip.crit{background:var(--crit);color:#fff}.chip.high{background:var(--high);color:#0d1117}
.chip.med{background:var(--med);color:#0d1117}.chip.low{background:var(--low);color:#0d1117}
.chip.ok{background:var(--ok);color:#0d1117}
.body{padding:0 18px 18px}
.meta{color:var(--dim);font-size:12px;margin-bottom:10px}
table{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:12px}
th{text-align:left;color:var(--dim);font-weight:600;border-bottom:1px solid var(--border);padding:6px 8px}
td{padding:6px 8px;border-bottom:1px solid rgba(48,54,61,.5);vertical-align:top}
tr[data-sev="CRITICAL"] td:first-child{box-shadow:inset 3px 0 var(--crit)}
tr[data-sev="HIGH"] td:first-child{box-shadow:inset 3px 0 var(--high)}
tr[data-sev="MEDIUM"] td:first-child{box-shadow:inset 3px 0 var(--med)}
tr[data-sev="LOW"] td:first-child{box-shadow:inset 3px 0 var(--low)}
code,.mono{font-family:Consolas,'Cascadia Mono',Menlo,monospace;font-size:12px}
.diffgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}
@media(max-width:900px){.diffgrid{grid-template-columns:1fr}}
.pane{background:#010409;border:1px solid var(--border);border-radius:8px;padding:10px;min-width:0}
.pane h4{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;
display:flex;justify-content:space-between;align-items:center}
.pane pre{white-space:pre-wrap;word-break:break-word;color:#adbac7;font-size:12px}
footer{margin-top:28px;color:var(--dim);font-size:12px;text-align:center}
</style>
</head>
<body>
<header>
<svg width="34" height="34" viewBox="0 0 24 24" fill="none" aria-hidden="true">
<path d="M12 2L4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3z" fill="#238636"/>
<path d="M9 12l2 2 4-4" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
<div>
<h1>SQLean-Lint Semantic SQL Quality Dashboard</h1>
<span class="ver">version __VERSION__ &middot; air-gapped artifact &middot; rendered fully offline</span>
</div>
<div class="badge"><button onclick="toggleTheme()" type="button">Toggle theme</button></div>
</header>

<section class="cards">
<div class="card">
<svg width="30" height="30" viewBox="0 0 24 24" fill="none" aria-hidden="true"><ellipse cx="12" cy="5" rx="8" ry="3" stroke="#58a6ff" stroke-width="1.6"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5" stroke="#58a6ff" stroke-width="1.6"/><path d="M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7" stroke="#58a6ff" stroke-width="1.6"/></svg>
<div><div class="num" id="m-files">-</div><div class="lbl">Files scanned</div></div>
</div>
<div class="card">
<svg width="30" height="30" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="9" stroke="#d29922" stroke-width="1.6"/><path d="M12 7v5l3 3" stroke="#d29922" stroke-width="1.6" stroke-linecap="round"/></svg>
<div><div class="num" id="m-violations">-</div><div class="lbl">Violations</div></div>
</div>
<div class="card">
<svg width="30" height="30" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 14a8 8 0 1 1 16 0" stroke="#3fb950" stroke-width="1.6"/><path d="M12 14L16 9" stroke="#3fb950" stroke-width="1.6" stroke-linecap="round"/><circle cx="12" cy="14" r="1.6" fill="#3fb950"/></svg>
<div><div class="num" id="m-risk">-</div><div class="lbl">Max risk /100</div></div>
</div>
<div class="card">
<svg width="30" height="30" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3L2 21h20L12 3z" stroke="#f85149" stroke-width="1.6" stroke-linejoin="round"/><path d="M12 10v5" stroke="#f85149" stroke-width="1.6" stroke-linecap="round"/><circle cx="12" cy="17.6" r=".9" fill="#f85149"/></svg>
<div><div class="num" id="m-critical">-</div><div class="lbl">Critical</div></div>
</div>
</section>

<div class="toolbar" id="toolbar">
<button type="button" class="on" onclick="filterSev('ALL',this)">All</button>
<button type="button" onclick="filterSev('CRITICAL',this)">Critical</button>
<button type="button" onclick="filterSev('HIGH',this)">High</button>
<button type="button" onclick="filterSev('MEDIUM',this)">Medium</button>
<button type="button" onclick="filterSev('LOW',this)">Low</button>
<button type="button" onclick="expandAll(true)">Expand all</button>
<button type="button" onclick="expandAll(false)">Collapse all</button>
</div>

<main id="app"></main>

<footer>This file is fully self-contained: inline CSS, vanilla JS and inline SVG only.
No network requests, no telemetry, no external fonts. Generated locally by sqlean-lint.</footer>

<script>
var DATA = __PAYLOAD__;
function el(tag, cls, text){var n=document.createElement(tag);if(cls){n.className=cls;}
if(text!==undefined&&text!==null){n.textContent=text;}return n;}
function chip(label,cls){return el('span','chip '+cls,label);}
function sevClass(sev){return {CRITICAL:'crit',HIGH:'high',MEDIUM:'med',LOW:'low'}[sev]||'low';}
document.getElementById('m-files').textContent=DATA.summary.files;
document.getElementById('m-violations').textContent=DATA.summary.total_violations;
document.getElementById('m-risk').textContent=DATA.summary.max_risk_score;
document.getElementById('m-critical').textContent=DATA.summary.by_severity.CRITICAL||0;
function render(){
 var app=document.getElementById('app');app.innerHTML='';
 DATA.results.forEach(function(res,idx){
  var det=el('details','file');det.setAttribute('data-idx',idx);
  if(res.violations.length>0){det.setAttribute('open','open');}
  var sum=el('summary');
  sum.appendChild(el('span','path',res.file_path));
  if(res.violations.length===0){sum.appendChild(chip('CLEAN','ok'));}
  res.violations.slice(0,4).forEach(function(v){
   sum.appendChild(chip(v.severity,sevClass(v.severity)));
  });
  if(res.violations.length>4){
   sum.appendChild(chip('+'+(res.violations.length-4),'low'));
  }
  det.appendChild(sum);
  var body=el('div','body');
  body.appendChild(el('div','meta','Risk '+res.cost.risk_score+'/100 ('+res.cost.risk_level+
    ') &middot; complexity '+res.cost.scan_complexity+' &middot; '+res.duration_ms+' ms'));
  if(res.violations.length>0){
   var tbl=el('table');var thead=el('thead');var hr=el('tr');
   ['Rule','Severity','Location','Message','Fix'].forEach(function(h){var th=el('th',null,h);hr.appendChild(th);});
   thead.appendChild(hr);tbl.appendChild(thead);
   var tb=el('tbody');
   res.violations.forEach(function(v){
    var tr=el('tr');tr.setAttribute('data-sev',v.severity);
    tr.appendChild(el('td','mono',v.rule_id));
    tr.appendChild(chip(v.severity,sevClass(v.severity)));
    tr.appendChild(el('td','mono',v.line+':'+v.col));
    var msgTd=el('td',null,v.message);tr.appendChild(msgTd);
    tr.appendChild(el('td',null,v.suggested_fix||''));
    tb.appendChild(tr);
   });
   tbl.appendChild(tb);body.appendChild(tbl);
  }else{
   body.appendChild(el('p',null,'No violations detected.'));
  }
  var grid=el('div','diffgrid');
  var p1=el('div','pane');var h1=el('h4',null,'Original SQL');p1.appendChild(h1);
  p1.appendChild(el('pre','mono',res.raw_sql));
  var p2=el('div','pane');var h2=el('h4',null,'Auto-Optimized SQL');
  var cp=el('button',null,'Copy');cp.type='button';
  cp.onclick=function(){navigator.clipboard&&navigator.clipboard.writeText(p2pre());};
  function p2pre(){return res.optimized_sql||res.raw_sql;}
  h2.appendChild(cp);p2.appendChild(h2);
  p2.appendChild(el('pre','mono',res.optimized_sql||'(no safe rewrite available)'));
  grid.appendChild(p1);grid.appendChild(p2);body.appendChild(grid);
  det.appendChild(body);app.appendChild(det);
 });
}
function filterSev(sev,btn){
 var btns=document.querySelectorAll('#toolbar button');
 for(var i=0;i<3;i++){} /* noop keeps loop simple */
 btns.forEach(function(b){b.classList.remove('on');});
 btn.classList.add('on');
 document.querySelectorAll('details.file').forEach(function(det){
  var idx=det.getAttribute('data-idx');var res=DATA.results[idx];
  var has=res.violations.some(function(v){return sev==='ALL'||v.severity===sev;});
  var clean=sev==='ALL'||((sev==='CRITICAL')&&false);
  det.style.display=(has||(res.violations.length===0&&sev==='ALL'))?'':'none';
 });
}
function expandAll(open){document.querySelectorAll('details.file').forEach(function(d){
 if(open){d.setAttribute('open','open');}else{d.removeAttribute('open');}});}
function toggleTheme(){
 var r=document.documentElement;
 if(r.getAttribute('data-theme')==='light'){r.removeAttribute('data-theme');}
 else{r.setAttribute('data-theme','light');
  r.style.setProperty('--bg','#f6f8fa');r.style.setProperty('--panel','#ffffff');
  r.style.setProperty('--border','#d0d7de');r.style.setProperty('--fg','#1f2328');
  r.style.setProperty('--dim','#656d76');}
}
render();
</script>
</body>
</html>
"""


def to_html(results: Sequence[LintResult]) -> str:
    """Single-file, offline HTML dashboard (inline CSS/JS/SVG, zero requests)."""
    payload = {
        "tool": "sqlean-lint",
        "version": __version__,
        "summary": build_summary(results),
        "results": [result.to_dict(include_sql=True) for result in results],
    }
    # Prevent </script> breakouts inside embedded JSON payloads.
    payload_json = json.dumps(payload).replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("__PAYLOAD__", payload_json).replace("__VERSION__", __version__)
