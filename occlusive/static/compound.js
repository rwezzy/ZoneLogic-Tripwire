'use strict';
(() => {
  const base = { api, selectSource, persistConfig };
  let rules = [], selected = null, loadedSource = null, token = 0;
  const el = id => document.getElementById(id);
  const make = (tag, text, className) => { const n = document.createElement(tag); if (text) n.textContent = text; if (className) n.className = className; return n; };
  const status = message => { el('compound-status').textContent = message; };
  function select(id, options) {
    const input = make('select'); input.id = id;
    for (const [value, title] of options) { const option = make('option', title); option.value = value; input.append(option); }
    return input;
  }
  function label(text, input) { const n = make('label', text); n.append(input); return n; }
  function number(id, value, min, max, step) {
    const n = make('input'); Object.assign(n, { id, type: 'number', value, min, max, step }); return n;
  }
  function check(id, text) {
    const input = make('input'); input.id = id; input.type = 'checkbox';
    const n = make('label', '', 'compound-check'); n.append(input, document.createTextNode(text)); return n;
  }
  function fill(rule) {
    selected = rule?.id || null;
    el('compound-name').value = rule?.name || 'Potential co-occupancy for review';
    el('compound-operator').value = rule?.operator || 'AND';
    el('compound-dwell').value = rule?.dwell_seconds ?? 0;
    el('compound-cooldown').value = rule?.cooldown_seconds ?? 5;
    el('compound-enabled').checked = rule?.enabled !== false;
    el('compound-sound').checked = rule?.actions?.includes('sound') || false;
    for (const key of ['a', 'b']) {
      const sensor = rule?.[key];
      if (sensor) { el(`compound-${key}-class`).value = sensor.class_name; el(`compound-${key}-zone`).value = sensor.zone_id; }
      el(`compound-${key}-confidence`).value = sensor?.min_confidence ?? .4;
      el(`compound-${key}-not`).checked = sensor?.negate || false;
    }
    el('compound-delete').disabled = !selected;
    el('compound-picker').value = selected || '';
  }
  function clearState() { el('compound-state').replaceChildren(make('span', 'Boolean sensors: awaiting a synchronized detection frame.', 'unknown')); }
  async function load() {
    const id = sourceId(), request = ++token;
    loadedSource = null; el('compound-save').disabled = true;
    clearState();
    if (!id || !state.session) return;
    try {
      const result = await base.api(`api/compound/config?source_id=${encodeURIComponent(id)}`);
      if (id !== sourceId() || request !== token) return;
      rules = result.rules || []; loadedSource = id;
      const labels = state.classes.map(name => [name, name]);
      const zones = state.config.zones.map(zone => [zone.id, zone.name]);
      for (const key of ['a', 'b']) {
        for (const [suffix, options] of [['class', labels], ['zone', zones]]) {
          const target = el(`compound-${key}-${suffix}`), replacement = select(target.id, options);
          target.replaceWith(replacement);
        }
      }
      const picker = el('compound-picker'); picker.replaceChildren();
      for (const [value, title] of [['', 'New compound rule'], ...rules.map(rule => [rule.id, rule.name])]) {
        const option = make('option', title); option.value = value; picker.append(option);
      }
      fill(rules.find(rule => rule.id === selected) || rules[0]);
      el('compound-save').disabled = !labels.length || !zones.length;
      status(!zones.length ? 'Draw and save a zone first.' : !labels.length ? 'This source has no detected class labels.' : 'Both inputs use the same frame and box-center geometry. Single-object rules remain separate.');
    } catch (error) { status(`Compound settings unavailable: ${error.message}. Existing rules still work.`); }
  }
  async function save(remove = false) {
    const id = sourceId();
    if (loadedSource !== id || state.loading) return;
    setPlayback(false);
    if (state.evaluation) await state.evaluation;
    const inputs = {};
    for (const key of ['a', 'b']) inputs[key] = { class_name: el(`compound-${key}-class`).value, zone_id: el(`compound-${key}-zone`).value,
      min_confidence: Number(el(`compound-${key}-confidence`).value), negate: el(`compound-${key}-not`).checked };
    const rule = { id: selected || uid('compound'), name: el('compound-name').value.trim(), ...inputs, operator: el('compound-operator').value,
      dwell_seconds: Number(el('compound-dwell').value), cooldown_seconds: Number(el('compound-cooldown').value),
      enabled: el('compound-enabled').checked, actions: el('compound-sound').checked ? ['log', 'sound'] : ['log'], severity: 'warning' };
    const next = rules.filter(item => item.id !== rule.id); if (!remove) next.push(rule);
    el('compound-save').disabled = true;
    try {
      await base.api('api/compound/config', { method: 'PUT', body: JSON.stringify({ source_id: id, rules: next }) });
      selected = remove ? null : rule.id;
      if (id === sourceId()) { await selectSource(id); status('Compound rules saved. Replay from the beginning; an observed false-to-true transition is required.'); }
    } catch (error) { status(error.message); el('compound-save').disabled = false; }
  }
  function showValues(result) {
    const host = el('compound-state'); host.replaceChildren();
    if (!result.compound_states?.length) { clearState(); return; }
    const bit = value => value === null ? '?' : value ? '1' : '0';
    for (const value of result.compound_states) {
      const text = `${value.name}: ${value.not_a ? 'NOT ' : ''}A=${bit(value.a)} ${value.operator} ${value.not_b ? 'NOT ' : ''}B=${bit(value.b)} → ${bit(value.result)} · frame ${Number(value.timestamp).toFixed(2)}s${result.compound_fresh ? '' : ' · STALE'}`;
      host.append(make('span', text, value.result === null ? 'unknown' : value.result ? 'true' : ''));
    }
  }
  api = async function(path, options) {
    const generation = state.generation, transition = state.transition;
    const result = await base.api(path, options);
    if (/\/evaluate$/.test(path) && generation === state.generation && transition === state.transition) {
      try { showValues(result); } catch { /* Indicator failure cannot interrupt playback. */ }
    }
    return result;
  };
  selectSource = async function(id) { ++token; loadedSource = null; if (el('compound-state')) clearState(); await base.selectSource(id); await load(); };
  persistConfig = async function(next) { const result = await base.persistConfig(next); if (result) void load(); return result; };

  const panel = make('section', '', 'compound-panel'); panel.id = 'compound-panel';
  panel.append(make('h2', 'Compound Boolean Rules'), make('p', 'Two virtual sensors, one synchronized frame. AND requires both; OR requires either; XOR requires exactly one. NOT reverses detection presence, not physical certainty. This is not a collision detector.', 'compound-help'));
  const picker = select('compound-picker', [['', 'New compound rule']]); picker.className = 'compound-rule-picker'; panel.append(label('Saved compound rule', picker));
  picker.addEventListener('change', () => fill(rules.find(rule => rule.id === picker.value)));
  const form = make('form'); form.id = 'compound-form'; const grid = make('div', '', 'compound-grid');
  for (const key of ['a', 'b']) {
    const sensor = make('div', '', 'compound-sensor'); sensor.append(make('h3', `Sensor ${key.toUpperCase()}`),
      label('Detected object class', select(`compound-${key}-class`, [])), label('Saved zone', select(`compound-${key}-zone`, [])),
      label('Minimum confidence (0–1)', number(`compound-${key}-confidence`, .4, 0, 1, .05)), check(`compound-${key}-not`, 'NOT: no qualifying detection'));
    grid.append(sensor);
    if (key === 'a') grid.append(label('Operator', select('compound-operator', [['AND','AND'],['OR','OR'],['XOR','XOR']])));
  }
  form.append(grid);
  const settings = make('div', '', 'compound-settings'), name = make('input'); name.id = 'compound-name'; name.required = true; name.maxLength = 120;
  settings.append(label('Rule name', name), label('True for seconds', number('compound-dwell', 0, 0, 3600, .1)), label('Cooldown seconds', number('compound-cooldown', 5, 0, 3600, .1)), check('compound-enabled', 'Rule enabled'), check('compound-sound', 'Play alert sound'));
  form.append(settings);
  const actions = make('div', '', 'compound-actions'), submit = make('button', 'Save compound rule', 'button primary'), remove = make('button', 'Delete selected compound rule', 'button secondary');
  submit.id = 'compound-save'; submit.type = 'submit'; submit.disabled = true;
  remove.id = 'compound-delete'; remove.type = 'button'; remove.disabled = true; remove.addEventListener('click', () => { void save(true); }); actions.append(submit, remove); form.append(actions);
  form.addEventListener('submit', event => { event.preventDefault(); void save(); }); panel.append(form);
  const message = make('p', '', 'compound-status'); message.id = 'compound-status'; message.setAttribute('role', 'status'); panel.append(message);
  document.querySelector('.workspace-grid').after(panel);
  const indicator = make('div', '', 'compound-state'); indicator.id = 'compound-state'; indicator.setAttribute('role', 'status'); document.querySelector('.monitor-panel').append(indicator);
  clearState(); if (sourceId() && state.session) void load();
})();
