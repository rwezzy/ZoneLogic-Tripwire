/* Optional browser integration check. Install Playwright or point PLAYWRIGHT_MODULE
 * at an existing package. Uses an isolated browser profile and localhost only. */
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
  const data = path.resolve('artifacts', `browser-test-data-${Date.now()}`);
  const server = spawn(process.env.PYTHON || 'python', ['run.py', '--port', String(port)], {
    windowsHide: true, env: { ...process.env, ZONELOGIC_DATA_DIR: data }, stdio: 'ignore',
  });
  process.on('exit', () => server.kill());
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try { ready = (await fetch(base + '/api/status')).ok; } catch {}
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!ready) { server.kill(); throw new Error('Isolated test server did not start'); }
  const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1512, height: 1120 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await fs.mkdir('artifacts', { recursive: true });
  try {
    await page.goto(base + '/app/', { waitUntil: 'networkidle' });
    await page.locator('#play-button:not([disabled])').waitFor();
    assert.equal(await page.title(), 'ZoneLogic · Visual rules, real events');
    assert.match(await page.locator('#source-kind').innerText(), /SIMULATED/);
    assert.equal(await page.locator('.source-card').count(), 2);
    await page.screenshot({ path: 'artifacts/dashboard-desktop.png', fullPage: true });

    await page.locator('#rule-name').fill('Passage occupied for 2 seconds');
    await page.locator('#save-rule').click();
    await page.waitForFunction(() => document.querySelector('#save-state').textContent.includes('Saved'));
    await page.locator('#play-button').click();
    await page.waitForFunction(() => document.querySelector('#current-time').textContent >= '00:08', null, { timeout: 15000 });
    await page.locator('#play-button').click();
    await page.waitForFunction(() => Number(document.querySelector('#incident-count').textContent) >= 1);
    const incidentResponse = await page.request.get(base + '/api/incidents?source_id=demo-access');
    const items = (await incidentResponse.json()).incidents;
    assert(items.some(item => item.class_name === 'suitcase' && item.rule_name === 'Passage occupied for 2 seconds'));
    await page.getByRole('button', { name: 'View', exact: true }).first().click();
    await page.locator('#evidence-dialog[open]').waitFor();
    assert(await page.locator('#evidence-image').evaluate(img => img.complete && img.naturalWidth > 0));
    await page.locator('#close-evidence').click();
    await page.getByText('Mark resolved', { exact: true }).first().click();
    await page.waitForFunction(() => document.querySelector('#open-count').textContent === '0');
    await page.screenshot({ path: 'artifacts/incident-desktop.png', fullPage: true });

    // The geometry editor must persist the same normalized polygon shown on screen.
    await page.locator('#draw-button').click();
    await page.locator('#zone-name').fill('Browser test region');
    const box = await page.locator('#overlay').boundingBox();
    for (const [x, y] of [[.12, .12], [.3, .12], [.3, .3], [.12, .3]]) {
      await page.locator('#overlay').click({ position: { x: box.width * x, y: box.height * y } });
    }
    await page.locator('#finish-zone').click();
    await page.waitForFunction(() => document.querySelector('#zone-list').textContent.includes('Browser test region'));
    const config = await (await page.request.get(base + '/api/config?source_id=demo-access')).json();
    assert(config.zones.some(zone => zone.name === 'Browser test region' && zone.points.length === 4));

    await page.locator('.source-card').nth(1).click();
    await page.waitForFunction(() => document.querySelector('#monitor-heading').textContent.includes('Waste'));
    await page.locator('#play-button:not([disabled])').waitFor();
    await page.locator('#play-button').click();
    await page.waitForFunction(() => document.querySelector('#current-time').textContent >= '00:08', null, { timeout: 15000 });
    await page.locator('#play-button').click();
    const waste = await (await page.request.get(base + '/api/incidents?source_id=demo-waste')).json();
    assert(waste.incidents.some(item => item.class_name === 'bottle' && item.condition === 'enter'));

    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: 'artifacts/dashboard-mobile.png', fullPage: true });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Mobile has horizontal overflow');
    // Exercise actual video decoding and range serving with a generated WebM.
    // This is a synthetic media fixture, not a model or VAST integration claim.
    const bytes = await page.evaluate(async () => {
      const canvas = document.createElement('canvas'); canvas.width = 320; canvas.height = 180;
      const ctx = canvas.getContext('2d'), stream = canvas.captureStream(10);
      const recorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp8' });
      const chunks = [];
      recorder.ondataavailable = event => chunks.push(event.data);
      const stopped = new Promise(resolve => { recorder.onstop = resolve; });
      recorder.start();
      for (let i = 0; i < 20; i++) {
        ctx.fillStyle = '#172d2c'; ctx.fillRect(0, 0, 320, 180);
        ctx.fillStyle = '#d7ec8a'; ctx.fillRect(25 + i * 4, 50, 20, 70);
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      recorder.stop(); await stopped; stream.getTracks().forEach(track => track.stop());
      return Array.from(new Uint8Array(await new Blob(chunks, { type: 'video/webm' }).arrayBuffer()));
    });
    const document = { width: 320, height: 180, duration: 2, provenance: 'Synthetic browser media fixture, no detector inference.', frames: Array.from({ length: 10 }, (_, i) => ({ timestamp: i / 5, detections: [{ class_name: 'bottle', confidence: .9, bbox: [(25 + i * 8) / 320, 50 / 180, (45 + i * 8) / 320, 120 / 180], track_id: 'test-bottle' }] })) };
    await page.locator('details.import-section > summary').filter({ hasText: 'Upload a recording' }).click();
    await page.locator('#upload-form input[name=name]').fill('Synthetic video decode test');
    await page.locator('#upload-form input[name=video]').setInputFiles({ name: 'synthetic.webm', mimeType: 'video/webm', buffer: Buffer.from(bytes) });
    await page.locator('#upload-form input[name=detections]').setInputFiles({ name: 'synthetic.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) });
    await page.locator('#upload-form button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#monitor-heading').textContent === 'Synthetic video decode test');
    await page.waitForFunction(() => document.querySelector('#video').videoWidth === 320);
    await page.locator('#play-button:not([disabled])').waitFor();
    await page.locator('#play-button').click();
    await page.waitForFunction(() => document.querySelector('#video').currentTime > .5);
    await page.locator('#play-button').click();
    const mediaUrl = await page.locator('#video').getAttribute('src');
    const ranged = await page.request.get(new URL(mediaUrl, base + '/app/').href, { headers: { Range: 'bytes=0-63' } });
    assert.equal(ranged.status(), 206);
    assert.equal((await ranged.body()).length, 64);
    await page.goto(base + '/', { waitUntil: 'networkidle' });
    await page.locator('#play-button:not([disabled])').waitFor();
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log(JSON.stringify({ ok: true, desktop: true, mobile: true, prefixedRoute: true, entry: true, dwell: true, polygonSaved: true, evidence: true, resolved: true, videoPlayback: true, byteRange: true, errors, artifacts: path.resolve('artifacts') }));
  } catch (error) {
    await page.screenshot({ path: 'artifacts/browser-failure.png', fullPage: true });
    console.error(JSON.stringify({ errors, notice: await page.locator('#notice').textContent(), playback: await page.locator('#playback-state').textContent(), time: await page.locator('#current-time').textContent(), button: await page.locator('#play-button').getAttribute('aria-label') }));
    throw error;
  } finally {
    await context.close();
    await browser.close();
    server.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
