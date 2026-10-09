'use strict';

// All URLs are relative so the workspace also works behind VAST's /app proxy.
const $ = (id) => document.getElementById(id);
const state = {
  sources: [], source: null, config: { zones: [], rules: [] }, frames: [], classes: [],
  incidents: [], session: null, generation: 0, time: 0, duration: 0,
  playing: false, lastTick: 0, lastEvaluation: 0, evaluation: null,
  drawing: false, points: [], cursor: [.5, .5], keyboardCursor: false,
  selectedRule: null, filter: 'all', saving: false, loading: false,
  audio: null, noticeTimer: null, transition: 0, seekQueue: Promise.resolve(), seekToken: null, starting: false,
};
const palette = ['#d7ec8a', '#83cdbb', '#ead298', '#bda9e0', '#9fc5e1'];
const clamp = (n, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, n));
const uid = (prefix) => `${prefix}_${globalThis.crypto?.randomUUID?.() || `${Date.now()}_${Math.random().toString(36).slice(2)}`}`;
const timecode = (seconds) => { const s = Math.max(0, Math.floor(Number(seconds) || 0)); return `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`; };
const isSimulation = () => state.source?.kind === 'simulation';
const sourceId = () => state.source?.id;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function notice(message, error = false) {
  clearTimeout(state.noticeTimer);
  $('notice').textContent = message;
  $('notice').className = `notice${error ? ' error' : ''}`;
  $('notice').hidden = false;
  if (!error) state.noticeTimer = setTimeout(() => { $('notice').hidden = true; }, 6500);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof data === 'object' ? data.detail || data.message || data.error : data;
    const message = typeof detail === 'string' ? detail : JSON.stringify(detail);
    throw new Error(message || `Request failed (${response.status}).`);
  }
  return data;
}

function setPlayback(playing) {
  state.playing = playing;
  state.lastTick = performance.now();
  $('play-button').textContent = playing ? 'Ⅱ' : '▶';
  $('play-button').classList.toggle('playing', playing);
  $('play-button').setAttribute('aria-label', playing ? 'Pause source' : 'Play source');
  $('playback-state').textContent = playing ? 'PLAYING' : 'PAUSED';
  if (!playing) $('video').pause();
}

function showTime() {
  $('current-time').textContent = timecode(state.time);
  $('timeline').value = state.time;
}

function setAvailable(available) {
  available = available && !state.seekToken;
  ['play-button', 'reset-button', 'timeline', 'draw-button', 'new-rule'].forEach((id) => { $(id).disabled = !available; });
  $('save-rule').disabled = !available || !state.config.zones.length || state.saving;
}

async function initialize() {
  try {
    const [status, result] = await Promise.all([api('api/status'), api('api/sources')]);
    $('connection-dot').className = 'status-dot ready';
    $('connection-label').textContent = status.vast_configured ? 'VAST configured' : 'Local workspace';
    $('vast-help').textContent = status.vast_configured
      ? 'Import recorded segments and their stored YOLO detections from your team’s VAST archive. Browse to verify the connection.'
      : 'Run on your team VM with INGRESS_URL, VSS_USERNAME and VSS_PASSWORD configured on the server.';
    state.sources = result.sources || [];
    renderSources();
    if (state.sources.length) await selectSource(state.sources[0].id);
    else $('monitor-heading').textContent = 'Add a source to get started';
  } catch (error) {
    $('connection-dot').className = 'status-dot error';
    $('connection-label').textContent = 'Connection unavailable';
    $('monitor-heading').textContent = 'Workspace unavailable';
    $('sources').replaceChildren(element('p', 'empty', 'The source catalog could not be loaded.'));
    notice(`Could not connect to the backend. ${error.message}`, true);
  }
}

function renderSources() {
  $('source-count').textContent = state.sources.length;
  $('sources').replaceChildren();
  for (const source of state.sources) {
    const button = element('button', `source-card${source.id === sourceId() ? ' active' : ''}`);
    button.type = 'button';
    button.setAttribute('aria-pressed', String(source.id === sourceId()));
    const thumb = element('div', 'source-thumb');
    const canvas = document.createElement('canvas');
    canvas.width = 320; canvas.height = 180; canvas.setAttribute('aria-hidden', 'true');
    drawBackdrop(canvas.getContext('2d'), canvas.width, canvas.height, source);
    thumb.append(canvas, element('span', '', source.kind === 'simulation' ? 'SIMULATED SCENE' : 'VIDEO SOURCE'));
    button.append(thumb, element('h3', '', source.name), element('p', '', `${source.kind === 'simulation' ? 'Generated detections' : 'Recorded video'} · ${timecode(source.duration)}`));
    button.addEventListener('click', () => selectSource(source.id));
    $('sources').append(button);
  }
}

async function selectSource(id) {
  const source = state.sources.find((item) => item.id === id);
  if (!source || (state.loading && sourceId() === id)) return;
  const generation = ++state.generation;
  state.transition++;
  state.loading = true;
  setPlayback(false);
  cancelDrawing();
  state.source = source;
  state.session = null;
  state.seekToken = null;
  state.starting = false;
  state.time = 0;
  state.frames = [];
  state.classes = [];
  state.duration = Number(source.duration || 0);
  $('duration').textContent = timecode(state.duration);
  state.incidents = [];
  state.config = { source_id: id, zones: [], rules: [] };
  state.selectedRule = null;
  setAvailable(false);
  renderSources();
  renderIncidents();
  renderZones();
  renderRules();
  $('monitor-heading').textContent = source.name;
  $('source-kind').textContent = source.kind === 'simulation' ? 'SIMULATED SOURCE' : source.kind === 'vast' ? 'VAST VIDEO SOURCE' : 'UPLOADED SOURCE';
  $('mode-badge').textContent = source.kind === 'simulation' ? 'SIMULATION' : 'RECORDING';
  $('mode-badge').className = `badge ${source.kind === 'simulation' ? 'simulation' : 'recording'}`;
  $('source-description').textContent = source.description || 'Zones and rules are saved separately for this source.';
  $('stage-caption').textContent = source.kind === 'simulation' ? 'GENERATED SCENE · NO CAMERA FOOTAGE' : 'RECORDED VIDEO · DETECTION OVERLAY';
  $('stage-error').hidden = true;
  const video = $('video');
  video.removeAttribute('src');
  video.load();
  video.hidden = isSimulation();
  $('scene').hidden = !isSimulation();
  const width = source.width || 1280, height = source.height || 720;
  $('stage').style.aspectRatio = `${width} / ${height}`;
  for (const id of ['scene', 'overlay']) { $(id).width = width; $(id).height = height; }
  draw();
  showTime();
  try {
    const [detections, config, session, incidentResult] = await Promise.all([
      api(`api/sources/${encodeURIComponent(id)}/detections`),
      api(`api/config?source_id=${encodeURIComponent(id)}`),
      api('api/sessions', { method: 'POST', body: JSON.stringify({ source_id: id }) }),
      api(`api/incidents?source_id=${encodeURIComponent(id)}`),
    ]);
    if (generation !== state.generation) return;
    state.frames = (detections.frames || []).slice().sort((a, b) => a.timestamp - b.timestamp);
    state.classes = [...new Set(state.frames.flatMap((frame) => (frame.detections || []).map((detection) => detection.class_name)))].filter(Boolean).sort();
    state.config = { ...config, zones: config.zones || [], rules: config.rules || [] };
    state.session = session.id;
    state.duration = Number(detections.duration || source.duration || state.frames.at(-1)?.timestamp || 30);
    state.incidents = incidentResult.incidents || [];
    $('timeline').max = state.duration;
    $('duration').textContent = timecode(state.duration);
    if (!isSimulation()) {
      if (!source.video_url) throw new Error('This source has no playable video URL.');
      video.src = source.video_url;
    }
    renderZones();
    renderRules();
    renderClassChips();
    if (state.config.rules.length) editRule(state.config.rules[0].id);
    else clearRule();
    renderIncidents();
    setAvailable(true);
    draw();
  } catch (error) {
    if (generation !== state.generation) return;
    notice(`Could not load ${source.name}. ${error.message}`, true);
    $('stage-error').hidden = false;
    $('stage-error').textContent = error.message;
  } finally {
    if (generation === state.generation) state.loading = false;
  }
}

// Binary search avoids scanning a large detection file on each animation frame.
function detectionsAt(timestamp) {
  let low = 0, high = state.frames.length - 1, result = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (Number(state.frames[mid].timestamp) <= timestamp + 0.0001) { result = mid; low = mid + 1; }
    else high = mid - 1;
  }
  return result >= 0 ? state.frames[result].detections || [] : [];
}

function drawBackdrop(ctx, width, height, source) {
  ctx.save(); ctx.scale(width / 1280, height / 720);
  const bottleScene = /bottle|waste|lobby|litter/i.test(`${source?.id} ${source?.name}`);
  const gradient = ctx.createLinearGradient(0, 0, 0, 720);
  gradient.addColorStop(0, '#849184'); gradient.addColorStop(1, '#b3bbaa');
  ctx.fillStyle = gradient; ctx.fillRect(0, 0, 1280, 720);
  ctx.fillStyle = '#6c7c70'; ctx.fillRect(0, 0, 1280, 210);
  ctx.fillStyle = '#7b8c7c'; ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(250, 180); ctx.lineTo(250, 310); ctx.lineTo(0, 720); ctx.fill();
  ctx.fillStyle = '#879780'; ctx.beginPath(); ctx.moveTo(1280, 0); ctx.lineTo(1030, 180); ctx.lineTo(1030, 310); ctx.lineTo(1280, 720); ctx.fill();
  ctx.fillStyle = '#576c5e'; ctx.fillRect(476, 100, 310, 160);
  ctx.fillStyle = '#b1bca4'; ctx.fillRect(486, 109, 290, 140);
  ctx.strokeStyle = '#87977b'; ctx.lineWidth = 3;
  for (let y = 117; y < 250; y += 20) { ctx.beginPath(); ctx.moveTo(486, y); ctx.lineTo(776, y); ctx.stroke(); }
  ctx.fillStyle = '#d4dbc4'; ctx.fillRect(591, 76, 79, 16); ctx.fillStyle = '#798b70'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center'; ctx.fillText(bottleScene ? 'COMMON AREA' : 'LOADING BAY', 630, 88);
  ctx.strokeStyle = '#70877545'; ctx.lineWidth = 2;
  for (let x = -1000; x < 2600; x += 170) { ctx.beginPath(); ctx.moveTo(640 + (x - 640) * .2, 235); ctx.lineTo(x, 720); ctx.stroke(); }
  for (const y of [280, 345, 430, 545, 685]) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(1280, y); ctx.stroke(); }
  // Schematic fixtures provide spatial context; all moving objects come from detections.
  if (!bottleScene) {
    for (const side of [0, 1]) {
      ctx.save(); if (side) { ctx.translate(1280, 0); ctx.scale(-1, 1); }
      for (let i = 0; i < 3; i++) {
        const x = 25 + i * 73, y = 220 - i * 23, w = 145 - i * 23, h = 270 - i * 39;
        ctx.fillStyle = '#536f604a'; ctx.fillRect(x + 10, y + h, w, 15);
        ctx.fillStyle = '#627969'; ctx.fillRect(x, y, 7, h); ctx.fillRect(x + w, y, 7, h);
        for (let j = 0; j < 3; j++) {
          const sy = y + j * (h / 3);
          ctx.fillStyle = '#aab28e'; ctx.fillRect(x + 10, sy + 8, w * .47, h / 3 - 12);
          ctx.fillStyle = '#bbc19e'; ctx.fillRect(x + w * .55, sy + 16, w * .41, h / 3 - 20);
          ctx.fillStyle = '#6c8070'; ctx.fillRect(x, sy + h / 3, w + 7, 7);
          ctx.strokeStyle = '#8c9a70'; ctx.lineWidth = 2; ctx.strokeRect(x + 10, sy + 8, w * .47, h / 3 - 12);
        }
      }
      ctx.restore();
    }
    ctx.strokeStyle = '#d3d9a777'; ctx.lineWidth = 6; ctx.setLineDash([25, 22]);
    ctx.beginPath(); ctx.moveTo(426, 285); ctx.lineTo(275, 720); ctx.moveTo(857, 285); ctx.lineTo(1007, 720); ctx.stroke(); ctx.setLineDash([]);
  } else {
    ctx.fillStyle = '#587264'; ctx.fillRect(150, 324, 235, 22); ctx.fillRect(166, 345, 14, 84); ctx.fillRect(350, 345, 14, 84);
    ctx.fillStyle = '#7c927c'; ctx.fillRect(150, 275, 235, 44);
    ctx.fillStyle = '#657d65'; ctx.beginPath(); ctx.ellipse(1070, 379, 46, 15, 0, 0, Math.PI * 2); ctx.fill(); ctx.fillRect(1024, 317, 92, 63);
    ctx.fillStyle = '#a4bda0'; ctx.beginPath(); ctx.ellipse(1070, 315, 47, 13, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#5d795e'; ctx.beginPath(); ctx.ellipse(1112, 172, 63, 66, -.3, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.ellipse(1040, 209, 54, 63, .4, 0, Math.PI * 2); ctx.fill();
    // The general bin's footprint matches the default demo zone. These large
    // foreground fixtures make the simulated bottle-to-bin relationship visible.
    for (const [x, y, w, h, color, label] of [[225, 370, 340, 295, '#668d89', 'RECYCLING'], [704, 338, 422, 338, '#818a68', 'GENERAL WASTE']]) {
      ctx.fillStyle = '#3e625245'; ctx.fillRect(x + 15, y + h - 4, w, 20);
      ctx.fillStyle = color; ctx.fillRect(x, y + 36, w, h - 36);
      ctx.fillStyle = '#526b56'; ctx.fillRect(x + w - 26, y + 36, 26, h - 36);
      ctx.fillStyle = '#b3bda0'; ctx.fillRect(x - 6, y, w + 12, 54);
      ctx.fillStyle = '#314d41'; ctx.fillRect(x + 18, y + 15, w - 36, h - 79);
      ctx.fillStyle = '#486452'; ctx.fillRect(x + 30, y + 31, w - 60, h - 108);
      ctx.fillStyle = '#dce8ce'; ctx.font = '600 24px sans-serif'; ctx.textAlign = 'center'; ctx.fillText(label, x + w / 2, y + h - 24);
      ctx.fillStyle = '#aec4a26b'; ctx.font = '48px sans-serif'; ctx.fillText(label === 'RECYCLING' ? '↻' : '↓', x + w / 2, y + h * .48);
    }
  }
  ctx.fillStyle = '#233e3722'; ctx.fillRect(0, 0, 1280, 720);
  ctx.restore();
}

function drawSimulation(ctx, width, height, timestamp) {
  drawBackdrop(ctx, width, height, state.source);
  for (const detection of detectionsAt(timestamp)) {
    const [x1, y1, x2, y2] = detection.bbox || [0, 0, 0, 0];
    const x = x1 * width, y = y1 * height, w = (x2 - x1) * width, h = (y2 - y1) * height;
    ctx.save();
    ctx.fillStyle = '#253c3445'; ctx.beginPath(); ctx.ellipse(x + w / 2, y + h, w * .58, h * .055, 0, 0, Math.PI * 2); ctx.fill();
    if (['person', 'pedestrian'].includes(detection.class_name)) {
      ctx.fillStyle = '#d1b291'; ctx.beginPath(); ctx.ellipse(x + w / 2, y + h * .13, w * .16, h * .115, 0, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#bed088'; ctx.beginPath(); ctx.moveTo(x + w * .26, y + h * .29); ctx.lineTo(x + w * .73, y + h * .29); ctx.lineTo(x + w * .85, y + h * .59); ctx.lineTo(x + w * .61, y + h * .63); ctx.lineTo(x + w * .32, y + h * .59); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = '#b3c280'; ctx.lineWidth = Math.max(3, w * .16); ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(x + w * .29, y + h * .35); ctx.lineTo(x + w * .13, y + h * .61); ctx.moveTo(x + w * .72, y + h * .36); ctx.lineTo(x + w * .91, y + h * .62); ctx.stroke();
      ctx.strokeStyle = '#3f5b50'; ctx.lineWidth = w * .2; ctx.beginPath(); ctx.moveTo(x + w * .43, y + h * .62); ctx.lineTo(x + w * .32, y + h * .94); ctx.moveTo(x + w * .62, y + h * .62); ctx.lineTo(x + w * .73, y + h * .94); ctx.stroke();
    } else if (detection.class_name === 'bottle') {
      ctx.fillStyle = '#a6d5bc'; ctx.fillRect(x + w * .32, y + h * .04, w * .34, h * .2); ctx.fillStyle = '#72b69e'; ctx.fillRect(x + w * .15, y + h * .25, w * .7, h * .72); ctx.fillStyle = '#d8e4c9'; ctx.fillRect(x + w * .15, y + h * .51, w * .7, h * .25);
    } else {
      ctx.fillStyle = /car|truck|bus|forklift/.test(detection.class_name) ? '#c5b783' : '#9eae8b';
      ctx.fillRect(x + w * .07, y + h * .18, w * .86, h * .7); ctx.fillStyle = '#78958a'; ctx.fillRect(x + w * .17, y + h * .23, w * .64, h * .32); ctx.fillStyle = '#344e40'; ctx.fillRect(x + w * .09, y + h * .82, w * .19, h * .14); ctx.fillRect(x + w * .73, y + h * .82, w * .19, h * .14);
    }
    ctx.restore();
  }
}

function drawOverlay(ctx, width, height, timestamp, includeDraft = true) {
  ctx.clearRect(0, 0, width, height);
  // Keep controls and labels readable in CSS pixels while geometry stays intrinsic.
  const scale = width / Math.max(320, $('stage').clientWidth || width);
  ctx.save(); ctx.lineWidth = Math.max(1.2, 2 * scale);
  for (let i = 0; i < state.config.zones.length; i++) {
    const zone = state.config.zones[i];
    if (!zone.points?.length) continue;
    ctx.beginPath(); zone.points.forEach(([x, y], index) => index ? ctx.lineTo(x * width, y * height) : ctx.moveTo(x * width, y * height)); ctx.closePath();
    ctx.fillStyle = `${palette[i % palette.length]}28`; ctx.fill(); ctx.strokeStyle = palette[i % palette.length]; ctx.setLineDash([8 * scale, 5 * scale]); ctx.stroke(); ctx.setLineDash([]);
    const px = Math.min(...zone.points.map((p) => p[0])) * width, py = Math.min(...zone.points.map((p) => p[1])) * height;
    ctx.font = `500 ${10 * scale}px sans-serif`;
    const label = String(zone.name || 'Zone'), tw = ctx.measureText(label).width;
    ctx.fillStyle = palette[i % palette.length]; ctx.fillRect(clamp(px, 0, width - tw - 12 * scale), Math.max(0, py - 22 * scale), tw + 12 * scale, 19 * scale);
    ctx.fillStyle = '#34503a'; ctx.fillText(label, clamp(px, 0, width - tw - 12 * scale) + 6 * scale, Math.max(0, py - 22 * scale) + 13 * scale);
  }
  for (const detection of detectionsAt(timestamp)) {
    if (!Array.isArray(detection.bbox) || detection.bbox.length !== 4) continue;
    const [x1, y1, x2, y2] = detection.bbox;
    const x = clamp(x1) * width, y = clamp(y1) * height, w = (clamp(x2) - clamp(x1)) * width, h = (clamp(y2) - clamp(y1)) * height;
    ctx.strokeStyle = '#abe4cd'; ctx.lineWidth = Math.max(1, 1.6 * scale); ctx.strokeRect(x, y, w, h);
    const label = `${detection.class_name} ${Math.round((Number(detection.confidence) || 0) * 100)}%`;
    ctx.font = `${9 * scale}px sans-serif`;
    const tw = ctx.measureText(label).width, tx = clamp(x, 0, width - tw - 10 * scale), ty = Math.max(14 * scale, y - 3 * scale);
    ctx.fillStyle = '#234e42e8'; ctx.fillRect(tx, ty - 13 * scale, tw + 10 * scale, 17 * scale); ctx.fillStyle = '#ddf5e1'; ctx.fillText(label, tx + 5 * scale, ty - 1 * scale);
  }
  if (includeDraft && state.drawing) {
    ctx.strokeStyle = '#eefaa1'; ctx.lineWidth = Math.max(2, 3 * scale); ctx.fillStyle = '#e8f69730';
    ctx.beginPath(); state.points.forEach(([x, y], i) => i ? ctx.lineTo(x * width, y * height) : ctx.moveTo(x * width, y * height));
    if (state.points.length) ctx.lineTo(state.cursor[0] * width, state.cursor[1] * height);
    if (state.points.length > 1) ctx.fill(); ctx.stroke();
    for (const [x, y] of state.points) { ctx.beginPath(); ctx.arc(x * width, y * height, 5 * scale, 0, Math.PI * 2); ctx.fillStyle = '#efffc0'; ctx.fill(); }
    if (state.keyboardCursor) {
      const x = state.cursor[0] * width, y = state.cursor[1] * height;
      ctx.strokeStyle = '#fff'; ctx.beginPath(); ctx.moveTo(x - 12 * scale, y); ctx.lineTo(x + 12 * scale, y); ctx.moveTo(x, y - 12 * scale); ctx.lineTo(x, y + 12 * scale); ctx.stroke();
    }
  }
  ctx.restore();
}

function draw() {
  const canvas = $('scene');
  if (isSimulation()) drawSimulation(canvas.getContext('2d'), canvas.width, canvas.height, state.time);
  const overlay = $('overlay');
  drawOverlay(overlay.getContext('2d'), overlay.width, overlay.height, state.time);
  const count = detectionsAt(state.time).length;
  $('detection-count').textContent = `${count} object${count === 1 ? '' : 's'}`;
}

async function evaluate(options = {}) {
  if (!state.session || state.evaluation) return;
  const generation = state.generation, transition = state.transition, session = state.session, timestamp = state.time;
  const pending = (async () => {
    const result = await api(`api/sessions/${encodeURIComponent(session)}/evaluate`, { method: 'POST', body: JSON.stringify({ timestamp, ...options }) });
    if (generation !== state.generation || transition !== state.transition) return;
    const events = result.events || [];
    if (events.length) {
      if (events.some((event) => event.actions?.includes('sound'))) soundAlert();
      await Promise.allSettled(events.map((event) => captureEvidence(event, generation)));
      if (generation === state.generation) await refreshIncidents(generation);
    }
  })();
  state.evaluation = pending;
  try { await pending; }
  catch (error) { if (generation === state.generation) { setPlayback(false); notice(`Evaluation paused. ${error.message}`, true); } }
  finally { if (state.evaluation === pending) state.evaluation = null; }
}

async function seek(timestamp, reset = false) {
  if (!state.session || state.loading) return;
  setPlayback(false);
  const generation = state.generation, transition = ++state.transition, session = state.session;
  const target = clamp(Number(timestamp), 0, state.duration);
  const token = { generation, transition }, previous = state.seekQueue;
  state.seekToken = token; setAvailable(false);
  const operation = (async () => {
    await previous.catch(() => {});
    if (generation !== state.generation || transition !== state.transition) return;
    if (state.evaluation) { try { await state.evaluation; } catch { /* Handled by evaluate. */ } }
    if (generation !== state.generation || transition !== state.transition) return;
    if (reset) await api(`api/sessions/${encodeURIComponent(session)}/reset`, { method: 'POST', body: '{}' });
    if (generation !== state.generation || transition !== state.transition) return;
    state.time = target;
    if (!isSimulation()) $('video').currentTime = target;
    showTime(); draw();
    await evaluate({ seek: true });
    if (generation === state.generation && transition === state.transition) await refreshIncidents(generation);
  })();
  state.seekQueue = operation;
  try { await operation; }
  catch (error) { if (generation === state.generation) notice(error.message, true); }
  finally {
    if (state.seekToken === token) { state.seekToken = null; setAvailable(!!state.session && !state.loading); }
  }
}

async function togglePlay() {
  if (!state.session || state.loading || state.starting || state.seekToken) return;
  if (state.playing) { setPlayback(false); return; }
  const generation = state.generation;
  if (state.drawing) cancelDrawing();
  state.starting = true;
  try {
    if (state.time >= state.duration - .05) await seek(0, true);
    if (generation !== state.generation || state.loading) return;
    if (!isSimulation()) await $('video').play();
    // Browsers permit audio after this explicit user gesture.
    try {
      if (globalThis.AudioContext || globalThis.webkitAudioContext) {
        state.audio ||= new (globalThis.AudioContext || globalThis.webkitAudioContext)();
        // Some browsers/audio devices leave resume() pending. Alerts are optional;
        // advancing video and evaluating spatial rules must never wait for audio.
        if (state.audio.state === 'suspended') void state.audio.resume().catch(() => {});
      }
    } catch { /* Audio availability must not prevent playback and event logging. */ }
    if (generation !== state.generation || state.loading) return;
    setPlayback(true); state.lastEvaluation = 0;
  } catch (error) { if (generation === state.generation) { setPlayback(false); notice(`Playback could not start. ${error.message}`, true); } }
  finally { if (generation === state.generation) state.starting = false; }
}

function tick(now) {
  if (state.playing && state.source) {
    // An animation frame's shared timestamp can precede a click handler's
    // performance.now(). Never let that first frame send a negative timestamp.
    const elapsed = clamp((now - state.lastTick) / 1000, 0, .2);
    state.time = isSimulation() ? clamp(state.time + elapsed, 0, state.duration) : clamp($('video').currentTime, 0, state.duration);
    showTime(); draw();
    if (now - state.lastEvaluation >= 250 && !state.evaluation) { state.lastEvaluation = now; void evaluate(); }
    if (state.time >= state.duration) {
      setPlayback(false);
      const pending = state.evaluation;
      if (pending) pending.finally(() => { if (!state.playing && state.time >= state.duration) void evaluate(); }).catch(() => {});
      else void evaluate();
    }
  }
  state.lastTick = now;
  requestAnimationFrame(tick);
}

function soundAlert() {
  if (!state.audio || state.audio.state !== 'running') return;
  const oscillator = state.audio.createOscillator(), gain = state.audio.createGain(), now = state.audio.currentTime;
  oscillator.connect(gain); gain.connect(state.audio.destination); oscillator.type = 'sine'; oscillator.frequency.setValueAtTime(660, now); oscillator.frequency.setValueAtTime(880, now + .12); gain.gain.setValueAtTime(.035, now); gain.gain.exponentialRampToValueAtTime(.001, now + .35); oscillator.start(now); oscillator.stop(now + .36);
}

function beginDrawing() {
  if (!state.source) return;
  setPlayback(false); state.drawing = true; state.points = []; state.cursor = [.5, .5]; state.keyboardCursor = true;
  $('zone-name').value = `Zone ${state.config.zones.length + 1}`;
  $('draw-toolbar').hidden = false; $('stage').classList.add('drawing'); $('draw-button').disabled = true;
  updatePointCount(); $('overlay').focus(); draw();
}

function cancelDrawing() {
  state.drawing = false; state.points = []; state.keyboardCursor = false;
  $('draw-toolbar').hidden = true; $('stage').classList.remove('drawing'); $('draw-button').disabled = !state.session;
  draw();
}

function updatePointCount() {
  $('draw-count').textContent = `${state.points.length} point${state.points.length === 1 ? '' : 's'}`;
  $('finish-zone').disabled = state.points.length < 3 || state.saving;
  $('undo-point').disabled = state.points.length === 0;
}

async function finishZone() {
  if (state.points.length < 3 || state.saving) return;
  const points = state.points.map((point) => [...point]);
  const area = Math.abs(points.reduce((total, point, i) => { const next = points[(i + 1) % points.length]; return total + point[0] * next[1] - next[0] * point[1]; }, 0)) / 2;
  if (area < .0001) { notice('This zone is too small. Mark a visible area with at least three distinct corners.', true); return; }
  const zone = { id: uid('zone'), name: $('zone-name').value.trim() || `Zone ${state.config.zones.length + 1}`, points };
  const next = structuredClone(state.config); next.zones.push(zone);
  if (await persistConfig(next)) { cancelDrawing(); $('rule-zone').value = zone.id; notice(`“${zone.name}” saved. Choose the objects and condition for this zone.`); }
}

function renderZones() {
  const selectedZone = $('rule-zone').value;
  $('zone-list').replaceChildren(); $('rule-zone').replaceChildren();
  if (!state.config.zones.length) {
    $('zone-list').append(element('p', 'empty', 'Draw an area on the source to create your first zone.'));
    const option = element('option', '', 'Draw a zone first'); option.value = ''; $('rule-zone').append(option);
  }
  state.config.zones.forEach((zone, index) => {
    const chip = element('div', 'zone-chip'); const swatch = element('span', 'swatch'); swatch.style.background = palette[index % palette.length];
    const remove = element('button', 'icon-button', '×'); remove.type = 'button'; remove.setAttribute('aria-label', `Delete zone ${zone.name} and its rules`); remove.title = 'Delete zone and its rules';
    remove.addEventListener('click', async () => {
      const next = structuredClone(state.config); next.zones = next.zones.filter((item) => item.id !== zone.id); next.rules = next.rules.filter((rule) => rule.zone_id !== zone.id);
      if (await persistConfig(next)) { if (!next.rules.some((rule) => rule.id === state.selectedRule)) clearRule(); notice(`Deleted “${zone.name}” and its linked rules.`); }
    });
    chip.append(swatch, element('span', '', zone.name), remove); $('zone-list').append(chip);
    const option = element('option', '', zone.name); option.value = zone.id; $('rule-zone').append(option);
  });
  if (state.config.zones.some((zone) => zone.id === selectedZone)) $('rule-zone').value = selectedZone;
  $('save-rule').disabled = !state.config.zones.length || !state.session || state.saving;
}

async function persistConfig(next) {
  if (state.saving || !state.source) return false;
  setPlayback(false); state.saving = true; $('save-state').textContent = 'Saving…'; $('save-rule').disabled = true;
  const generation = state.generation, source = sourceId();
  try {
    if (state.evaluation) { try { await state.evaluation; } catch { /* Evaluation reports its own errors. */ } }
    if (generation !== state.generation) return false;
    const result = await api('api/config', { method: 'PUT', body: JSON.stringify({ ...next, source_id: source }) });
    if (generation !== state.generation) return false;
    state.config = { ...result, zones: result.zones || [], rules: result.rules || [] };
    // A fresh evaluation session guarantees timers use the newly saved rules.
    const session = await api('api/sessions', { method: 'POST', body: JSON.stringify({ source_id: source }) });
    if (generation !== state.generation) return false;
    state.session = session.id; state.transition++;
    await evaluate({ seek: true });
    renderZones(); renderRules(); draw();
    $('save-state').textContent = 'Saved to this source · rule timers reset';
    return true;
  } catch (error) { $('save-state').textContent = 'Save failed'; notice(`Could not save configuration. ${error.message}`, true); return false; }
  finally { state.saving = false; setAvailable(!!state.session && !state.loading); updatePointCount(); }
}

function renderRules() {
  $('rule-list').replaceChildren();
  for (const rule of state.config.rules) {
    const button = element('button', `rule-pill${rule.id === state.selectedRule ? ' active' : ''}${rule.enabled === false ? ' disabled' : ''}`, rule.name);
    button.type = 'button'; button.setAttribute('aria-pressed', String(rule.id === state.selectedRule)); button.addEventListener('click', () => editRule(rule.id)); $('rule-list').append(button);
  }
}

function availableClasses() {
  return state.classes;
}

function selectedClasses() { return [...new Set($('rule-classes').value.split(',').map((name) => name.trim().toLowerCase()).filter(Boolean))]; }

function renderClassChips() {
  $('class-chips').replaceChildren();
  const selected = selectedClasses();
  for (const name of availableClasses()) {
    const button = element('button', `class-chip${selected.includes(name) ? ' selected' : ''}`, name); button.type = 'button'; button.setAttribute('aria-pressed', String(selected.includes(name)));
    button.addEventListener('click', () => { const current = selectedClasses(); $('rule-classes').value = (current.includes(name) ? current.filter((item) => item !== name) : [...current, name]).join(', '); renderClassChips(); });
    $('class-chips').append(button);
  }
}

function formDependencies() {
  $('confidence-value').value = `${Math.round(Number($('rule-confidence').value) * 100)}%`;
  $('rule-dwell').disabled = $('rule-condition').value === 'enter';
  $('overlap-label').hidden = $('rule-anchor').value !== 'overlap';
}

function fillRule(rule) {
  $('rule-name').value = rule.name || '';
  if (rule.zone_id) $('rule-zone').value = rule.zone_id;
  $('rule-classes').value = (rule.classes || []).join(', ');
  $('rule-condition').value = rule.condition || 'dwell'; $('rule-dwell').value = rule.dwell_seconds ?? 3;
  $('rule-confidence').value = rule.min_confidence ?? .5; $('rule-cooldown').value = rule.cooldown_seconds ?? 10;
  $('rule-anchor').value = rule.anchor || 'bottom_center'; $('rule-overlap').value = (rule.min_overlap ?? .2) * 100;
  $('rule-severity').value = rule.severity || 'warning'; $('rule-enabled').checked = rule.enabled !== false; $('rule-sound').checked = (rule.actions || []).includes('sound');
  $('delete-rule').hidden = !state.selectedRule;
  $('save-state').textContent = '';
  $('rule-context').textContent = (rule.classes || []).includes('bottle') ? 'A bottle detection is a candidate for review. The rule does not determine whether an object is waste.' : 'Rules use detected classes and geometry. Review the evidence before acting.';
  formDependencies(); renderClassChips(); renderRules();
}

function editRule(id) {
  const rule = state.config.rules.find((item) => item.id === id);
  if (!rule) return;
  state.selectedRule = id; fillRule(rule);
}

function clearRule() {
  state.selectedRule = null;
  fillRule({ name: '', classes: [], condition: 'dwell', dwell_seconds: 3, enabled: true, severity: 'warning' });
}

function applyPreset(name) {
  state.selectedRule = null;
  if (name === 'waste') fillRule({ name: 'Bottle left in zone', classes: ['bottle'], condition: 'dwell', dwell_seconds: 5, anchor: 'center', severity: 'info', enabled: true });
  else fillRule({ name: 'Access route occupied', classes: ['person', 'bicycle', 'car', 'truck'].filter((name) => availableClasses().includes(name)), condition: 'dwell', dwell_seconds: 3, anchor: 'bottom_center', severity: 'warning', enabled: true });
  if (!$('rule-classes').value) $('rule-classes').value = 'person';
  renderClassChips();
  $('rule-context').textContent = name === 'waste' ? 'Review persistent bottle detections as possible waste. A bottle may still belong to someone.' : 'Flags sustained occupancy of your access zone. A person detection alone cannot establish an accessibility violation.';
}

async function saveRule(event) {
  event.preventDefault();
  if (!state.source || !state.config.zones.length || state.saving) return;
  const classes = selectedClasses();
  if (!classes.length) { notice('Add at least one object class to watch for.', true); return; }
  const rule = {
    id: state.selectedRule || uid('rule'), name: $('rule-name').value.trim(), zone_id: $('rule-zone').value,
    classes, condition: $('rule-condition').value, dwell_seconds: Number($('rule-dwell').value),
    cooldown_seconds: Number($('rule-cooldown').value), min_confidence: Number($('rule-confidence').value),
    anchor: $('rule-anchor').value, min_overlap: Number($('rule-overlap').value) / 100,
    enabled: $('rule-enabled').checked, actions: $('rule-sound').checked ? ['log', 'sound'] : ['log'], severity: $('rule-severity').value,
  };
  if (!rule.name) { notice('Give this rule a name.', true); return; }
  const next = structuredClone(state.config), index = next.rules.findIndex((item) => item.id === rule.id);
  if (index >= 0) next.rules[index] = rule; else next.rules.push(rule);
  if (await persistConfig(next)) { state.selectedRule = rule.id; renderRules(); $('delete-rule').hidden = false; notice(`“${rule.name}” saved. Play the source to evaluate it.`); }
}

async function refreshIncidents(generation = state.generation) {
  const result = await api(`api/incidents?source_id=${encodeURIComponent(sourceId())}`);
  if (generation !== state.generation) return;
  state.incidents = result.incidents || []; renderIncidents();
}

async function captureEvidence(event, generation) {
  if (!event.id || generation !== state.generation || !Number.isFinite(Number(event.timestamp))) return;
  const timestamp = Number(event.timestamp);
  if (!isSimulation() && (Math.abs($('video').currentTime - timestamp) > .5 || $('video').readyState < 2)) return;
  const canvas = document.createElement('canvas'); canvas.width = $('overlay').width; canvas.height = $('overlay').height;
  const ctx = canvas.getContext('2d');
  if (isSimulation()) drawSimulation(ctx, canvas.width, canvas.height, timestamp);
  else ctx.drawImage($('video'), 0, 0, canvas.width, canvas.height);
  const overlay = document.createElement('canvas'); overlay.width = canvas.width; overlay.height = canvas.height;
  drawOverlay(overlay.getContext('2d'), overlay.width, overlay.height, timestamp, false); ctx.drawImage(overlay, 0, 0);
  if (isSimulation()) {
    ctx.font = `${Math.max(12, canvas.width / 85)}px sans-serif`; ctx.fillStyle = '#153d32d9'; ctx.fillRect(12, canvas.height - 40, 290, 28); ctx.fillStyle = '#f0f8e0'; ctx.fillText(`SIMULATION · ${timecode(timestamp)} · GENERATED DETECTIONS`, 20, canvas.height - 21);
  }
  await api(`api/incidents/${encodeURIComponent(event.id)}/evidence`, { method: 'POST', body: JSON.stringify({ data_url: canvas.toDataURL('image/jpeg', .8), timestamp }) });
}

function renderIncidents() {
  const all = state.incidents, filtered = all.filter((incident) => state.filter === 'all' || incident.status === state.filter);
  $('incident-count').textContent = all.length; $('nav-count').textContent = all.length; $('open-count').textContent = all.filter((incident) => incident.status !== 'resolved').length;
  $('incident-summary').textContent = `${filtered.length} incident${filtered.length === 1 ? '' : 's'}${state.source ? ` · ${state.source.name}` : ''}`;
  const host = $('incidents'); host.replaceChildren();
  if (!filtered.length) {
    const empty = element('div', 'incident-empty'); empty.append(element('span', '', '◎'), element('h3', '', all.length ? 'Nothing in this view.' : 'Your rules are ready to notice.'), element('p', '', all.length ? 'Change the filter to see other incidents.' : 'Play the source to evaluate objects and create incident tickets.')); host.append(empty); return;
  }
  const table = element('table', 'incident-table'), thead = document.createElement('thead'), header = document.createElement('tr');
  ['TIME', 'INCIDENT', 'OBJECT', 'SEVERITY', 'STATUS', 'EVIDENCE'].forEach((title) => { const cell = element('th', '', title); cell.scope = 'col'; header.append(cell); }); thead.append(header); table.append(thead);
  const body = document.createElement('tbody');
  for (const incident of [...filtered].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))) {
    const row = document.createElement('tr'), timestamp = element('td', '', timecode(incident.timestamp));
    timestamp.title = incident.created_at || '';
    const description = document.createElement('td'); description.append(element('span', 'incident-rule', incident.rule_name || 'Zone event'), element('span', 'incident-message', incident.message || `${incident.class_name || 'Object'} triggered a spatial rule.`));
    const object = document.createElement('td'); object.append(element('span', 'incident-class', incident.class_name || 'No object'));
    const severity = document.createElement('td'); severity.append(element('span', `severity ${['info', 'warning', 'critical'].includes(incident.severity) ? incident.severity : 'info'}`, incident.severity || 'info'));
    const status = document.createElement('td'), statusButton = element('button', `ticket-status${incident.status === 'resolved' ? ' resolved' : ''}`, incident.status === 'resolved' ? '✓ Resolved' : 'Mark resolved'); statusButton.type = 'button';
    statusButton.setAttribute('aria-label', `${incident.status === 'resolved' ? 'Reopen' : 'Resolve'} ${incident.rule_name || 'incident'} at ${timecode(incident.timestamp)}`);
    statusButton.addEventListener('click', async () => {
      statusButton.disabled = true; const generation = state.generation;
      try { await api(`api/incidents/${encodeURIComponent(incident.id)}`, { method: 'PATCH', body: JSON.stringify({ status: incident.status === 'resolved' ? 'open' : 'resolved' }) }); if (generation === state.generation) await refreshIncidents(generation); }
      catch (error) { statusButton.disabled = false; notice(error.message, true); }
    }); status.append(statusButton);
    const evidence = document.createElement('td'), actions = element('div', 'incident-actions'), jump = element('button', '', '↗ Jump'); jump.type = 'button'; jump.addEventListener('click', () => { void seek(incident.timestamp); $('monitor-heading').scrollIntoView({ behavior: 'smooth', block: 'start' }); }); actions.append(jump);
    if (incident.evidence_url) {
      const view = element('button', '', 'View'); view.type = 'button'; view.addEventListener('click', () => {
        $('evidence-image').src = incident.evidence_url; $('evidence-caption').textContent = `${incident.source_name || state.source.name} · ${timecode(incident.timestamp)} · ${incident.rule_name || 'Incident'}${isSimulation() ? ' · Simulated source' : ''}`; $('evidence-dialog').showModal();
      }); actions.append(view);
    }
    evidence.append(actions); row.append(timestamp, description, object, severity, status, evidence); body.append(row);
    ['Time', 'Incident', 'Object', 'Severity', 'Status', 'Evidence'].forEach((label, index) => { row.children[index].dataset.label = label; });
  }
  table.append(body); host.append(table);
}

async function importSource(source) {
  const entry = source.source && typeof source.source === 'object' ? source.source : source;
  if (!entry.id) throw new Error('The server did not return a source ID.');
  const result = await api('api/sources'); state.sources = result.sources || [];
  renderSources(); await selectSource(entry.id);
}

async function uploadSource(event) {
  event.preventDefault(); const form = event.currentTarget, button = form.querySelector('button'); button.disabled = true; button.textContent = 'Uploading…';
  try { const result = await api('api/sources/upload', { method: 'POST', body: new FormData(form) }); await importSource(result); form.reset(); notice('Recording and detections imported. Draw zones to start defining rules.'); }
  catch (error) { notice(`Upload failed. ${error.message}`, true); }
  finally { button.disabled = false; button.textContent = 'Add source'; }
}

async function loadCatalog(event) {
  event.preventDefault(); const button = event.currentTarget.querySelector('button'), host = $('catalog-results'); button.disabled = true; host.replaceChildren(element('p', 'help', 'Loading videos…'));
  try {
    const result = await api(`api/vast/videos?location=${encodeURIComponent($('catalog-location').value.trim())}`);
    const videos = result.videos || []; host.replaceChildren();
    if (!videos.length) host.append(element('p', 'help', 'No videos found for this location.'));
    for (const video of videos) {
      const source = typeof video === 'string' ? video : video.source || video.Source || video.url;
      const name = typeof video === 'string' ? video.split('/').pop() : video.name || video.Name || video.description || source;
      const item = element('div', 'catalog-item'), add = element('button', 'text-button', 'Import'); add.type = 'button';
      add.disabled = !source; add.addEventListener('click', async () => {
        add.disabled = true; add.textContent = 'Running…';
        try { const result = await api('api/vast/import', { method: 'POST', body: JSON.stringify({ source }) }); await importSource(result); notice('VAST video imported and ready for spatial rules.'); add.textContent = 'Added'; }
        catch (error) { notice(`VAST import failed. ${error.message}`, true); add.disabled = false; add.textContent = 'Import'; }
      }); item.append(element('span', '', name || 'Video'), add); host.append(item);
    }
  } catch (error) { host.replaceChildren(element('p', 'help', error.message)); notice(`Could not load VAST catalog. ${error.message}`, true); }
  finally { button.disabled = false; }
}

$('play-button').addEventListener('click', togglePlay);
$('reset-button').addEventListener('click', () => seek(0, true));
$('timeline').addEventListener('input', () => { setPlayback(false); $('current-time').textContent = timecode($('timeline').value); });
$('timeline').addEventListener('change', () => seek(Number($('timeline').value)));
$('draw-button').addEventListener('click', beginDrawing);
$('cancel-zone').addEventListener('click', cancelDrawing);
$('undo-point').addEventListener('click', () => { state.points.pop(); updatePointCount(); draw(); });
$('finish-zone').addEventListener('click', finishZone);
$('overlay').addEventListener('pointermove', (event) => {
  if (!state.drawing) return;
  const rect = $('overlay').getBoundingClientRect(); state.cursor = [clamp((event.clientX - rect.left) / rect.width), clamp((event.clientY - rect.top) / rect.height)]; state.keyboardCursor = false; draw();
});
$('overlay').addEventListener('click', (event) => {
  if (!state.drawing || state.saving) return;
  const rect = $('overlay').getBoundingClientRect(), point = [clamp((event.clientX - rect.left) / rect.width), clamp((event.clientY - rect.top) / rect.height)];
  if (state.points.length && Math.hypot(point[0] - state.points.at(-1)[0], point[1] - state.points.at(-1)[1]) < .003) return;
  state.points.push(point); state.cursor = point; updatePointCount(); draw();
});
$('overlay').addEventListener('keydown', (event) => {
  if (!state.drawing) return;
  const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  if (arrows[event.key]) { event.preventDefault(); const step = event.shiftKey ? .05 : .01; state.cursor = state.cursor.map((value, index) => clamp(value + arrows[event.key][index] * step)); state.keyboardCursor = true; draw(); }
  else if (event.key === ' ') { event.preventDefault(); if (!state.saving) { state.points.push([...state.cursor]); updatePointCount(); draw(); } }
  else if (event.key === 'Enter') { event.preventDefault(); void finishZone(); }
  else if (event.key === 'Escape') { event.preventDefault(); cancelDrawing(); }
  else if (event.key === 'Backspace') { event.preventDefault(); state.points.pop(); updatePointCount(); draw(); }
});
$('new-rule').addEventListener('click', () => { clearRule(); $('rule-name').focus(); });
$('rule-form').addEventListener('submit', saveRule);
$('rule-classes').addEventListener('input', renderClassChips);
['rule-confidence', 'rule-condition', 'rule-anchor'].forEach((id) => $(id).addEventListener('input', formDependencies));
$('delete-rule').addEventListener('click', async () => { const next = structuredClone(state.config); next.rules = next.rules.filter((rule) => rule.id !== state.selectedRule); if (await persistConfig(next)) { clearRule(); notice('Rule deleted. Existing incidents are kept.'); } });
document.querySelectorAll('[data-preset]').forEach((button) => button.addEventListener('click', () => applyPreset(button.dataset.preset)));
document.querySelectorAll('[data-filter]').forEach((button) => button.addEventListener('click', () => { state.filter = button.dataset.filter; document.querySelectorAll('[data-filter]').forEach((item) => { item.classList.toggle('active', item === button); item.setAttribute('aria-pressed', String(item === button)); }); renderIncidents(); }));
$('upload-form').addEventListener('submit', uploadSource);
$('catalog-form').addEventListener('submit', loadCatalog);
$('close-evidence').addEventListener('click', () => $('evidence-dialog').close());
$('evidence-dialog').addEventListener('click', (event) => { if (event.target === $('evidence-dialog')) $('evidence-dialog').close(); });
$('video').addEventListener('error', () => {
  if (!$('video').getAttribute('src') || isSimulation()) return;
  setPlayback(false); $('stage-error').textContent = 'This video could not be played. Upload a browser-compatible MP4 or WebM file.'; $('stage-error').hidden = false;
});
$('video').addEventListener('ended', () => { state.time = state.duration; showTime(); setPlayback(false); void evaluate(); });
new ResizeObserver(draw).observe($('stage'));
formDependencies();
void initialize();
requestAnimationFrame(tick);
