"""The three static files of the read-only web board. Plain strings so the installer needs no new directory.

The page draws only what the server already built (labels, tones, glyphs, ordering, sections): it never
re-derives job state. Every piece of worker-supplied text goes in through textContent, never as HTML.
"""

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rig board</title>
<link rel="stylesheet" href="/app.css">
</head>
<body>
<header>
  <span id="project" class="project">Rig</span>
  <span id="health" class="health"></span>
  <span class="readonly" title="This page cannot change anything">read-only</span>
</header>
<nav id="tabs" role="tablist"></nav>
<div id="banner" role="status"></div>
<main id="main"><p class="muted">Loading…</p></main>
<script src="/app.js"></script>
</body>
</html>
"""

APP_CSS = """:root { color-scheme: light dark; --fg:#1b1f23; --bg:#fff; --muted:#6a737d; --line:#d0d7de; --sel:#e8f0fe; --attention:#9a6700; --ok:#1a7f37; --bad:#cf222e; --active:#0969da; }
@media (prefers-color-scheme: dark) { :root { --fg:#e6edf3; --bg:#0d1117; --muted:#8b949e; --line:#30363d; --sel:#1c2a44; --attention:#d29922; --ok:#3fb950; --bad:#f85149; --active:#58a6ff; } }
* { box-sizing: border-box; }
body { margin:0; font:14px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color:var(--fg); background:var(--bg); }
header { display:flex; gap:1rem; align-items:baseline; padding:.6rem 1rem; border-bottom:1px solid var(--line); }
.project { font-weight:700; }
.health { color:var(--muted); flex:1; }
.readonly { font-size:12px; border:1px solid var(--line); border-radius:3px; padding:0 .4rem; color:var(--muted); }
nav { display:flex; gap:.25rem; padding:.4rem 1rem 0; border-bottom:1px solid var(--line); overflow-x:auto; }
nav button { font:inherit; color:var(--muted); background:none; border:1px solid transparent; border-bottom:none; padding:.3rem .8rem; cursor:pointer; white-space:nowrap; }
nav button[aria-selected=true] { color:var(--fg); border-color:var(--line); background:var(--bg); margin-bottom:-1px; }
#banner { padding:.3rem 1rem; color:var(--attention); min-height:1.6em; }
main { padding:0 1rem 2rem; }
.split { display:grid; grid-template-columns:minmax(0,1.1fr) minmax(0,1fr); gap:1rem; }
@media (max-width: 800px) { .split { grid-template-columns:1fr; } }
.rows { list-style:none; margin:0; padding:0; }
.rows li { display:grid; grid-template-columns:7.5rem 1fr auto; gap:.6rem; padding:.35rem .5rem; border-bottom:1px solid var(--line); cursor:pointer; }
.rows li[aria-selected=true] { background:var(--sel); }
.rows .second { grid-column:2 / 4; color:var(--muted); font-size:12px; }
.tone-attention { color:var(--attention); font-weight:700; }
.tone-active { color:var(--active); }
.tone-verified, .tone-ok { color:var(--ok); }
.tone-failed { color:var(--bad); }
.muted { color:var(--muted); }
.detail { border:1px solid var(--line); padding:.6rem .8rem; white-space:pre-wrap; overflow-wrap:anywhere; }
.detail h3, .section h3 { margin:.2rem 0 .4rem; font-size:13px; color:var(--muted); font-weight:600; }
.section { border-top:1px solid var(--line); padding:.4rem 0; }
.section div { white-space:pre-wrap; overflow-wrap:anywhere; }
button.link { font:inherit; color:var(--active); background:none; border:none; padding:0; cursor:pointer; text-decoration:underline; }
pre.log { margin:0; padding:.6rem .8rem; border:1px solid var(--line); overflow:auto; max-height:70vh; white-space:pre-wrap; overflow-wrap:anywhere; }
"""

APP_JS = r"""(function () {
  'use strict';
  var TABS = ['Jobs', 'Queue', 'Workflows', 'Workspace', 'Fleet', 'Logs'];
  var state = { tab: 'Jobs', view: null, selected: {}, panel: {}, log: null, evidence: null, evidenceId: '', lastOK: 0, error: '', chat: null, chatView: null };

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) { Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); }); }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }
  function api(method, params) {
    var query = Object.keys(params || {}).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&');
    return fetch('/api/' + method + (query ? '?' + query : ''), { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok || body.error) { throw new Error((body.error && body.error.message) || body.message || ('HTTP ' + r.status)); }
        return body.result;
      });
    });
  }
  function rowsFor(tab) {
    if (!state.view) { return []; }
    return tab === 'Jobs' ? state.view.jobs : tab === 'Queue' ? state.view.queue : tab === 'Workflows' ? state.view.workflows : [];
  }
  function selectedRow(tab) {
    var rows = rowsFor(tab), id = state.selected[tab];
    for (var i = 0; i < rows.length; i++) { if (rows[i].id === id) { return rows[i]; } }
    return rows[0] || null;
  }
  function toneClass(tone) { return tone ? 'tone-' + String(tone).replace(/[^a-z]/g, '') : ''; }

  function renderHeader() {
    var v = state.view;
    document.getElementById('project').textContent = v ? 'Rig · ' + v.header.project : 'Rig';
    document.getElementById('health').textContent = v ? '! ' + v.header.attention + ' need attention · ● ' + v.header.running + ' running · slots ' + v.header.slots + '/' + v.header.cap : '';
  }
  // The Chat tab exists only while the headless-parent chat experiment is on.
  function visibleTabs() { return state.chat && state.chat.enabled ? TABS.concat(['Chat']) : TABS; }
  function renderTabs() {
    var nav = document.getElementById('tabs');
    nav.textContent = '';
    visibleTabs().forEach(function (name) {
      var label = name, info = state.view && state.view.tabs.filter(function (t) { return t.name === name; })[0];
      if (info) { label += info.attention > 0 ? ' !' + info.attention : info.count > 0 ? ' ' + info.count : ''; }
      var button = el('button', { role: 'tab', 'aria-selected': name === state.tab ? 'true' : 'false', 'data-tab': name }, label);
      button.addEventListener('click', function () { state.tab = name; state.evidence = null; render(); refreshTab(); });
      nav.appendChild(button);
    });
  }
  function renderSections(container, sections) {
    (sections || []).forEach(function (section, index) {
      var box = el('div', { 'class': 'section' });
      box.appendChild(el('h3', null, section.title));
      (section.lines || []).forEach(function (line) { box.appendChild(el('div', { 'class': toneClass(line.tone) }, line.text)); });
      container.appendChild(box);
    });
  }
  function renderList(main) {
    var rows = rowsFor(state.tab), current = selectedRow(state.tab);
    var split = el('div', { 'class': 'split' }), list = el('ul', { 'class': 'rows' }), detail = el('div', { 'class': 'detail' });
    if (!rows.length) { list.appendChild(el('li', null, 'Nothing here yet.')); }
    rows.forEach(function (row) {
      var li = el('li', { 'aria-selected': current && current.id === row.id ? 'true' : 'false', 'data-id': row.id });
      li.appendChild(el('span', { 'class': toneClass(row.tone) }, (row.glyph || '·') + ' ' + row.label));
      li.appendChild(el('span', null, row.title));
      li.appendChild(el('span', { 'class': 'muted' }, row.id));
      if (row.second) { li.appendChild(el('span', { 'class': 'second' }, row.second)); }
      li.addEventListener('click', function () { state.selected[state.tab] = row.id; state.evidence = null; render(); });
      list.appendChild(li);
    });
    if (current) {
      detail.appendChild(el('h3', { 'class': toneClass(current.tone) }, (current.glyph || '·') + ' ' + current.label));
      if (current.explanation) { detail.appendChild(el('div', null, current.explanation)); }
      detail.appendChild(el('div', null, '\n' + (current.detail || []).join('\n')));
      if (state.tab === 'Jobs') {
        var open = el('button', { 'class': 'link', 'data-action': 'evidence' }, 'evidence');
        open.addEventListener('click', function () { openEvidence(current.id); });
        var logs = el('button', { 'class': 'link', 'data-action': 'logs' }, 'log');
        logs.addEventListener('click', function () { state.tab = 'Logs'; render(); refreshTab(); });
        var bar = el('div'); bar.appendChild(open); bar.appendChild(document.createTextNode('  ')); bar.appendChild(logs);
        detail.appendChild(bar);
      }
      if (state.evidence && state.evidenceId === current.id) {
        detail.appendChild(el('h3', null, 'Evidence · ' + current.id));
        renderSections(detail, state.evidence.sections);
      }
    }
    split.appendChild(list); split.appendChild(detail); main.appendChild(split);
  }
  function renderPanel(main, key, empty) {
    var panel = state.panel[key];
    if (!panel) { main.appendChild(el('p', { 'class': 'muted' }, empty)); return; }
    renderSections(main, panel.sections);
  }
  function renderLogs(main) {
    var job = selectedRow('Jobs');
    if (!state.log) { main.appendChild(el('p', { 'class': 'muted' }, 'Loading log…')); return; }
    if (!state.log.job) { main.appendChild(el('p', { 'class': 'muted' }, 'No jobs yet. Output of the active job appears here.')); return; }
    main.appendChild(el('h3', { 'class': 'muted' }, 'Logs · ' + state.log.job.id + ' ' + ((state.log.job.label || '') + ' ' + (state.log.job.worker || '')).trim()));
    main.appendChild(el('pre', { 'class': 'log' }, state.log.text));
  }
  function renderChat(main) {
    main.appendChild(el('p', { 'class': 'muted' }, 'EXPERIMENT: headless parent chat. This page only reads; send messages from the terminal board (c on the Chat tab) or with rig chat send.'));
    var view = state.chatView;
    if (!view) { main.appendChild(el('p', { 'class': 'muted' }, state.chat && state.chat.sessions.length ? 'Loading transcript…' : 'No chat sessions yet.')); return; }
    main.appendChild(el('h3', { 'class': 'muted' }, 'Chat · ' + view.session + ' · ' + view.parent + ' · ' + view.state + (view.note ? ' (' + view.note + ')' : '')));
    var box = el('div', { 'class': 'section' });
    (view.entries || []).forEach(function (entry) {
      box.appendChild(el('div', { 'class': entry.role === 'error' ? 'tone-failed' : entry.role === 'tool' || entry.role === 'system' || entry.role === 'switch' ? 'muted' : '' }, (entry.role === 'user' ? 'you' : entry.role === 'assistant' && entry.parent ? entry.parent : entry.role) + ' › ' + entry.text));
    });
    main.appendChild(box);
  }
  function render() {
    renderHeader(); renderTabs(); writeHash();
    document.getElementById('banner').textContent = state.error;
    var main = document.getElementById('main');
    main.textContent = '';
    if (!state.view && !state.error) { main.appendChild(el('p', { 'class': 'muted' }, 'Loading…')); return; }
    if (state.tab === 'Workspace') { renderPanel(main, 'workspace', 'Loading workspace…'); }
    else if (state.tab === 'Fleet') { renderPanel(main, 'fleet', 'Loading fleet…'); }
    else if (state.tab === 'Logs') { renderLogs(main); }
    else if (state.tab === 'Chat') { renderChat(main); }
    else { renderList(main); }
  }
  function fail(error) {
    var age = state.lastOK ? ' (snapshot ' + Math.round((Date.now() - state.lastOK) / 1000) + 's old)' : ' (no snapshot yet)';
    state.error = 'refresh failed: ' + error.message + age;
    render();
  }
  function refreshBoard() {
    // Asking the experiment's status is cheap and tells the page whether to offer the Chat tab at all.
    api('chat.status').then(function (s) { state.chat = s; }).catch(function () { /* an older server has no chat; the tab stays hidden */ });
    return api('board.snapshot', { rows: 200 }).then(function (r) { state.view = r.view; state.lastOK = Date.now(); state.error = ''; render(); }).catch(fail);
  }
  function refreshTab() {
    if (state.tab === 'Workspace') { return api('workspace.status', { deep: 'true' }).then(function (r) { state.panel.workspace = r.view; render(); }).catch(fail); }
    if (state.tab === 'Fleet') { return api('fleet.status').then(function (r) { state.panel.fleet = r.view; render(); }).catch(fail); }
    if (state.tab === 'Chat') {
      return api('chat.status').then(function (s) {
        state.chat = s;
        var latest = s.sessions && s.sessions[0];
        if (!latest) { state.chatView = null; render(); return null; }
        return api('chat.transcript', { session: latest.id, tail: 200 }).then(function (v) { state.chatView = v; render(); });
      }).catch(fail);
    }
    if (state.tab === 'Logs') {
      var job = selectedRow('Jobs');
      return api('log.follow', { id: job ? job.id : '', lines: 200 }).then(function (r) { state.log = r; render(); }).catch(fail);
    }
    return Promise.resolve();
  }
  function openEvidence(id) {
    state.evidenceId = id; state.evidence = null;
    api('job.evidence', { id: id }).then(function (r) { state.evidence = r; render(); }).catch(fail);
  }
  function tick() { if (document.hidden) { return; } refreshBoard().then(refreshTab); }
  // Deep links: #Fleet, #Jobs/<job id>, #Jobs/<job id>/evidence. The hash follows the page, so a view can be shared.
  function applyHash() {
    var parts = decodeURIComponent((location.hash || '').slice(1)).split('/');
    if (TABS.indexOf(parts[0]) >= 0 || parts[0] === 'Chat') { state.tab = parts[0]; }
    if (parts[1]) { state.selected[state.tab] = parts[1]; }
    if (parts[2] === 'evidence' && parts[1]) { openEvidence(parts[1]); }
  }
  function writeHash() {
    var row = state.tab === 'Jobs' ? selectedRow('Jobs') : null;
    try { history.replaceState(null, '', '#' + encodeURIComponent(state.tab) + (row ? '/' + encodeURIComponent(row.id) : '')); } catch (e) { /* a sandboxed frame may refuse */ }
  }
  applyHash();
  refreshBoard().then(refreshTab);
  setInterval(tick, 2000);
  render();
})();
"""
