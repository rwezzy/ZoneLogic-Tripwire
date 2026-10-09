'use strict';

// Loaded only by the optional launcher. All core files remain unchanged.
(() => {
  if (typeof state === 'undefined' || typeof api !== 'function') return;
  const original = { api, selectSource, fillRule, persistConfig, renderIncidents, drawBackdrop, drawSimulation };
  let preferences = {}, preferenceSource = null, loadToken = 0;
  const node = (tag, text, className) => {
    const result = document.createElement(tag);
    if (text) result.textContent = text;
    if (className) result.className = className;
    return result;
  };
  const field = (id) => document.getElementById(id);
  const sceneKey = (source) => source?.extra?.scenario_key;
  const supported = new Set(['sorting', 'wheelchair', 'crosswalk', 'cane']);

  function text(ctx, label, x, y, size, color = '#213e38') {
    ctx.fillStyle = color; ctx.font = `600 ${size}px sans-serif`; ctx.textAlign = 'center'; ctx.fillText(label, x, y);
  }
  function icon(ctx, kind, x, y, width, height) {
    ctx.save(); ctx.translate(x, y); ctx.scale(width, height);
    ctx.lineWidth = .055; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.fillStyle = '#477b70'; ctx.strokeStyle = '#263e39';
    if (kind === 'bottle') {
      ctx.fillStyle = '#80cabc'; ctx.beginPath(); ctx.moveTo(-.18, -.48); ctx.lineTo(.18, -.48); ctx.lineTo(.18, -.26); ctx.lineTo(.34, -.12); ctx.lineTo(.34, .46); ctx.lineTo(-.34, .46); ctx.lineTo(-.34, -.12); ctx.lineTo(-.18, -.26); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#f5f5dd'; ctx.fillRect(-.31, -.02, .62, .25); ctx.fillStyle = '#256757'; ctx.fillRect(-.2, -.52, .4, .09);
    } else if (kind === 'banana') {
      ctx.fillStyle = '#efc655'; ctx.beginPath(); ctx.moveTo(-.45, -.3); ctx.bezierCurveTo(-.35, .45, .4, .65, .49, -.25); ctx.bezierCurveTo(.16, .12, -.13, .13, -.45, -.3); ctx.fill(); ctx.stroke();
    } else if (kind === 'car') {
      ctx.fillStyle = '#d4a653'; ctx.beginPath(); ctx.roundRect(-.48, -.22, .96, .57, .1); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#dae9e5'; ctx.beginPath(); ctx.moveTo(-.30, -.21); ctx.lineTo(-.16, -.46); ctx.lineTo(.23, -.46); ctx.lineTo(.38, -.21); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#263e39'; for (const cx of [-.29, .30]) { ctx.beginPath(); ctx.ellipse(cx, .32, .13, .17, 0, 0, Math.PI * 2); ctx.fill(); }
    } else if (kind === 'wheelchair') {
      ctx.strokeStyle = '#266c66'; ctx.lineWidth = .085;
      ctx.beginPath(); ctx.arc(-.05, .20, .29, -.5, Math.PI * 1.8); ctx.stroke();
      ctx.beginPath(); ctx.arc(.05, -.39, .09, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.moveTo(.04, -.22); ctx.lineTo(.04, .12); ctx.lineTo(.30, .12); ctx.lineTo(.40, .4); ctx.lineTo(.5, .4); ctx.moveTo(.03, -.06); ctx.lineTo(.26, -.06); ctx.stroke();
    } else if (kind === 'cane') {
      ctx.strokeStyle = '#755d95'; ctx.lineWidth = .06; ctx.beginPath(); ctx.moveTo(-.30, -.43); ctx.lineTo(-.12, -.49); ctx.lineTo(.33, .46); ctx.stroke();
      ctx.fillStyle = '#ba725c'; ctx.beginPath(); ctx.arc(.33, .46, .075, 0, Math.PI * 2); ctx.fill();
    } else {
      ctx.fillStyle = '#bc9071'; ctx.beginPath(); ctx.roundRect(-.38, -.35, .76, .78, .05); ctx.fill(); ctx.stroke();
      ctx.strokeRect(-.19, -.49, .38, .15); ctx.fillStyle = '#263e39'; ctx.fillRect(-.3, .43, .12, .09); ctx.fillRect(.18, .43, .12, .09);
    }
    ctx.restore();
  }
  function backdrop(ctx, w, h, source) {
    const key = sceneKey(source), extra = source.extra;
    ctx.save(); ctx.fillStyle = extra.palette?.background || '#edf3ed'; ctx.fillRect(0, 0, w, h);
    if (key === 'sorting') {
      text(ctx, 'SORTING STATION', .5 * w, .10 * h, .035 * h);
      text(ctx, 'Draw a boundary over either bin', .5 * w, .17 * h, .024 * h);
      for (const sign of extra.signage || []) {
        const [x1, y1, x2, y2] = sign.bounds; ctx.fillStyle = sign.color;
        ctx.beginPath(); ctx.roundRect(x1 * w, y1 * h, (x2 - x1) * w, (y2 - y1) * h, .025 * h); ctx.fill();
        ctx.fillStyle = '#172d2c'; ctx.fillRect((x1 + .03) * w, (y1 + .025) * h, (x2 - x1 - .06) * w, .055 * h);
        text(ctx, sign.icon === 'recycle' ? '♻' : '×', (x1 + x2) / 2 * w, .75 * h, .13 * h, '#f2f5eb');
        text(ctx, sign.label, (x1 + x2) / 2 * w, .84 * h, .027 * h, '#f2f5eb');
      }
    } else if (key === 'crosswalk') {
      ctx.fillStyle = '#566775'; ctx.fillRect(0, .23 * h, w, .63 * h);
      ctx.fillStyle = '#e5e5c8'; for (let y = .26; y < .84; y += .085) ctx.fillRect(.39 * w, y * h, .22 * w, .046 * h);
      ctx.strokeStyle = '#e3cb79'; ctx.lineWidth = .009 * h; ctx.setLineDash([.07 * w, .04 * w]); ctx.beginPath(); ctx.moveTo(0, .53 * h); ctx.lineTo(.35 * w, .53 * h); ctx.moveTo(.65 * w, .53 * h); ctx.lineTo(w, .53 * h); ctx.stroke();
      text(ctx, 'CROSSWALK · IMAGE REGION', .5 * w, .12 * h, .03 * h);
    } else {
      ctx.fillStyle = extra.palette?.floor || '#d8ded9'; ctx.beginPath(); ctx.moveTo(.4 * w, .25 * h); ctx.lineTo(.6 * w, .25 * h); ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = '#f4f5eb'; ctx.lineWidth = .014 * h;
      for (const side of [-1, 1]) { ctx.beginPath(); ctx.moveTo((.5 + side * .1) * w, .25 * h); ctx.lineTo((.5 + side * .45) * w, h); ctx.stroke(); }
      text(ctx, key === 'wheelchair' ? 'MARKED WHEELCHAIR ROUTE' : 'CANE CAMERA · FORWARD VIEW CONCEPT', .5 * w, .12 * h, .031 * h);
      for (const item of extra.context_icons || []) icon(ctx, item.icon, item.x * w, item.y * h, item.scale * w, item.scale * h);
    }
    ctx.restore();
  }
  drawBackdrop = function(ctx, w, h, source) {
    if (!supported.has(sceneKey(source))) return original.drawBackdrop(ctx, w, h, source);
    try { backdrop(ctx, w, h, source); } catch { original.drawBackdrop(ctx, w, h, source); }
  };
  drawSimulation = function(ctx, w, h, timestamp) {
    if (!supported.has(sceneKey(state.source))) return original.drawSimulation(ctx, w, h, timestamp);
    try {
      backdrop(ctx, w, h, state.source);
      for (const item of detectionsAt(timestamp)) {
        const [x1, y1, x2, y2] = item.bbox;
        icon(ctx, item.class_name, (x1 + x2) / 2 * w, (y1 + y2) / 2 * h, (x2 - x1) * w, (y2 - y1) * h);
      }
    } catch { original.drawSimulation(ctx, w, h, timestamp); }
  };

  function populate() {
    const saved = state.config.rules.find(rule => rule.id === state.selectedRule);
    const options = preferenceSource === sourceId() ? preferences[state.selectedRule] || {} : {};
    const ready = !!saved && preferenceSource === sourceId();
    field('scenario-meaning').value = options.meaning || 'none';
    field('scenario-vibrate').checked = options.vibrate === true;
    for (const id of ['scenario-meaning', 'scenario-vibrate', 'scenario-save-actions']) field(id).disabled = !ready;
    field('scenario-action-status').textContent = !saved ? 'Save a rule first to add optional actions.' : !ready ? 'Optional actions are unavailable or loading; core rules still work.' : options.meaning ? 'Optional preferences loaded. Device cues also need the page switch below.' : 'Optional actions are off. Save them after changing a rule or its boundary.';
  }
  async function loadPreferences() {
    const id = sourceId(), token = ++loadToken;
    if (!id) return;
    preferences = {}; preferenceSource = null; populate();
    try {
      const result = await original.api(`api/scenarios/options?source_id=${encodeURIComponent(id)}`);
      if (token !== loadToken || id !== sourceId()) return;
      preferences = result.rules || {}; preferenceSource = id;
    } catch { /* Core settings and playback remain available. */ }
    if (token === loadToken) populate();
  }
  function notes() {
    const panel = field('scenario-notes'); panel.replaceChildren(node('summary', 'About this simulation'));
    const lines = state.source?.extra?.scenario_notes || [];
    for (const line of lines) panel.append(node('p', line));
    panel.hidden = !lines.length;
  }
  function cue(event) {
    const details = event.scenario || {}, box = field('scenario-cue');
    let device = 'Visual cue. Device vibration is off.';
    if (details.vibration_requested && field('scenario-device-cues').checked && document.visibilityState === 'visible') {
      device = 'Vibration is unavailable on this device/browser; visual cue shown.';
      if (typeof navigator.vibrate === 'function') {
        try { device = navigator.vibrate(details.pattern || [180, 100, 180]) ? 'Vibration request accepted; physical delivery is unverified.' : 'Vibration request rejected; visual cue shown.'; }
        catch { device = 'Vibration unavailable; visual cue shown. Incident saved.'; }
      }
    }
    box.replaceChildren(node('strong', details.headline || event.message || 'Spatial event recorded'), node('span', device));
    box.dataset.severity = event.severity || 'info'; box.hidden = false;
  }
  api = async function(path, options) {
    const generation = state.generation, transition = state.transition, session = state.session;
    const result = await original.api(path, options);
    try {
      if (/\/evaluate$/.test(path) && generation === state.generation && transition === state.transition && session === state.session) {
        for (const event of result.events || []) if (event.source_id === sourceId()) cue(event);
      }
    } catch { /* Optional interface/device failures never reject core requests. */ }
    return result;
  };
  selectSource = async function(id) {
    ++loadToken; preferences = {}; preferenceSource = null;
    if (field('scenario-cue')) field('scenario-cue').hidden = true;
    await original.selectSource(id);
    try { notes(); await loadPreferences(); } catch { /* Optional only. */ }
  };
  fillRule = function(rule) {
    original.fillRule(rule);
    try { if (field('scenario-meaning')) populate(); } catch { /* Optional only. */ }
  };
  persistConfig = async function(next) {
    const result = await original.persistConfig(next);
    if (result) setTimeout(() => { void loadPreferences(); }, 0);
    return result;
  };
  renderIncidents = function() {
    original.renderIncidents();
    try {
      const events = state.incidents.filter(event => state.filter === 'all' || event.status === state.filter).slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
      document.querySelectorAll('#incidents tbody tr').forEach((row, index) => {
        const details = events[index]?.scenario;
        if (details) row.children[1].append(node('span', details.headline, 'scenario-event-detail'));
      });
    } catch { /* The original table remains usable. */ }
  };

  function installControls() {
    // Optional presets include sub-second dwell values already supported by
    // the backend. Keep the new page's input step consistent with those values.
    field('rule-dwell').step = '0.1';
    const banner = node('div', 'Optional playground · separate saved data. Choose one of the four new sources, then press Play. Starter boundaries are editable.', 'scenario-banner');
    document.querySelector('.workspace-grid').before(banner);
    const about = node('details', '', 'scenario-notes'); about.id = 'scenario-notes'; about.hidden = true;
    field('source-description').after(about);
    const panel = node('section', '', 'scenario-panel'); panel.append(node('h3', 'Optional event actions'));
    const fields = node('div', '', 'scenario-fields');
    const meaningLabel = node('label', 'Event meaning'), meaning = node('select'); meaning.id = 'scenario-meaning';
    for (const [value, title] of [['none', 'No optional action'], ['recycling', 'Recycling zone placement'], ['trash', 'Trash zone placement'], ['path', 'Protected route occupancy'], ['crosswalk', 'Crosswalk vehicle presence'], ['cane', 'Forward camera region cue']]) {
      const option = node('option', title); option.value = value; meaning.append(option);
    }
    meaningLabel.append(meaning);
    const vibrateLabel = node('label'), vibrate = node('input'); vibrate.type = 'checkbox'; vibrate.id = 'scenario-vibrate';
    vibrateLabel.append(vibrate, document.createTextNode('Request vibration for this rule'));
    fields.append(meaningLabel, vibrateLabel); panel.append(fields);
    const save = node('button', 'Save optional actions', 'button secondary'); save.type = 'button'; save.id = 'scenario-save-actions'; panel.append(save);
    const status = node('p', '', 'scenario-status'); status.id = 'scenario-action-status'; status.setAttribute('aria-live', 'polite'); panel.append(status);
    const deviceLabel = node('label', '', 'scenario-device'), device = node('input'); device.type = 'checkbox'; device.id = 'scenario-device-cues';
    deviceLabel.append(device, document.createTextNode('Enable device cues for this page')); panel.append(deviceLabel);
    panel.append(node('p', 'Visual cues and incident logs work without vibration. Browser support varies; no walking-stick motor is connected.'));
    field('rule-form').after(panel);
    const alert = node('div', '', 'scenario-cue'); alert.id = 'scenario-cue'; alert.hidden = true; alert.setAttribute('role', 'status'); alert.setAttribute('aria-live', 'polite');
    document.querySelector('.monitor-panel').append(alert);
    save.addEventListener('click', async () => {
      const id = sourceId(), rule = state.selectedRule;
      if (!rule || preferenceSource !== id) return;
      const payload = { ...preferences, [rule]: { meaning: meaning.value, vibrate: vibrate.checked, pattern: [180, 100, 180] } };
      save.disabled = true;
      try {
        await original.api('api/scenarios/options', { method: 'PUT', body: JSON.stringify({ source_id: id, rules: payload }) });
        if (sourceId() === id) { await loadPreferences(); status.textContent = 'Optional actions saved.'; }
      } catch { if (sourceId() === id) { status.textContent = 'Optional actions could not be saved. Core rules still work.'; save.disabled = false; } }
    });
    device.addEventListener('change', () => { if (!device.checked && typeof navigator.vibrate === 'function') { try { navigator.vibrate(0); } catch {} } });
    const location = field('catalog-location');
    if (location) {
      const places = node('div', '', 'scenario-places');
      for (const name of ['warehouse3', 'toronto', 'nashville', 'indoor']) {
        const button = node('button', name); button.type = 'button'; button.addEventListener('click', () => { location.value = name; location.focus(); }); places.append(button);
      }
      location.closest('form').before(places);
    }
    populate(); notes(); if (sourceId()) void loadPreferences();
    if (state.sources.length) { renderSources(); draw(); renderIncidents(); }
  }
  try { installControls(); } catch { /* A missing optional DOM anchor leaves the original app running. */ }
})();
