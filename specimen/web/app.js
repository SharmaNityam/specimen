(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const els = {
    healthDot: $('#health-dot'), healthLabel: $('#health-label'), version: $('#version-label'),
    grid: $('#example-grid'), runState: $('#run-state'), timeline: $('#timeline'), eventCount: $('#event-count'),
    liveLabel: $('#live-label'), lastUpdate: $('#last-update'), beforeTable: $('#before-table'), afterTable: $('#after-table'),
    beforeCount: $('#before-count'), afterCount: $('#after-count'), previewTag: $('#preview-tag'), previewCaption: $('#preview-caption'),
    download: $('#download-button'), resultEmpty: $('#result-empty'), resultContent: $('#result-content'), resultStatus: $('#result-status'),
    resultId: $('#result-id'), metricRows: $('#metric-rows'), metricRowSub: $('#metric-row-sub'), metricReduction: $('#metric-reduction'),
    metricVerification: $('#metric-verification'), metricVerificationSub: $('#metric-verification-sub'), fingerprint: $('#fingerprint'),
    fingerprintNote: $('#fingerprint-note'), transforms: $('#transform-list'), blockers: $('#blocker-list'), seal: $('#verification-seal'),
    verifyCopy: $('#verification-copy'), verifyChecks: $('#verification-checks'), dockerCallout: $('#docker-callout'),
    uploadForm: $('#upload-form'), uploadSubmit: $('#upload-submit'), uploadMessage: $('#upload-message'), uploadPanel: $('#upload-panel'),
    toastRegion: $('#toast-region')
  };
  const state = { health: null, examples: [], activeJob: null, busy: false, pollTimer: null, pollFailures: 0, eventsSeen: 0, lastStatus: null, toastTimer: null, uploadDockerWarning: false };
  const terminal = new Set(['complete', 'error']);

  function text(node, value) { if (node) node.textContent = value == null ? '' : String(value); }
  function setBusy(value) {
    state.busy = value;
    document.querySelectorAll('[data-run-example]').forEach((button) => { button.disabled = value; });
    if (els.uploadSubmit) els.uploadSubmit.disabled = value || !state.health?.docker_available;
    if (els.download) els.download.disabled = value || !state.activeJob?.download_available;
    document.body.classList.toggle('is-running', value);
  }
  function showToast(message, kind = 'error') {
    const toast = document.createElement('div');
    toast.className = `toast ${kind}`;
    toast.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    const mark = document.createElement('span'); mark.className = 'toast-mark'; mark.textContent = kind === 'error' ? '!' : '✓';
    const copy = document.createElement('span'); copy.textContent = message;
    toast.append(mark, copy); els.toastRegion.append(toast);
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => toast.remove(), 6500);
  }
  function errorMessage(error, action = 'connect to the local service') {
    if (error?.name === 'AbortError') return 'The request timed out. Check that the local service is still running, then try again.';
    const detail = error?.message && error.message !== 'Failed to fetch' ? ` (${error.message})` : '';
    return `Could not ${action}${detail}. Check the local service and try again.`;
  }
  async function api(path, options = {}, timeout = 12000) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal, headers: options.body instanceof FormData ? options.headers : { Accept: 'application/json', ...options.headers } });
      if (!response.ok) {
        let message = `Request failed (${response.status})`;
        try { const body = await response.json(); message = body.detail || body.error || body.message || message; } catch (_) { /* leave useful status */ }
        throw new Error(message);
      }
      return response.status === 204 ? null : response.json();
    } finally { window.clearTimeout(timer); }
  }
  function setHealth(health) {
    state.health = health;
    const ready = health?.status === 'ok';
    els.healthDot.classList.toggle('online', ready);
    els.healthDot.classList.toggle('offline', !ready);
    text(els.healthLabel, ready ? 'Service online' : 'Service unavailable');
    text(els.version, health?.version ? `v${health.version}` : 'LOCAL');
    const docker = Boolean(health?.docker_available);
    els.dockerCallout.className = `upload-callout ${docker ? 'available' : 'unavailable'}`;
    const icon = document.createElement('span'); icon.className = 'callout-mark'; icon.textContent = docker ? '✓' : '×';
    const message = document.createElement('p');
    message.textContent = docker ? 'Docker is available. Uploaded scripts run in an isolated, network-disabled container.' : 'Docker is unavailable. Uploads need Docker because scripts are executed in isolation. Start Docker, then reload this page. Bundled demos remain available.';
    els.dockerCallout.replaceChildren(icon, message);
    if (els.uploadSubmit) els.uploadSubmit.disabled = state.busy || !docker;
  }
  function scenarioCard(example) {
    const card = document.createElement('article'); card.className = 'example-card';
    const top = document.createElement('div'); top.className = 'example-card-top';
    const index = document.createElement('span'); index.className = 'example-index micro'; index.textContent = `SPECIMEN / ${String(state.examples.indexOf(example) + 1).padStart(2, '0')}`;
    const expected = document.createElement('span'); expected.className = `expected-chip ${example.expected === 'verified' ? 'good' : 'blocked'}`; expected.textContent = example.expected === 'verified' ? 'EXPORT EXPECTED' : 'CONFLICT EXPECTED';
    top.append(index, expected);
    const title = document.createElement('h3'); title.textContent = example.name || 'Untitled example';
    const description = document.createElement('p'); description.textContent = example.description || 'A bundled synthetic failure scenario.';
    const meta = document.createElement('div'); meta.className = 'example-meta';
    const rowLabel = document.createElement('span'); rowLabel.textContent = `${formatNumber(example.rows)} rows`;
    const bug = document.createElement('span'); bug.className = 'bug-label'; bug.textContent = example.bug || 'Python failure';
    meta.append(rowLabel, bug);
    const action = document.createElement('button'); action.type = 'button'; action.className = 'example-action'; action.dataset.runExample = example.id;
    action.disabled = state.busy; action.setAttribute('aria-label', `Run ${example.name || 'demo'}`);
    const btnText = document.createElement('span'); btnText.textContent = 'Run this demo';
    const arrow = document.createElement('span'); arrow.textContent = '↗'; arrow.setAttribute('aria-hidden', 'true'); action.append(btnText, arrow);
    action.addEventListener('click', () => startDemo(example.id));
    card.append(top, title, description, meta, action);
    return card;
  }
  function renderExamples(examples) {
    const order = ['unicode', 'relations', 'conflict'];
    state.examples = (Array.isArray(examples) ? examples : []).slice().sort((left, right) => {
      const a = order.indexOf(left?.id), b = order.indexOf(right?.id);
      return (a < 0 ? order.length : a) - (b < 0 ? order.length : b);
    });
    els.grid.setAttribute('aria-busy', 'false');
    if (!state.examples.length) {
      const empty = document.createElement('div'); empty.className = 'examples-empty';
      const title = document.createElement('strong'); title.textContent = 'No bundled demos found';
      const body = document.createElement('p'); body.textContent = 'The service is online but did not return any examples. You can retry the connection or use the upload panel when Docker is available.';
      const retry = document.createElement('button'); retry.className = 'text-button'; retry.type = 'button'; retry.textContent = 'Retry loading demos'; retry.addEventListener('click', loadExamples);
      empty.append(title, body, retry); els.grid.replaceChildren(empty); return;
    }
    els.grid.replaceChildren(...state.examples.map(scenarioCard));
  }
  async function loadExamples() {
    els.grid.setAttribute('aria-busy', 'true');
    try { renderExamples(await api('/api/examples')); }
    catch (error) {
      els.grid.setAttribute('aria-busy', 'false');
      const card = document.createElement('div'); card.className = 'examples-empty error-empty';
      const title = document.createElement('strong'); title.textContent = 'Demos could not load';
      const body = document.createElement('p'); body.textContent = errorMessage(error, 'load bundled demos');
      const retry = document.createElement('button'); retry.className = 'text-button'; retry.type = 'button'; retry.textContent = 'Try again'; retry.addEventListener('click', loadExamples);
      card.append(title, body, retry); els.grid.replaceChildren(card);
    }
  }
  function formatNumber(value) { return Number.isFinite(Number(value)) ? Number(value).toLocaleString() : '—'; }
  function resetRun(title) {
    stopPolling(); state.eventsSeen = 0; state.lastStatus = null; state.pollFailures = 0;
    text(els.eventCount, '0 EVENTS');
    const blank = document.createElement('div'); blank.className = 'timeline-empty';
    const glyph = document.createElement('span'); glyph.className = 'empty-glyph'; glyph.textContent = '···';
    const p = document.createElement('p'); p.textContent = 'Preparing verification run…';
    const sub = document.createElement('span'); sub.textContent = title;
    blank.append(glyph, p, sub); els.timeline.replaceChildren(blank);
    text(els.liveLabel, 'STARTING'); els.lastUpdate.textContent = 'Starting local job';
    els.runState.classList.add('active'); text(els.runState.querySelector('span:last-child'), 'Preparing verification…');
    els.resultEmpty.classList.remove('hidden'); els.resultContent.classList.add('hidden');
    els.download.disabled = true; state.activeJob = null;
    els.seal.className = 'verification-seal';
    text(els.seal.querySelector('span'), 'CHECKING');
    text(els.seal.querySelector('b'), '…');
    text($('#verification-heading'), 'A new check is running.');
    text(els.verifyCopy, 'Previous results are cleared. This run must finish its own baseline, replay, and privacy checks.');
    renderChecks({}, {});
    clearPreview('Processing synthetic input…', '— rows', 'Awaiting reduced rows…');
  }
  function clearPreview(before, count, after) {
    emptyTable(els.beforeTable, before); emptyTable(els.afterTable, after);
    text(els.beforeCount, count); text(els.afterCount, '— rows');
  }
  function emptyTable(target, message) { const div = document.createElement('div'); div.className = 'empty-preview'; div.textContent = message; target.replaceChildren(div); syncTableScroll(); }
  function syncTableScroll() {
    document.querySelectorAll('[data-scroll-target]').forEach((button) => {
      const region = document.getElementById(button.dataset.scrollTarget);
      const room = region ? region.scrollWidth - region.clientWidth : 0;
      const atEdge = room <= 1 || (Number(button.dataset.scrollDir) < 0 ? region.scrollLeft <= 1 : region.scrollLeft >= room - 1);
      button.setAttribute('aria-disabled', String(atEdge));
    });
  }
  async function startDemo(id) {
    if (state.busy) return;
    const example = state.examples.find((item) => item.id === id);
    resetRun(example?.name || 'Bundled synthetic demo'); setBusy(true);
    try {
      const result = await api('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ example_id: id, model_assist: false }) });
      attachJob(result, true);
    } catch (error) { failStart(error, 'start this demo'); }
  }
  async function startUpload(formData) {
    if (state.busy) return false;
    resetRun('Uploaded input · original values hidden'); setBusy(true);
    text(els.previewTag, 'UPLOAD / REDACTED');
    text(els.previewCaption, 'Uploaded values are hidden; only column and row shape is shown.');
    try {
      const result = await api('/api/jobs/upload', { method: 'POST', body: formData }, 30000);
      attachJob(result, false);
      return true;
    } catch (error) { failStart(error, 'start the isolated upload run'); }
    return false;
  }
  function failStart(error, action) {
    setBusy(false); state.activeJob = null; stopPolling();
    els.runState.classList.remove('active'); text(els.runState.querySelector('span:last-child'), 'Could not start');
    text(els.liveLabel, 'ERROR'); text(els.lastUpdate, 'Job was not created');
    const msg = errorMessage(error, action); appendEvent({ type: 'error', message: msg }); showToast(msg);
  }
  function attachJob(job, isDemo) {
    if (!job?.id) throw new Error('The service did not return a job ID.');
    state.activeJob = { id: job.id, download_available: false, isDemo };
    text(els.resultId, `JOB ${job.id}`);
    text(els.runState.querySelector('span:last-child'), 'Reduction in progress');
    text(els.liveLabel, 'RUNNING');
    pollJob();
  }
  function stopPolling() { if (state.pollTimer) window.clearTimeout(state.pollTimer); state.pollTimer = null; }
  async function pollJob() {
    const job = state.activeJob; if (!job) return;
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(job.id)}`, {}, 10000);
      state.pollFailures = 0; updateJob(data, job);
      if (terminal.has(data.status)) { finishJob(data, job); return; }
      state.pollTimer = window.setTimeout(pollJob, data.status === 'queued' ? 900 : 700);
    } catch (error) {
      state.pollFailures += 1;
      if (state.pollFailures > 5) {
        setBusy(false); text(els.liveLabel, 'DISCONNECTED');
        text(els.runState.querySelector('span:last-child'), 'Connection lost');
        text(els.lastUpdate, 'Reconnect attempts ended');
        appendEvent({ type: 'error', message: 'Lost connection to the local service after several retries. The job may still be running; reload to reconnect.' });
        showToast(errorMessage(error, 'reconnect to the running job')); return;
      }
      text(els.liveLabel, 'RECONNECTING'); text(els.lastUpdate, `Retry ${state.pollFailures} of 5`);
      state.pollTimer = window.setTimeout(pollJob, Math.min(900 * (2 ** (state.pollFailures - 1)), 8000));
    }
  }
  function updateJob(data, job) {
    if (!data || typeof data !== 'object') return;
    if (data.status && state.lastStatus !== data.status) {
      state.lastStatus = data.status;
      text(els.runState.querySelector('span:last-child'), statusLabel(data.status));
      text(els.liveLabel, data.status.toUpperCase());
    }
    const events = Array.isArray(data.events) ? data.events : [];
    for (; state.eventsSeen < events.length; state.eventsSeen += 1) appendEvent(events[state.eventsSeen]);
    if (events.length) text(els.lastUpdate, `${events.length} event${events.length === 1 ? '' : 's'} received`);
    text(els.eventCount, `${events.length} EVENT${events.length === 1 ? '' : 'S'}`);
    if (data.preview) renderPreview(data.preview, job.isDemo);
    if (data.report) renderReport(data.report, job);
    job.download_available = Boolean(data.download_available);
    els.download.disabled = state.busy || !job.download_available;
  }
  function statusLabel(status) {
    return ({ queued: 'Queued for execution', running: 'Reduction in progress', complete: 'Run complete', error: 'Run failed' })[status] || status;
  }
  function appendEvent(event) {
    if (!event || typeof event !== 'object') return;
    if (state.eventsSeen === 0 && els.timeline.querySelector('.timeline-empty')) els.timeline.replaceChildren();
    const row = document.createElement('article'); row.className = `timeline-event ${safeClass(event.type)}`;
    const marker = document.createElement('span'); marker.className = 'timeline-marker'; marker.textContent = event.type === 'error' ? '!' : '·';
    const body = document.createElement('div'); body.className = 'timeline-copy';
    const message = document.createElement('p'); message.textContent = typeof event.message === 'string' ? event.message : 'Execution event'; body.append(message);
    const meta = document.createElement('span'); meta.className = 'timeline-meta micro';
    const bits = [];
    if (event.runs != null) bits.push(`${formatNumber(event.runs)} RUNS`);
    if (event.rows != null) bits.push(`${formatNumber(event.rows)} ROWS`);
    if (event.elapsed != null) bits.push(`${event.elapsed}s`);
    meta.textContent = [event.type, ...bits].filter(Boolean).join(' · ').toUpperCase(); body.append(meta);
    row.append(marker, body); els.timeline.append(row);
    els.timeline.scrollTop = els.timeline.scrollHeight;
  }
  function safeClass(value) { return String(value || 'event').toLowerCase().replace(/[^a-z0-9_-]/g, '-').slice(0, 30); }
  function renderPreview(preview, isDemo) {
    const before = Array.isArray(preview.before) ? preview.before : [];
    const after = Array.isArray(preview.after) ? preview.after : [];
    if (isDemo) {
      renderTable(els.beforeTable, before); text(els.beforeCount, `${formatNumber(before.length)} shown`);
    } else {
      emptyTable(els.beforeTable, 'Hidden for uploaded files'); text(els.beforeCount, 'private');
      text(els.previewTag, 'UPLOAD / REDACTED'); text(els.previewCaption, 'Uploaded values are hidden; only column and row shape is shown.');
    }
    renderTable(els.afterTable, after, !isDemo); text(els.afterCount, `${formatNumber(after.length)} shown`);
  }
  function renderTable(target, rows, hideValues = false) {
    if (!rows.length) { emptyTable(target, 'No preview rows available yet.'); return; }
    const columns = [...new Set(rows.slice(0, 8).flatMap((row) => row && typeof row === 'object' ? Object.keys(row) : []))].slice(0, 5);
    if (!columns.length) { emptyTable(target, 'Preview data is not available.'); return; }
    const wrap = document.createElement('table'); const head = document.createElement('thead'); const tr = document.createElement('tr');
    columns.forEach((column) => { const th = document.createElement('th'); th.textContent = column; tr.append(th); }); head.append(tr);
    const body = document.createElement('tbody');
    rows.slice(0, 5).forEach((row) => {
      const line = document.createElement('tr');
      columns.forEach((column) => { const td = document.createElement('td'); const value = row?.[column]; td.textContent = hideValues ? '••••••' : value == null ? '—' : typeof value === 'string' ? value : JSON.stringify(value); if (hideValues || (typeof value === 'string' && /^(?:<[^>]+>|\*+|redacted)/i.test(value))) td.classList.add('redacted-value'); line.append(td); });
      body.append(line);
    });
    wrap.append(head, body); target.replaceChildren(wrap); syncTableScroll();
  }
  function renderReport(report, job) {
    els.resultEmpty.classList.add('hidden'); els.resultContent.classList.remove('hidden');
    const status = String(report.status || 'needs_review').toLowerCase();
    els.resultStatus.className = `result-status status-${safeClass(status)}`;
    text(els.resultStatus.querySelector('span'), status.replaceAll('_', ' '));
    text(els.metricRows, formatNumber(report.final_rows)); text(els.metricRowSub, `from ${formatNumber(report.original_rows)} rows`);
    const original = Number(report.original_rows), reduced = Number(report.final_rows);
    const percent = original > 0 && Number.isFinite(reduced) ? Math.max(0, (1 - reduced / original) * 100) : null;
    text(els.metricReduction, percent == null ? '—' : `${percent.toFixed(1)}%`);
    const verification = report.verification;
    text(els.metricVerification, verificationCount(verification));
    text(els.metricVerificationSub, verificationDescription(verification));
    text(els.fingerprint, report.baseline_fingerprint == null ? 'Unavailable' : fingerprintText(report.baseline_fingerprint));
    text(els.fingerprintNote, report.sha256 ? `Specimen SHA-256 · ${report.sha256}` : 'The failure signature is captured before reduction and compared against the final specimen.');
    fillList(els.transforms, transformationEntries(report.transformations), 'No transformations reported.');
    fillList(els.blockers, report.blockers, 'No unresolved values reported.');
    const verified = status === 'verified';
    text(document.querySelector('.fingerprint-mark'), verified ? 'FINAL REPLAYS MATCH' : 'BASELINE ONLY');
    text(document.querySelector('#verification-heading'), verified ? 'Same failure, checked again.' : 'Verification needs review.');
    els.seal.className = `verification-seal ${verified ? 'verified' : 'unverified'}`;
    text(els.seal.querySelector('span'), verified ? 'VERIFIED' : 'REVIEW');
    text(els.seal.querySelector('b'), verified ? '✓' : '…');
    text(els.verifyCopy, verified ? 'The final specimen reproduced the same observed failure. Review the transformations and privacy notes before sharing.' : 'This run needs review before sharing. Check the blockers and verification details in the report.');
    renderChecks(verification, report);
    if (job?.id) text(els.resultId, `JOB ${job.id}`);
  }
  function fingerprintText(value) {
    if (typeof value === 'string') return value;
    if (typeof value === 'object') return Object.entries(value).map(([key, val]) => `${key}: ${typeof val === 'string' ? val : JSON.stringify(val)}`).join(' · ');
    return String(value);
  }
  function verificationCount(value) {
    if (value == null) return '—';
    if (typeof value === 'number') return String(value);
    if (Array.isArray(value)) return String(value.length);
    if (typeof value === 'object') {
      const total = value.final_runs ?? value.repeats ?? value.runs ?? value.count;
      return total == null ? '0' : String(total);
    }
    return String(value);
  }
  function verificationDescription(value) {
    if (value && typeof value === 'object') {
      const runs = value.final_runs ?? value.repeats ?? value.runs;
      if (runs != null) return `${runs} final replay${Number(runs) === 1 ? '' : 's'}`;
      return 'no final replay verified';
    }
    return 'final replay runs';
  }
  function fillList(target, data, emptyMessage) {
    const entries = Array.isArray(data) ? data : data == null ? [] : [data];
    const nodes = entries.length ? entries.map((item) => { const li = document.createElement('li'); li.textContent = typeof item === 'string' ? item : safeObjectText(item); return li; }) : [Object.assign(document.createElement('li'), { textContent: emptyMessage })];
    target.replaceChildren(...nodes);
  }
  function transformationEntries(value) {
    if (value == null) return [];
    if (Array.isArray(value)) return value;
    if (typeof value !== 'object') return [String(value)];
    const entries = [];
    const columns = value.sensitive_columns;
    const changed = value.values_changed;
    if (columns != null && Number.isFinite(Number(columns))) entries.push(`${formatNumber(columns)} sensitive column${Number(columns) === 1 ? '' : 's'} selected`);
    if (changed != null && Number.isFinite(Number(changed))) entries.push(`${formatNumber(changed)} distinct sensitive source value${Number(changed) === 1 ? '' : 's'} identified`);
    if (typeof value.strategy === 'string' && value.strategy) entries.push(`Replacement strategy: ${value.strategy}`);
    for (const [key, item] of Object.entries(value)) {
      if (['sensitive_columns', 'values_changed', 'strategy'].includes(key) || item == null) continue;
      const label = key.replaceAll('_', ' ');
      entries.push(`${label}: ${typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean' ? item : 'recorded'}`);
    }
    return entries;
  }
  function safeObjectText(value) {
    if (value && typeof value === 'object') {
      const label = value.description || value.action || value.type || value.name;
      return label ? `${label}${value.count != null ? ` · ${value.count}` : ''}` : JSON.stringify(value);
    }
    return String(value);
  }
  function renderChecks(verification, report) {
    const status = String(report.status || '').toLowerCase();
    const baselineRuns = Number(verification?.baseline_runs);
    const finalRuns = Number(verification?.final_runs);
    const baseline = Number.isFinite(baselineRuns) && baselineRuns >= 2 ? Boolean(report.baseline_fingerprint) && status !== 'unstable' : null;
    const final = Number.isFinite(finalRuns) && finalRuns >= 2
      ? report.baseline_fingerprint != null && verification?.final_fingerprint === report.baseline_fingerprint
      : null;
    const hasPrivacyBlocker = Array.isArray(report.blockers) && report.blockers.some((item) => /sensitive|privacy|secret|pseudonym/i.test(typeof item === 'string' ? item : safeObjectText(item)));
    const privacy = status === 'verified' ? (Array.isArray(report.blockers) ? report.blockers.length === 0 : true) : hasPrivacyBlocker ? false : null;
    const checks = [['Baseline consistency', baseline], ['Final replay', final], ['Sensitive values', privacy]];
    els.verifyChecks.replaceChildren(...checks.map(([label, passed]) => {
      const item = document.createElement('span'); const mark = document.createElement('i');
      item.className = passed === true ? 'check-passed' : passed === false ? 'check-failed' : 'check-unknown';
      mark.textContent = passed === true ? '✓' : passed === false ? '×' : '○';
      const copy = document.createElement('span'); copy.textContent = label; item.append(mark, copy); return item;
    }));
  }
  function finishJob(data, job) {
    setBusy(false); text(els.lastUpdate, data.status === 'complete' ? 'Final report received' : 'Job ended with an error');
    els.runState.classList.remove('active');
    if (data.status === 'error') {
      text(els.runState.querySelector('span:last-child'), 'Run failed'); text(els.liveLabel, 'ERROR');
      const message = data.error || 'The job stopped before it could produce a report.';
      if (!data.report) { els.resultEmpty.querySelector('strong').textContent = 'Run could not complete'; els.resultEmpty.querySelector('p').textContent = message; }
      appendEvent({ type: 'error', message }); showToast(message);
    } else {
      text(els.runState.querySelector('span:last-child'), `Finished · ${String(data.report?.status || 'review').replaceAll('_', ' ')}`);
      text(els.liveLabel, 'COMPLETE');
    }
    if (data.preview) renderPreview(data.preview, job.isDemo);
    job.download_available = Boolean(data.download_available); els.download.disabled = !job.download_available;
    window.requestAnimationFrame(() => {
      document.getElementById('result')?.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start'
      });
    });
  }
  els.download.addEventListener('click', () => {
    if (!state.activeJob || !state.activeJob.download_available) return;
    window.location.assign(`/api/jobs/${encodeURIComponent(state.activeJob.id)}/download`);
  });
  els.uploadForm.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!state.health?.docker_available) { showToast('Docker is required to run uploaded scripts. Start Docker and reload this page.', 'error'); return; }
    const form = new FormData(els.uploadForm);
    const assist = els.uploadForm.elements.model_assist.checked;
    form.set('model_assist', assist ? 'true' : 'false');
    const columns = String(form.get('sensitive_columns') || '').split(',').map((item) => item.trim()).filter(Boolean).join(',');
    form.set('sensitive_columns', columns);
    text(els.uploadMessage, 'Creating an isolated job…');
    startUpload(form).then((submitted) => {
      text(els.uploadMessage, submitted ? 'Job submitted. Follow each run in the evidence timeline.' : 'The upload was not accepted. Review the error and try again.');
    });
  });

  async function initialize() {
    let healthError;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const health = await api('/api/health', {}, 8000); setHealth(health); healthError = null; break;
      } catch (error) {
        healthError = error;
        if (attempt < 2) await new Promise((resolve) => window.setTimeout(resolve, 500 * (2 ** attempt)));
      }
    }
    if (healthError) {
      setHealth(null); text(els.healthLabel, 'Service unavailable');
      showToast(`${errorMessage(healthError, 'reach the local Specimen service')} Retried twice.`);
    }
    await loadExamples();
  }
  document.querySelectorAll('[data-scroll-target]').forEach((button) => {
    button.addEventListener('click', () => {
      const region = document.getElementById(button.dataset.scrollTarget);
      if (!region || button.getAttribute('aria-disabled') === 'true') return;
      const step = Math.max(120, Math.round(region.clientWidth * 0.75));
      const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
      region.scrollBy({ left: Number(button.dataset.scrollDir) * step, behavior });
    });
  });
  document.querySelectorAll('.table-wrap').forEach((region) => region.addEventListener('scroll', syncTableScroll, { passive: true }));
  window.addEventListener('resize', syncTableScroll);
  initialize();
})();
