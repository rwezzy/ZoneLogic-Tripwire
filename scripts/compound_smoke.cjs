'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const net = require('node:net');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const port = await new Promise(resolve => { const s=net.createServer(); s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));}); });
  const base=`http://127.0.0.1:${port}`;
  const server=spawn(process.env.PYTHON || 'python',['run_occlusive.py','--port',String(port),'--data-dir',path.resolve('artifacts',`compound-browser-${Date.now()}`)],{windowsHide:true,stdio:'ignore'});
  process.on('exit',()=>server.kill()); let browser;
  try {
    let ready=false;
    for(let i=0;i<100;i++){try{ready=(await fetch(base+'/health')).ok;}catch{}if(ready)break;await new Promise(r=>setTimeout(r,100));}
    assert(ready);
    browser=await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL || 'msedge',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1050}}), errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/app/'); await page.locator('#play-button:not([disabled])').waitFor();
    assert.match(await page.title(),/Occlusive Logic/);
    const bytes=await page.evaluate(async()=>{
      const canvas=document.createElement('canvas');canvas.width=640;canvas.height=360;
      const ctx=canvas.getContext('2d'), stream=canvas.captureStream(10), rec=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'}),chunks=[];
      rec.ondataavailable=e=>chunks.push(e.data);const done=new Promise(r=>{rec.onstop=r;});rec.start();
      for(let i=0;i<20;i++){ctx.fillStyle='#eef2e6';ctx.fillRect(0,0,640,360);ctx.fillStyle='#246b5b';ctx.font='20px sans-serif';ctx.fillText('SYNTHETIC BROWSER TEST · not VAST',30,35);if(i>=4&&i<12)ctx.fillRect(180,110,40,110);if(i>=8&&i<16){ctx.fillStyle='#ba9158';ctx.fillRect(350,140,110,60);}await new Promise(r=>setTimeout(r,100));}
      rec.stop();await done;stream.getTracks().forEach(t=>t.stop());return Array.from(new Uint8Array(await new Blob(chunks).arrayBuffer()));
    });
    const detection=(class_name,bbox)=>({class_name,bbox,confidence:.9,track_id:class_name});
    const person=detection('person',[.28,.3,.35,.62]), car=detection('car',[.54,.38,.73,.56]);
    const document={width:640,height:360,duration:2,provenance:'Synthetic browser fixture; no real VAST or inference claim',frames:[{timestamp:0,detections:[]},{timestamp:.4,detections:[person]},{timestamp:.8,detections:[person,car]},{timestamp:1.2,detections:[car]},{timestamp:1.6,detections:[]}]};
    const uploaded=await page.request.post(base+'/api/sources/upload',{multipart:{name:'Synchronized browser fixture',video:{name:'fixture.webm',mimeType:'video/webm',buffer:Buffer.from(bytes)},detections:{name:'detections.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(document))}}});
    assert.equal(uploaded.status(),201); const id=(await uploaded.json()).id;
    assert.equal((await page.request.put(base+'/api/config',{data:{source_id:id,zones:[{id:'crosswalk',name:'Test crossing region',points:[[.1,.1],[.9,.1],[.9,.9],[.1,.9]]}],rules:[]}})).status(),200);
    await page.evaluate(async id=>{state.sources=(await api('api/sources')).sources;renderSources();await selectSource(id);},id);
    await page.waitForFunction(()=>document.querySelector('#video').videoWidth===640);
    await page.locator('#compound-a-class').selectOption('person');await page.locator('#compound-b-class').selectOption('car');
    assert.equal(await page.locator('#compound-a-class option').count(),2);
    await page.locator('#compound-name').fill('Person AND car for review');await page.locator('#compound-save').click();
    await page.waitForFunction(()=>document.querySelector('#compound-status').textContent.includes('Compound rules saved'));
    await page.locator('#play-button').click();
    await page.waitForFunction(()=>Number(document.querySelector('#incident-count').textContent)===1);
    await page.waitForFunction(()=>!state.playing && state.time>1.8);
    const response=await page.request.get(base+'/api/incidents?source_id='+id), events=(await response.json()).incidents;
    assert.equal(events.length,1);assert.equal(events[0].timestamp,.8);assert(events[0].compound.a && events[0].compound.b);assert(events[0].evidence_url);
    assert.match(await page.locator('#compound-state').innerText(),/A=0 AND B=0 → 0/);
    await page.getByRole('button',{name:'View',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#evidence-image').naturalWidth>0);await page.locator('#close-evidence').click();
    await page.getByRole('button',{name:/Resolve Person AND car/}).click();
    await page.waitForFunction(()=>document.querySelector('#open-count').textContent==='0');
    await page.locator('#play-button').click();await page.waitForFunction(()=>state.playing);await page.waitForFunction(()=>!state.playing && state.time>1.8);
    assert.equal((await (await page.request.get(base+'/api/incidents?source_id='+id)).json()).incidents.length,1);
    await page.evaluate(async()=>{await seek(.9);});
    await fs.mkdir('artifacts',{recursive:true});await page.screenshot({path:'artifacts/occlusive-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:'artifacts/occlusive-mobile.png',fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,realVideoDecoding:true,syntheticFixture:true,realVastVerified:false,compoundUI:true,frameSynchronized:true,positiveAt:.8,negativeAt:1.6,evidence:true,resolution:true,replayDeduplicated:true,mobile:true,errors}));
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
