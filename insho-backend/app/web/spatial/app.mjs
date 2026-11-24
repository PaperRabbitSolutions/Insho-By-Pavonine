import {button, choice, dialog, el, field, input, number} from "./dom.mjs";
import {Editor} from "./editor.mjs";

const root=document.querySelector("#app"), account=document.querySelector("#account"), breadcrumb=document.querySelector("#breadcrumb");
const [,, ,projectId,,sceneId]=location.pathname.split("/");
const pathFor=(project,scene)=>"/workspace/projects/"+project+"/spatial"+(scene?"/"+scene:"");
let token=sessionStorage.getItem("insho-session"), user=null, editor=null;

async function request(path,method="GET",data) {
  const headers={Accept:"application/json"};
  if(token)headers.Authorization="Bearer "+token;
  if(data!==undefined)headers["Content-Type"]="application/json";
  let response;
  try {response=await fetch("/api/v1"+path,{method,headers,signal:AbortSignal.timeout(15000),body:data===undefined?undefined:JSON.stringify(data)});}
  catch {throw new Error("The server could not be reached. Check your connection and retry.");}
  if(!response.ok){
    const result=await response.json().catch(()=>({}));
    const detail=result.detail;
    const message=Array.isArray(detail)?detail.map(x=>(x.loc||[]).slice(1).join(".")+" "+x.msg).join("; "):detail;
    const error=new Error(response.status===401?"Your session has expired or the credentials are invalid.":message||"Request failed ("+response.status+").");
    error.status=response.status;
    throw error;
  }
  return response.status===204?null:response.json();
}

function signInForm(success) {
  const message=el("p",{class:"error",role:"alert"});
  const form=el("form",{class:"stack",on:{submit:async event=>{
    event.preventDefault();
    const data=new FormData(form), submit=form.querySelector("button");
    submit.disabled=true;
    try {
      const result=await request("/auth/login","POST",{email:data.get("email"),password:data.get("password")});
      if(editor&&user?.id!==result.user.id)throw new Error("Sign in as the same designer to continue the open scene.");
      token=result.access_token;user=result.user;
      sessionStorage.setItem("insho-session",token);
      renderAccount();
      success();
    } catch(error){message.textContent=error.message;}
    finally {submit.disabled=false;}
  }}},field("Email",input("email",user?.email,{type:"email",autocomplete:"username",required:true})),
    field("Password",input("password",null,{type:"password",autocomplete:"current-password",required:true})),
    el("button",{type:"submit",class:"primary"},"Sign in"),message);
  return form;
}
function renderAccount() {
  account.replaceChildren(el("span",{},user.full_name),
    button("Sign out",async()=>{
      if(editor?.busy||editor?.inspector.dirty){
        editor.message("Save or discard your pending edits before signing out.");
        return;
      }
      await request("/auth/logout","POST").catch(()=>null);
      sessionStorage.removeItem("insho-session");
      location.assign("/workspace");
    }));
}
function reauthenticate() {
  const node=dialog("Sign in again",signInForm(()=>node.close()));
}
function page(...content) {
  root.className="page";
  root.setAttribute("aria-busy","false");
  root.replaceChildren(...content);
}

async function all(path) {
  const output=[];
  for(let offset=0;;offset+=100){
    const rows=await request(path+(path.includes("?")?"&":"?")+"limit=100&offset="+offset);
    output.push(...rows);
    if(rows.length<100)return output;
  }
}

async function projects() {
  const organizations=await request("/organizations/mine");
  breadcrumb.replaceChildren("Projects");
  if(!organizations.length){page(el("h1",{},"Projects"),el("p",{},"Your account has no organization memberships."));return;}
  const select=choice("organization",organizations.map(o=>[o.id,o.name]),organizations[0].id,{"aria-label":"Organization"});
  const list=el("ul",{class:"projects","aria-live":"polite"});
  let sequence=0;
  async function load(){
    const id=++sequence;list.replaceChildren(el("li",{},"Loading projects…"));
    try{
      const items=await all("/projects/?organization_id="+select.value);
      if(id!==sequence)return;
      list.replaceChildren(...(items.length?items.map(p=>el("li",{},el("a",{href:pathFor(p.id)},p.name,el("span",{class:"hint"},"Spatial scenes →")))):
        [el("li",{},"No projects are available in this organization.")]));
    }catch(error){if(id===sequence)list.replaceChildren(el("li",{class:"error"},error.message));}
  }
  select.addEventListener("change",load);
  page(el("h1",{},"Projects"),el("div",{class:"row"},el("span",{},"Organization"),select),list);
  await load();
}

async function projectWorkspace() {
  const project=await request("/projects/"+projectId);
  breadcrumb.replaceChildren(el("a",{href:"/workspace"},"Projects")," / ",el("a",{href:pathFor(project.id)},project.name)," / Spatial");
  if(sceneId){
    const state=await request("/spatial/scenes/"+sceneId+"/editor");
    if(state.scene.project_id!==project.id)throw new Error("This scene does not belong to the selected project.");
    breadcrumb.append(" / "+state.scene.name);
    root.setAttribute("aria-busy","false");
    editor=new Editor(root,request,state,project,reauthenticate);
    return;
  }
  const [scenes,events,organizations]=await Promise.all([request("/spatial/projects/"+projectId+"/scenes"),
    all("/projects/"+projectId+"/events"),request("/organizations/mine")]);
  const isEditor=["owner","admin","designer"].includes(organizations.find(o=>o.id===project.organization_id)?.role);
  const eventChoices=[["","All events and project scenes"],["project","Project scenes"],...events.map(e=>[e.id,e.name])];
  const filter=choice("scope",eventChoices,new URLSearchParams(location.search).get("event")||"",{"aria-label":"Event filter"});
  const list=el("ul",{class:"scenes"});
  function render(){
    const items=scenes.filter(s=>!filter.value || (filter.value==="project"?!s.event_id:s.event_id===filter.value));
    list.replaceChildren(...(items.length?items.map(s=>el("li",{},el("a",{href:pathFor(project.id,s.id)},s.name,
      el("span",{class:"hint"},events.find(e=>e.id===s.event_id)?.name||"Project scene")))):
      [el("li",{},"No spatial scenes in this scope.")]));
  }
  filter.addEventListener("change",render);
  const error=el("p",{class:"error",role:"alert"});
  const form=el("form",{class:"stack",on:{submit:async ev=>{
    ev.preventDefault();
    const data=new FormData(form), submit=form.querySelector("button"), scope=data.get("event");
    submit.disabled=true;
    try {
      const scene=await request("/spatial/projects/"+project.id+"/scenes","POST",{name:data.get("name").trim(),
        event_id:scope||null,dimensions:{width_m:+data.get("width"),depth_m:+data.get("depth"),height_m:+data.get("height")}});
      location.assign(pathFor(project.id,scene.id));
    }catch(err){error.textContent=err.message;submit.disabled=false;}
  }}},field("Scene name",input("name",null,{required:true,maxlength:160})),
    field("Event",choice("event",[["","Project scene"],...events.map(e=>[e.id,e.name])],events.some(e=>e.id===filter.value)?filter.value:"")),
    el("div",{class:"fields"},["width","depth","height"].map(k=>field(k.replace(/^./,x=>x.toUpperCase())+" (m)",
      number(k,null,{required:true,min:.000001,max:99999999})))),
    el("button",{class:"primary",type:"submit"},"Create scene"),error);
  page(el("h1",{},"Spatial scenes"),el("div",{class:"row"},el("span",{},"Scope"),filter),list,
    isEditor?el("details",{},el("summary",{},"New measured scene"),form):null);
  render();
}
async function openWorkspace() {
  try {
    if(!token){page(el("h1",{},"Sign in to INSHO"),signInForm(openWorkspace));return;}
    if(!user)user=await request("/auth/me");
    renderAccount();
    if(projectId)await projectWorkspace();
    else await projects();
  } catch(error) {
    if(error.status===401){
      user=null;token=null;sessionStorage.removeItem("insho-session");
      page(el("h1",{},"Sign in to INSHO"),signInForm(openWorkspace));
    }else page(el("h1",{},"Workspace unavailable"),el("p",{class:"error"},error.message),
      el("a",{href:"/workspace"},"Back to projects"));
  }
}
openWorkspace();
