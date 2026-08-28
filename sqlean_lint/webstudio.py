"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Self-contained HTML SPA for the Web Studio.

``STUDIO_HTML`` is a complete, air-gapped HTML document with inline CSS
and vanilla JavaScript.  It makes zero external network requests -- no
CDNs, no fonts, no analytics.  The UI provides a three-column layout:
Workspace editor, DAG + Cost HUD, and Fix & Verify panel.
"""
from __future__ import annotations

STUDIO_HTML: str = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sqlean-lint Web Studio</title>
<style>
:root {
  --bg: #0d1117; --panel: #161b22; --panel2: #010409;
  --border: #30363d; --fg: #e6edf3; --dim: #8b949e;
  --accent: #58a6ff; --accent2: #bc8cff;
  --ok: #3fb950; --warn: #d29922; --err: #f85149;
  --high: #ff7b72; --med: #d29922; --low: #58a6ff;
  --crit: #f85149;
  --radius: 8px; --mono: 'Cascadia Mono', Consolas, Menlo, monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--fg); font-family: 'Segoe UI', system-ui, sans-serif; height: 100vh; overflow: hidden; display: flex; flex-direction: column; }
a { color: var(--accent); text-decoration: none; }
button { background: var(--panel); color: var(--fg); border: 1px solid var(--border); border-radius: var(--radius); padding: 5px 14px; cursor: pointer; font-size: 13px; transition: border-color .15s; }
button:hover { border-color: var(--accent); }
button.primary { background: #238636; border-color: #238636; color: #fff; }
button.primary:hover { background: #2ea043; }
button.danger { background: var(--err); border-color: var(--err); color: #fff; }
select, input { background: var(--panel); color: var(--fg); border: 1px solid var(--border); border-radius: var(--radius); padding: 5px 10px; font-size: 13px; }
header { display: flex; align-items: center; gap: 14px; padding: 10px 18px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
header h1 { font-size: 16px; font-weight: 600; }
header .tag { font-size: 11px; color: var(--dim); background: var(--panel); padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); }
.header-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.main { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0; flex: 1; min-height: 0; }
.col { display: flex; flex-direction: column; border-right: 1px solid var(--border); overflow: hidden; }
.col:last-child { border-right: none; }
.col-header { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.col-header h2 { font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--dim); }
.col-body { flex: 1; overflow: auto; padding: 12px; }
textarea.sql-editor { width: 100%; height: 220px; background: var(--panel2); color: var(--fg); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px; font-family: var(--mono); font-size: 13px; resize: vertical; line-height: 1.5; tab-size: 2; }
textarea.sql-editor:focus { outline: none; border-color: var(--accent); }
.toolbar-row { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; align-items: center; }
.status-bar { padding: 6px 12px; border-top: 1px solid var(--border); font-size: 12px; color: var(--dim); flex-shrink: 0; display: flex; justify-content: space-between; }
.results-box { background: var(--panel2); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px; margin-top: 10px; }
.results-box h3 { font-size: 13px; color: var(--dim); margin-bottom: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { text-align: left; color: var(--dim); font-weight: 600; border-bottom: 1px solid var(--border); padding: 5px 6px; }
td { padding: 5px 6px; border-bottom: 1px solid rgba(48,54,61,.4); vertical-align: top; }
.chip { display: inline-block; font-size: 10px; font-weight: 700; border-radius: 999px; padding: 1px 7px; }
.chip.crit { background: var(--crit); color: #fff; }
.chip.high { background: var(--high); color: #0d1117; }
.chip.med { background: var(--med); color: #0d1117; }
.chip.low { background: var(--low); color: #0d1117; }
.chip.ok { background: var(--ok); color: #0d1117; }
.risk-gauge { display: flex; align-items: center; gap: 10px; margin: 10px 0; }
.risk-bar { flex: 1; height: 10px; background: var(--border); border-radius: 5px; overflow: hidden; }
.risk-fill { height: 100%; border-radius: 5px; transition: width .3s ease; }
.risk-label { font-size: 12px; color: var(--dim); white-space: nowrap; }
.dag-area { background: var(--panel2); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; min-height: 140px; font-family: var(--mono); font-size: 12px; color: var(--dim); white-space: pre-wrap; word-break: break-word; line-height: 1.5; }
.diff-pane { background: var(--panel2); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px; margin-top: 10px; }
.diff-pane pre { font-family: var(--mono); font-size: 12px; color: #adbac7; white-space: pre-wrap; word-break: break-word; line-height: 1.5; }
.diff-pane h4 { font-size: 11px; color: var(--dim); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 6px; }
.history-list { list-style: none; }
.history-list li { padding: 6px 8px; border-bottom: 1px solid rgba(48,54,61,.4); font-size: 12px; cursor: pointer; border-radius: var(--radius); }
.history-list li:hover { background: var(--panel); }
.history-list li .hl-dialect { color: var(--accent2); font-weight: 600; margin-right: 6px; }
.history-list li .hl-sql { color: var(--dim); display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px; }
.transpile-box { background: var(--panel2); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px; margin-top: 10px; }
.transpile-box pre { font-family: var(--mono); font-size: 12px; color: var(--fg); white-space: pre-wrap; word-break: break-word; min-height: 40px; }
.preset-btn { font-size: 11px; padding: 3px 8px; }
.empty-state { color: var(--dim); font-size: 13px; text-align: center; padding: 30px 10px; }
.spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin .6s linear infinite; vertical-align: middle; margin-right: 6px; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<header>
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3z" fill="#238636"/><path d="M9 12l2 2 4-4" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
  <h1>sqlean-lint Web Studio</h1>
  <span class="tag">v0.1.0</span>
  <span class="tag" id="tier-badge">community</span>
  <div class="header-actions">
    <button onclick="loadPresets()" type="button">Presets</button>
    <button onclick="toggleTheme()" type="button">Theme</button>
  </div>
</header>

<div class="main">
  <!-- Column 1: Workspace Editor -->
  <div class="col">
    <div class="col-header">
      <h2>Workspace</h2>
    </div>
    <div class="col-body">
      <div class="toolbar-row">
        <select id="dialect-select">
          <option value="duckdb">DuckDB</option>
          <option value="postgres">PostgreSQL</option>
          <option value="bigquery">BigQuery</option>
          <option value="snowflake">Snowflake</option>
          <option value="mysql">MySQL</option>
          <option value="sqlite">SQLite</option>
          <option value="tsql">T-SQL</option>
          <option value="databricks">Databricks</option>
        </select>
        <button class="primary" onclick="runLint()" type="button" id="lint-btn">Lint</button>
        <button onclick="optimizeQuery()" type="button">Optimize</button>
        <button onclick="applyFix()" type="button">Apply Fix</button>
        <button class="danger" onclick="clearAll()" type="button">Clear</button>
      </div>
      <textarea class="sql-editor" id="sql-input" spellcheck="false" placeholder="Enter SQL here...">SELECT *
FROM orders o, customers c
WHERE YEAR(o.order_date) = 2026;</textarea>
      <div id="results-area"></div>
    </div>
  </div>

  <!-- Column 2: DAG + Cost HUD -->
  <div class="col">
    <div class="col-header">
      <h2>DAG &amp; Cost HUD</h2>
      <button onclick="runLint()" type="button" class="preset-btn">Refresh</button>
    </div>
    <div class="col-body">
      <div class="risk-gauge" id="risk-gauge" style="display:none;">
        <span class="risk-label" id="risk-score-label">0/100</span>
        <div class="risk-bar"><div class="risk-fill" id="risk-fill" style="width:0;background:var(--ok);"></div></div>
        <span class="risk-label" id="risk-level-label">LOW</span>
      </div>
      <div class="dag-area" id="dag-area">
        <div class="empty-state">Run Lint to generate DAG and cost analysis.</div>
      </div>
      <div class="diff-pane" id="original-pane" style="display:none;">
        <h4>Original SQL</h4>
        <pre id="original-sql"></pre>
      </div>
      <div class="diff-pane" id="optimized-pane" style="display:none;">
        <h4>Auto-Optimized SQL</h4>
        <pre id="optimized-sql"></pre>
      </div>
    </div>
  </div>

  <!-- Column 3: Fix & Verify / Transpiler / History -->
  <div class="col">
    <div class="col-header">
      <h2>Fix &amp; Verify</h2>
    </div>
    <div class="col-body">
      <div class="toolbar-row">
        <select id="target-dialect-select">
          <option value="bigquery">BigQuery</option>
          <option value="snowflake">Snowflake</option>
          <option value="postgres">PostgreSQL</option>
          <option value="duckdb">DuckDB</option>
          <option value="mysql">MySQL</option>
          <option value="sqlite">SQLite</option>
          <option value="tsql">T-SQL</option>
          <option value="databricks">Databricks</option>
        </select>
        <button onclick="transpileQuery()" type="button">Transpile</button>
      </div>
      <div class="transpile-box" id="transpile-box" style="display:none;">
        <h4 style="font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Transpiled SQL</h4>
        <pre id="transpiled-sql"></pre>
      </div>

      <h3 style="font-size:13px;color:var(--dim);margin-top:18px;margin-bottom:8px;text-transform:uppercase;letter-spacing:.04em;">History</h3>
      <div id="history-container">
        <div class="empty-state">No lint sessions yet.</div>
      </div>
    </div>
  </div>
</div>

<div class="status-bar">
  <span id="status-text">Ready</span>
  <span id="perf-text"></span>
</div>

<script>
var API = '';
var _lastResult = null;
var _history = [];

function $(id) { return document.getElementById(id); }

function setStatus(msg, perf) {
  $('status-text').textContent = msg;
  $('perf-text').textContent = perf || '';
}

function sevChip(sev) {
  var cls = {CRITICAL:'crit',HIGH:'high',MEDIUM:'med',LOW:'low'}[sev] || 'low';
  return '<span class="chip ' + cls + '">' + sev + '</span>';
}

function riskColor(score) {
  if (score >= 75) return 'var(--crit)';
  if (score >= 50) return 'var(--high)';
  if (score >= 25) return 'var(--med)';
  return 'var(--ok)';
}

async function api(method, path, body) {
  var opts = { method: method, headers: {'Content-Type': 'application/json'} };
  if (body !== undefined) opts.body = JSON.stringify(body);
  var resp = await fetch(API + path, opts);
  return resp.json();
}

async function runLint() {
  var sql = $('sql-input').value.trim();
  if (!sql) { setStatus('No SQL to lint.'); return; }
  var dialect = $('dialect-select').value;
  setStatus('<span class="spinner"></span>Linting...', '');
  $('lint-btn').disabled = true;
  try {
    var data = await api('POST', '/api/lint', {sql: sql, dialect: dialect});
    _lastResult = data;
    renderResults(data);
    renderRisk(data.cost);
    renderDAG(data);
    renderHistory();
    setStatus('Lint complete.', data.duration_ms ? data.duration_ms.toFixed(1) + ' ms' : '');
  } catch (e) {
    setStatus('Error: ' + e.message);
  } finally {
    $('lint-btn').disabled = false;
  }
}

function renderResults(data) {
  var area = $('results-area');
  if (!data.violations || data.violations.length === 0) {
    area.innerHTML = '<div class="results-box"><h3>Result</h3><p style="color:var(--ok);font-size:13px;">No violations detected.</p></div>';
    return;
  }
  var html = '<div class="results-box"><h3>' + data.violations.length + ' Violation(s)</h3><table><thead><tr><th>Rule</th><th>Sev</th><th>Loc</th><th>Message</th></tr></thead><tbody>';
  data.violations.forEach(function(v) {
    html += '<tr><td style="font-family:var(--mono);font-size:11px;">' + v.rule_id + '</td><td>' + sevChip(v.severity) + '</td><td style="font-family:var(--mono);">' + v.line + ':' + v.col + '</td><td>' + (v.message || v.title) + '</td></tr>';
  });
  html += '</tbody></table></div>';
  area.innerHTML = html;
}

function renderRisk(cost) {
  if (!cost) { $('risk-gauge').style.display = 'none'; return; }
  $('risk-gauge').style.display = 'flex';
  var score = cost.risk_score || 0;
  $('risk-score-label').textContent = score + '/100';
  $('risk-level-label').textContent = cost.risk_level || 'LOW';
  var fill = $('risk-fill');
  fill.style.width = Math.max(score, 2) + '%';
  fill.style.background = riskColor(score);
}

function renderDAG(data) {
  var dagEl = $('dag-area');
  var parts = [];
  if (data.cost && data.cost.explanation) {
    parts.push('Cost Analysis:\n' + data.cost.explanation);
  }
  parts.push('Scan complexity: ' + (data.cost ? data.cost.scan_complexity : 'N/A'));
  parts.push('Join risk: ' + (data.cost ? data.cost.join_risk : 0));
  parts.push('Sort risk: ' + (data.cost ? data.cost.sort_risk : 0));
  if (data.optimized_sql) {
    parts.push('\nOptimized SQL available (see column 1 toolbar).');
  }
  dagEl.textContent = parts.join('\n');
  if (data.raw_sql) {
    $('original-pane').style.display = 'block';
    $('original-sql').textContent = data.raw_sql;
  }
  if (data.optimized_sql) {
    $('optimized-pane').style.display = 'block';
    $('optimized-sql').textContent = data.optimized_sql;
  } else {
    $('optimized-pane').style.display = 'none';
  }
}

async function optimizeQuery() {
  var sql = $('sql-input').value.trim();
  if (!sql) { return; }
  var dialect = $('dialect-select').value;
  setStatus('<span class="spinner"></span>Optimizing...', '');
  try {
    var data = await api('POST', '/api/lint', {sql: sql, dialect: dialect});
    _lastResult = data;
    renderResults(data);
    renderRisk(data.cost);
    renderDAG(data);
    setStatus('Optimize complete.', data.duration_ms ? data.duration_ms.toFixed(1) + ' ms' : '');
  } catch (e) {
    setStatus('Error: ' + e.message);
  }
}

async function applyFix() {
  if (!_lastResult) { setStatus('Run Lint first.'); return; }
  var sql = $('sql-input').value.trim();
  if (!sql) { return; }
  var dialect = $('dialect-select').value;
  setStatus('<span class="spinner"></span>Applying fix...', '');
  try {
    var data = await api('POST', '/api/apply_fix', {sql: sql, dialect: dialect});
    if (data.error) {
      setStatus('Error: ' + data.error);
      if (data.upgrade_url) setStatus(data.error + ' — ' + data.upgrade_url);
      return;
    }
    if (data.changed) {
      $('sql-input').value = data.optimized_sql;
      renderDAG(data);
      setStatus('Fix applied.');
    } else {
      setStatus('No safe rewrite available.');
    }
  } catch (e) {
    setStatus('Error: ' + e.message);
  }
}

async function transpileQuery() {
  var sql = $('sql-input').value.trim();
  if (!sql) { return; }
  var source = $('dialect-select').value;
  var target = $('target-dialect-select').value;
  if (source === target) { setStatus('Source and target dialect are the same.'); return; }
  setStatus('<span class="spinner"></span>Transpiling...', '');
  try {
    var data = await api('POST', '/api/transpile', {sql: sql, source_dialect: source, target_dialect: target});
    if (data.error) { setStatus('Error: ' + data.error); return; }
    $('transpile-box').style.display = 'block';
    $('transpiled-sql').textContent = data.transpiled_sql;
    setStatus('Transpile complete.');
  } catch (e) {
    setStatus('Error: ' + e.message);
  }
}

function renderHistory() {
  var container = $('history-container');
  if (_history.length === 0) {
    container.innerHTML = '<div class="empty-state">No lint sessions yet.</div>';
    return;
  }
  var html = '<ul class="history-list">';
  _history.forEach(function(h, i) {
    var preview = (h.sql || '').substring(0, 60).replace(/\n/g, ' ');
    html += '<li onclick="restoreHistory(' + i + ')">';
    html += '<span class="hl-dialect">' + h.dialect + '</span>';
    html += '<span style="color:var(--dim);float:right;font-size:11px;">risk ' + h.risk_score + '</span>';
    html += '<span class="hl-sql">' + escapeHtml(preview) + '</span>';
    html += '</li>';
  });
  html += '</ul>';
  container.innerHTML = html;
}

function restoreHistory(idx) {
  var h = _history[idx];
  if (!h) return;
  $('sql-input').value = h.sql;
  $('dialect-select').value = h.dialect;
}

async function loadPresets() {
  try {
    var data = await api('GET', '/api/presets');
    if (data.presets && data.presets.length > 0) {
      var p = data.presets[0];
      $('sql-input').value = p.sql;
      $('dialect-select').value = p.dialect;
      setStatus('Loaded preset: ' + p.name);
    }
  } catch (e) { setStatus('Could not load presets.'); }
}

function clearAll() {
  $('sql-input').value = '';
  $('results-area').innerHTML = '';
  $('dag-area').innerHTML = '<div class="empty-state">Run Lint to generate DAG and cost analysis.</div>';
  $('original-pane').style.display = 'none';
  $('optimized-pane').style.display = 'none';
  $('risk-gauge').style.display = 'none';
  $('transpile-box').style.display = 'none';
  _lastResult = null;
  setStatus('Cleared.');
}

function toggleTheme() {
  var r = document.documentElement;
  if (r.getAttribute('data-theme') === 'light') {
    r.removeAttribute('data-theme');
    r.style.setProperty('--bg', '#0d1117');
    r.style.setProperty('--panel', '#161b22');
    r.style.setProperty('--panel2', '#010409');
    r.style.setProperty('--border', '#30363d');
    r.style.setProperty('--fg', '#e6edf3');
    r.style.setProperty('--dim', '#8b949e');
  } else {
    r.setAttribute('data-theme', 'light');
    r.style.setProperty('--bg', '#f6f8fa');
    r.style.setProperty('--panel', '#ffffff');
    r.style.setProperty('--panel2', '#f6f8fa');
    r.style.setProperty('--border', '#d0d7de');
    r.style.setProperty('--fg', '#1f2328');
    r.style.setProperty('--dim', '#656d76');
  }
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function initState() {
  try {
    var data = await api('GET', '/api/state');
    if (data.tier) $('tier-badge').textContent = data.tier;
  } catch (e) { /* server may not be running */ }
}

initState();
</script>
</body>
</html>
"""
