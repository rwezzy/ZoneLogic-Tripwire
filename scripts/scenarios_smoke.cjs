/* Browser verification for the optional playground; isolated local data only. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const port = await new Promise((resolve, reject) => {
    const socket = net.createServer();
    socket.on('error', reject);
    socket.listen(0, '127.0.0.1', () => { const selected = socket.address().port; socket.close(() => resolve(selected)); });
  });
  const base = `http://127.0.0.1:${port}`;
  const data = path.resolve('artifacts', `scenario-browser-data-${Date.now()}`);
  const server = spawn(process.env.PYTHON || 'python', ['run_scenarios.py', '--port', String(port), '--data-dir', data], {
    windowsHide: true, stdio: 'ignore',
  });
  process.on('exit', () => server.kill());
  let browser;
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      try { ready = (await fetch(base + '/api/scenarios')).ok; } catch {}
      if (ready) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert(ready, 'Isolated playground did not start');
    assert((await (await fetch(base + '/api/scenarios')).json()).enabled, 'Optional pack fell back');
    browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1040 }, reducedMotion: 'reduce' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.vibrationCalls = [];
      Object.defineProperty(navigator, 'vibrate', { configurable: true, value(pattern) {
        window.vibrationCalls.push(pattern);
        return false;
      } });
    });
    await fs.mkdir('artifacts', { recursive: true });
    try {
      await page.goto(base + '/app/', { waitUntil: 'networkidle' });
      await page.locator('#play-button:not([disabled])').waitFor();
      assert.equal(await page.locator('.source-card').count(), 6);
      const cases = [
        ['scene-sorting', 'Recycling and trash', ['info', 'warning', 'info']],
        ['scene-wheelchair', 'Wheelchair route', ['warning']],
        ['scene-crosswalk', 'Crosswalk vehicle', ['warning']],
        ['scene-cane', 'Cane camera cue', ['warning']],
      ];
      for (const [id, title, severities] of cases) {
        await page.locator('.source-card').filter({ hasText: title }).click();
        await page.waitForFunction(id => state.source.id === id && !!state.session && !state.loading, id);
        await page.locator('#play-button').click();
        await page.waitForFunction(() => state.time > .5);
        await page.locator('#play-button').click();
        // Fast-forward the real evaluator, which processes every intervening
        // detection frame, captures evidence, and refreshes the actual UI.
        await page.evaluate(async () => {
          if (state.evaluation) await state.evaluation;
          state.time = 18; showTime(); draw(); await evaluate();
        });
        const events = (await (await page.request.get(base + '/api/incidents?source_id=' + id)).json()).incidents;
        events.sort((a, b) => a.timestamp - b.timestamp);
        assert.deepEqual(events.map(event => event.severity), severities, id);
        assert(events.every(event => event.scenario && event.evidence_url), 'Event metadata/evidence missing');
        assert(events.every(event => !event.scenario.vibration_requested), 'Starter scenes must not request vibration');
        assert.equal(await page.locator('.incident-table tbody tr').count(), severities.length);
        await page.screenshot({ path: `artifacts/${id}-desktop.png`, fullPage: true });
        await page.getByRole('button', { name: 'View', exact: true }).first().click();
        await page.locator('#evidence-dialog[open]').waitFor();
        await page.waitForFunction(() => document.getElementById('evidence-image').naturalWidth > 0);
        await page.locator('#close-evidence').click();
      }
      assert.deepEqual(await page.evaluate(() => window.vibrationCalls.filter(value => Array.isArray(value) && value.some(Boolean))), []);

      // A custom polygon and a modified core rule must still persist normally.
      await page.locator('#draw-button').click();
      await page.locator('#zone-name').fill('Custom forward region');
      const bounds = await page.locator('#overlay').boundingBox();
      for (const [x, y] of [[.30, .25], [.70, .25], [.80, .85], [.20, .85]]) {
        await page.locator('#overlay').click({ position: { x: x * bounds.width, y: y * bounds.height } });
      }
      await page.locator('#finish-zone').click();
      await page.waitForFunction(() => document.getElementById('zone-list').textContent.includes('Custom forward region'));
      await page.locator('#rule-name').fill('Custom vehicle cue');
      await page.locator('#save-rule').click();
      await page.waitForFunction(() => document.getElementById('save-state').textContent.includes('Saved'));
      const configuration = await (await page.request.get(base + '/api/config?source_id=scene-cane')).json();
      const custom = configuration.zones.find(zone => zone.name === 'Custom forward region');
      assert(custom && custom.points.length === 4);
      assert(configuration.rules.some(rule => rule.zone_id === custom.id && rule.name === 'Custom vehicle cue'));
      const options = await (await page.request.get(base + '/api/scenarios/options?source_id=scene-cane')).json();
      assert.equal(options.rules['cane-vehicle'], undefined, 'Changed rules must invalidate optional actions');

      // Exercise real rule/action controls with device capabilities mocked. These
      // checks establish graceful handling, never physical vibration delivery.
      for (const mode of ['rejected', 'throws', 'unsupported', 'disabled', 'accepted']) {
        await page.locator('#rule-name').fill(`Vehicle cue: ${mode}`);
        await page.locator('#save-rule').click();
        await page.waitForFunction(() => document.getElementById('save-state').textContent.includes('Saved'));
        await page.locator('#scenario-meaning').selectOption('cane');
        await page.locator('#scenario-vibrate').check();
        await page.locator('#scenario-save-actions').click();
        await page.waitForFunction(async () => {
          const value = await (await fetch('api/scenarios/options?source_id=scene-cane')).json();
          return value.rules['cane-vehicle']?.vibrate === true;
        });
        await page.locator('#scenario-device-cues').check();
        if (mode === 'disabled') await page.locator('#scenario-device-cues').uncheck();
        await page.evaluate(mode => {
          window.vibrationCalls = [];
          Object.defineProperty(navigator, 'vibrate', { configurable: true, value: mode === 'unsupported' ? undefined : function(pattern) {
            window.vibrationCalls.push(pattern);
            if (mode === 'throws') throw new Error('Mock device unavailable');
            return mode === 'accepted';
          } });
        }, mode);
        await page.locator('#reset-button').click();
        await page.locator('#play-button:not([disabled])').waitFor();
        await page.evaluate(async () => {
          if (state.evaluation) await state.evaluation;
          state.time = 18; showTime(); draw(); await evaluate();
        });
        const events = (await (await page.request.get(base + '/api/incidents?source_id=scene-cane')).json()).incidents;
        const event = events.find(item => item.rule_name === `Vehicle cue: ${mode}`);
        assert(event && event.scenario?.vibration_requested, `Core event lost for ${mode}`);
        assert.equal(event.scenario.physical_delivery_confirmed, false);
        await page.locator('#scenario-cue').waitFor({ state: 'visible' });
        const calls = await page.evaluate(() => window.vibrationCalls.filter(value => Array.isArray(value) && value.some(Boolean)));
        assert.equal(calls.length, ['unsupported', 'disabled'].includes(mode) ? 0 : 1, mode);
        assert(!await page.locator('#notice').evaluate(node => !node.hidden && node.textContent.includes('Evaluation paused')));
      }

      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: 'artifacts/scenarios-mobile.png', fullPage: true });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Mobile horizontal overflow');
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ ok: true, scenes: 4, expectedEvents: 6, polygonSaved: true, evidence: true, optionalDefaultsOff: true, modifiedRuleActionsOff: true, deviceFallbacks: ['rejected', 'throws', 'unsupported', 'disabled', 'accepted'], mobile: true, errors }));
    } catch (error) {
      await page.screenshot({ path: 'artifacts/scenarios-failure.png', fullPage: true });
      console.error(JSON.stringify({ errors, notice: await page.locator('#notice').textContent() }));
      throw error;
    } finally { await context.close(); }
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
