import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {setTimeout as delay} from "node:timers/promises";
import {once} from "node:events";

const profile=await mkdtemp(join(tmpdir(),"insho-chrome-"));
const browser=spawn(process.env.INSHO_CHROME,["--headless","--disable-gpu","--no-first-run","--no-default-browser-check",
  "--disable-background-networking","--remote-debugging-port=0","--user-data-dir="+profile,"--window-size=1500,900","about:blank"],
{stdio:["ignore","ignore","pipe"]});
let browserErrors="";
browser.stderr.on("data",buffer=>{browserErrors+=buffer.toString();});
let ws, pageDebug, lastStep="browser launch";
try {
  const port=await wait(async()=>(await readFile(join(profile,"DevToolsActivePort"),"utf8").catch(()=>"")).split("\n")[0]);
  const target=(await (await fetch("http://127.0.0.1:"+port+"/json/list")).json()).find(t=>t.type==="page");
  ws=new WebSocket(target.webSocketDebuggerUrl);
  await once(ws,"open");
  const pending=new Map(), exceptions=[];
  let seq=0;
  ws.addEventListener("message",event=>{
    const message=JSON.parse(event.data);
    if(message.id&&pending.has(message.id)){
      const {resolve,reject}=pending.get(message.id);
      pending.delete(message.id);
      if(message.error)reject(new Error(message.error.message));else resolve(message.result);
    }else if(message.method==="Runtime.exceptionThrown")exceptions.push(message.params.exceptionDetails.exception?.description);
  });
  function cdp(method,params={}){
    const id=++seq;
    return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
  }
  async function js(expression){
    const {result,exceptionDetails}=await cdp("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});
    if(exceptionDetails)throw new Error(exceptionDetails.exception?.description||exceptionDetails.text);
    return result.value;
  }
  const q=selector=>"document.querySelector("+JSON.stringify(selector)+")";
  async function click(selector){await wait(()=>js("!!"+q(selector)));await js(q(selector)+".click()");}
  async function named(text,selector="button"){
    const find="Array.from(document.querySelectorAll("+JSON.stringify(selector)+")).find(x=>x.textContent.trim()==="+JSON.stringify(text)+")";
    await wait(()=>js("!!("+find+")"));
    await js("("+find+").click()");
  }
  async function type(name,value){
    await js("(()=>{const e="+q('[name="'+name+'"]')+"; e.value="+JSON.stringify(value)+";e.dispatchEvent(new Event('input',{bubbles:true}));})()");
  }
  async function choose(name,value){
    await js("(()=>{const e="+q('[name="'+name+'"]')+"; e.value="+JSON.stringify(value)+";e.dispatchEvent(new Event('change',{bubbles:true}));})()");
  }
  let previousRevision=0;
  async function saved(){
    await wait(()=>js("document.querySelector('.save')?.textContent==='Saved' && +document.querySelector('.save').dataset.revision>"+previousRevision));
    previousRevision=await js("+document.querySelector('.save').dataset.revision");
  }
  async function shapeCount(n){await wait(()=>js("document.querySelectorAll('[data-shape]').length==="+n));}
  async function api(suffix=""){
    return js("(async()=>{const scene=location.pathname.split('/').at(-1);return (await fetch('/api/v1/spatial/scenes/'+scene+"+
      JSON.stringify(suffix||"/editor")+",{headers:{Authorization:'Bearer '+sessionStorage.getItem('insho-session')}})).json()})()");
  }
  async function coords(x,y){
    return js("(()=>{const r=document.querySelector('.viewport').getBoundingClientRect(),m=document.querySelector('.viewport g').getCTM();"+
      "return {x:r.left+m.e+m.a*"+x+",y:r.top+m.f+m.d*"+y+"};})()");
  }
  async function mouse(type,p,modifiers=0){
    return cdp("Input.dispatchMouseEvent",{type,x:p.x,y:p.y,button:type==="mouseMoved"?"none":"left",buttons:type==="mouseReleased"?0:1,
      ...(type==="mouseMoved"?{}:{clickCount:1}),modifiers});
  }
  async function worldClick(x,y,modifiers=0){
    const p=await coords(x,y);await mouse("mousePressed",p,modifiers);await mouse("mouseReleased",p,modifiers);
  }
  async function drag(a,b,modifiers=0){
    await mouse("mousePressed",a,modifiers);
    if(await js("document.querySelector('.viewport').dataset.drag")===undefined)throw new Error("Drag did not start at "+JSON.stringify(a));
    for(const t of [.25,.5,.75,1])await mouse("mouseMoved",{x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t},modifiers);
    await mouse("mouseReleased",b,modifiers);
  }
  async function key(key,modifiers=0){
    if(!modifiers)await js("document.querySelector('.viewport').focus()");
    await cdp("Input.dispatchKeyEvent",{type:"keyDown",key,modifiers});
    await cdp("Input.dispatchKeyEvent",{type:"keyUp",key,modifiers});
  }
  await cdp("Runtime.enable");
  await cdp("Page.enable");

  lastStep="login, project navigation, and event scene creation";
  await cdp("Page.navigate",{url:process.env.INSHO_URL+"/workspace"});
  await wait(()=>js("!!document.querySelector('[name=email]')"));
  await type("email",process.env.INSHO_TEST_EMAIL);await type("password",process.env.INSHO_TEST_PASSWORD);
  await named("Sign in");
  await click(".projects a");
  await named("New measured scene","summary");
  await type("name","Main plan"); await type("width","30");await type("depth","20");await type("height","8");
  const eventId=await js("document.querySelector('[name=event]').options[1].value");
  await choose("event",eventId);
  await named("Create scene");
  await wait(()=>js("!!document.querySelector('.viewport')"));
  await shapeCount(0);
  assert.equal(await js("document.querySelector('.empty-plan').hidden"),false);
  const emptyState=await api();
  assert.equal(emptyState.scene.event_id,eventId);
  previousRevision=emptyState.scene.current_revision;
  await js("window.trace=[]; ['pointerdown','pointerup','pointercancel','lostpointercapture'].forEach(type=>document.addEventListener(type,e=>{if(e.target.closest('.viewport'))trace.push([type,e.clientX,e.clientY,e.target.dataset.handle,document.querySelector('.viewport').dataset.drag]);},true));");
  pageDebug=()=>js("({trace:window.trace?.slice(-16), status:document.querySelector('.save')?.textContent, notice:document.querySelector('.notice')?.textContent, layers:document.querySelector('[aria-label=Layers]')?.innerHTML.slice(0,1900)})");

  lastStep="Phase 8 Plan reference";
  await named("Plan reference");
  await wait(()=>js("document.querySelector('#dialog').textContent.includes('Reception layout reference')"));
  await named("Close");

  lastStep="viewport and grid";
  await wait(()=>js("!!document.querySelector('.viewport g path[d^=M]')"));
  const initial=await coords(0,0);
  await click("[aria-label='Zoom in']");
  await wait(async()=>Math.abs((await coords(0,0)).x-initial.x)>3);
  await named("Pan");
  const beforePan=await coords(10,10);
  await drag(beforePan,{x:beforePan.x+52,y:beforePan.y-22});
  await js("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))");
  const afterPan=await coords(10,10);
  assert.ok(Math.abs(afterPan.x-beforePan.x-52)<3, JSON.stringify({beforePan,afterPan}));
  await named("Fit scene");
  await click(".contextbar input[type=checkbox]");
  await saved();
  assert.equal((await api()).scene.metadata.editor.grid,false);
  await click(".contextbar input[type=checkbox]");
  await saved();
  await choose("grid-preset","0.5");
  await saved();
  assert.equal((await api()).scene.metadata.editor.spacing,.5);
  await type("grid-spacing","1");
  await named("Set");
  await saved();
  assert.equal((await api()).scene.metadata.editor.spacing,1);

  lastStep="placement, zone, inspector, and validation";
  await named("Object");
  await worldClick(9.3,8.2);
  await saved();await shapeCount(1);
  let state=await api(), stage=state.objects[0];
  assert.equal(+stage.transform.position.x,9);
  await choose("placement","table");
  await worldClick(20,8);
  await saved();await shapeCount(2);
  await named("Zone");
  await drag(await coords(4,3),await coords(14,14));
  await saved();await shapeCount(3);
  assert.equal((await api()).zones.length,1);
  await type("name","Guest area");
  await type("width_m","12");
  await named("Apply properties");
  await saved();
  assert.equal(+(await api()).zones[0].dimensions.width_m,12);
  await named("Select");
  await worldClick(9,8);
  await type("name","Main Stage");
  await type("width_m","5");
  assert.equal(await js("document.querySelector('.save').textContent"),"Unsaved changes");
  assert.equal(await js("!window.dispatchEvent(new Event('beforeunload',{cancelable:true}))"),true);
  await named("Apply properties");
  await saved();
  state=await api();stage=state.objects.find(o=>o.id===stage.id);
  assert.equal(stage.name,"Main Stage");
  assert.equal(+stage.dimensions.width_m,5);

  lastStep="move, rotate, resize, and measurement";
  await drag(await coords(9,8),await coords(10,9));
  await saved();
  assert.equal(+(await api()).objects.find(o=>o.id===stage.id).transform.position.x,10);
  await js("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))");
  const handle=await js("(()=>{const r=document.querySelector('[data-handle=rotate]').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()");
  const center=await coords(10,9);
  await drag(handle,{x:center.x+(center.y-handle.y),y:center.y});
  await saved();
  let rotated=(await api()).objects.find(o=>o.id===stage.id);
  assert.equal(Math.abs(+rotated.transform.rotation_deg.z),90,JSON.stringify({handle,center,component:rotated.transform}));
  await js("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))");
  const corner=await js("(()=>{const r=document.querySelector('[data-handle=resize]').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()");
  await drag(corner,{x:corner.x+(corner.x-center.x)*.45,y:corner.y+(corner.y-center.y)*.45},8);
  await saved();
  assert.ok(+(await api()).objects.find(o=>o.id===stage.id).dimensions.width_m>6);
  await mouse("mousePressed",await coords(10,9));
  await mouse("mouseMoved",await coords(12,10));
  await key("Escape");
  await mouse("mouseReleased",await coords(12,10));
  assert.equal((await api()).scene.current_revision,previousRevision);
  await named("Measure");
  await drag(await coords(3,3),await coords(6,7));
  await wait(()=>js("Array.from(document.querySelectorAll('.viewport text')).some(n=>n.textContent==='5 m')"));
  await named("Select");

  lastStep="multi-selection, alignment, clipboard operations, and visibility";
  await worldClick(10,9);
  await worldClick(20,8,8);
  await wait(()=>js("document.querySelector('.inspector').textContent.includes('2 selected')"));
  await choose("alignment","center_y");
  await saved();
  state=await api();
  assert.equal(+state.objects[0].transform.position.y,+state.objects[1].transform.position.y);
  await named("Measure clearance");
  await wait(()=>js("[...document.querySelectorAll('.inspector span[role=status]')].some(n=>n.textContent.endsWith(' m'))"));
  const shiftedStage=state.objects.find(o=>o.id===stage.id);
  const origin=await coords(+shiftedStage.transform.position.x,+shiftedStage.transform.position.y);
  const end=await coords(+shiftedStage.transform.position.x,+shiftedStage.transform.position.y+2);
  await drag(origin,end);
  await saved();
  const movedGroup=await api();
  const shifts=state.objects.map(old=>+movedGroup.objects.find(row=>row.id===old.id).transform.position.y-+old.transform.position.y);
  assert.ok(shifts[0]>1);
  assert.equal(shifts[0],shifts[1]);
  await named("Duplicate");
  await saved();await shapeCount(5);
  await key("Delete");
  await saved();await shapeCount(3);
  await named("Undo");
  await saved();await shapeCount(5);
  await named("Redo");
  await saved();await shapeCount(3);
  assert.deepEqual((await api()).objects.map(o=>o.id).sort(),state.objects.map(o=>o.id).sort());
  await click("[aria-label='Hide Main Stage']");
  await saved();await shapeCount(2);
  await click("[aria-label='Show Main Stage']");
  await saved();await shapeCount(3);

  lastStep="failed save, retry, keyboard, and persistence after reload";
  await named("Main Stage",".sidebar .layer button:first-child");
  await named("Asset references (0)","summary");
  await named("Browse project assets");
  await wait(()=>js("document.querySelector('[name=asset] option')?.textContent==='Reception reference'"));
  await named("Attach");
  await saved();
  assert.equal((await api()).assets.length,1);
  await type("x","0");
  await named("Apply properties");
  await saved();
  await wait(()=>js("document.querySelector('.issues').textContent.includes('Partial scene containment')"));
  await named("Undo");
  await saved();
  await cdp("Network.enable");
  await cdp("Network.emulateNetworkConditions",{offline:true,latency:0,downloadThroughput:-1,uploadThroughput:-1});
  await key("ArrowRight");
  await wait(()=>js("document.querySelector('.save').dataset.state==='failed'"));
  await cdp("Network.emulateNetworkConditions",{offline:false,latency:0,downloadThroughput:-1,uploadThroughput:-1});
  await named("Retry save");
  await saved();
  const snapshot=await api();
  assert.equal(+snapshot.objects.find(o=>o.id===stage.id).transform.position.x,10.1);
  await cdp("Page.reload");
  await wait(()=>js("!!document.querySelector('.viewport')"));
  await shapeCount(3);
  const reopened=await api();
  assert.deepEqual(reopened.objects,snapshot.objects);
  assert.deepEqual(reopened.zones,snapshot.zones);
  assert.deepEqual(reopened.scene,snapshot.scene);
  assert.deepEqual(reopened.assets,snapshot.assets);

  lastStep="session expiry and sign-in recovery";
  await named("Main Stage",".sidebar .layer button:first-child");
  await js("fetch('/api/v1/auth/logout',{method:'POST',headers:{Authorization:'Bearer '+sessionStorage.getItem('insho-session')}}).then(r=>r.status)");
  await key("ArrowRight");
  await wait(()=>js("document.querySelector('.save').dataset.state==='failed'"));
  await named("Sign in again");
  await type("password",process.env.INSHO_TEST_PASSWORD);
  await named("Sign in");
  await wait(()=>js("!document.querySelector('#dialog').open"));
  await named("Retry save");
  await saved();
  assert.equal(+(await api()).objects.find(o=>o.id===stage.id).transform.position.x,10.2);

  lastStep="viewport culling with 240 additional persisted objects";
  const bulk=await js(`(async()=>{
    const scene=location.pathname.split('/').at(-1), headers={Authorization:'Bearer '+sessionStorage.getItem('insho-session')};
    const path='/api/v1/spatial/scenes/'+scene+'/editor';
    const start=performance.now(), current=await(await fetch(path,{headers})).json();
    const operations=Array.from({length:240},(_,i)=>({kind:'create_object',id:crypto.randomUUID(),
      data:{name:'Chair '+i,category:'chair',dimensions:{width_m:.5,depth_m:.5,height_m:1},
        transform:{position:{x:1+(i%20)*1.4,y:1+Math.floor(i/20)*1.5,z:.5}}}}));
    const res=await fetch(path+'/changes/'+crypto.randomUUID(),{method:'PUT',headers:{...headers,'Content-Type':'application/json'},
      body:JSON.stringify({base_revision:current.scene.current_revision,operations})});
    return {status:res.status,total:(await res.json()).snapshot?.objects.length,ms:Math.round(performance.now()-start)};
  })()`);
  assert.equal(bulk.status,200);assert.equal(bulk.total,242);
  await cdp("Page.reload");
  await shapeCount(243);
  const timings=await js(`(async()=>{
    const samples=[];
    for(let i=0;i<8;i++){
      const start=performance.now();
      document.querySelector('[aria-label="Zoom in"]').click();
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      samples.push(Math.round(performance.now()-start));
    }
    return {shown:document.querySelectorAll('[data-shape]').length,samples};
  })()`);
  assert.ok(timings.shown>0 && timings.shown<150,JSON.stringify(timings));
  await cdp("Emulation.setDeviceMetricsOverride",{width:620,height:880,deviceScaleFactor:1,mobile:false});
  await wait(()=>js("document.querySelector('.viewport').getBoundingClientRect().width>=600 && document.querySelector('.viewport').getBoundingClientRect().width<=620"));
  assert.deepEqual(exceptions,[]);
  console.log("Editor browser workflow passed using real API and database.");
  console.log(JSON.stringify({bulk,timings}));
} catch(error) {
  console.error(lastStep, error.stack, await pageDebug?.().catch(()=>null), browserErrors.slice(-850));
  process.exitCode=1;
} finally {
  ws?.close();
  if(browser.exitCode===null){
    const closed=once(browser,"exit");
    browser.kill("SIGTERM");
    await closed.catch(()=>null);
  }
  await rm(profile,{recursive:true,force:true,maxRetries:5});
}

async function wait(action, timeout=5500){
  const end=Date.now()+timeout;
  let value;
  while(Date.now()<end){
    try {value=await action();if(value)return value;}catch(error){value=error;}
    await delay(50);
  }
  throw new Error("Timed out waiting for browser state: "+value);
}
