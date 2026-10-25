"use strict";
const $ = id => document.getElementById(id);
const terminal = new Set(["completed", "needs_review", "failed", "cancelled", "interrupted"]);
const statuses = new Set([...terminal, "queued", "waiting", "running", "awaiting_approval"]);
const state = {selected: null, mission: null, missions: [], signedIn: false, busy: false, pending: null, autoApprove: false};
const examples = {
  python: "Create math_utils.py with add(a, b) and multiply(a, b). Use only the Python standard library. Check Python syntax and review both functions. Do not execute code.",
  json: "Create settings.json with project name PRIMUS, offline true, and retries 3. Verify it is valid JSON and contains the requested values. No other files are needed.",
  html: "Create a complete index.html landing page for PRIMUS with a heading, three feature cards, and a Get Started button. Use embedded CSS and no external assets. Check the HTML and review the requested elements."
};
function el(tag, cls, text) { const node = document.createElement(tag); if(cls) node.className = cls; if(text !== undefined) node.textContent = text; return node; }
function label(value) { return String(value || "waiting").replaceAll("_", " "); }
function badge(node, status) { const roleMarker=node.classList.contains("role-status")?" role-status":""; node.textContent = label(status); node.className = "pill " + (statuses.has(status) || ["online","offline"].includes(status) ? status : "") + roleMarker; }
function error(message) { $("error").textContent = message; $("error").hidden = !message; }
async function api(path, data) {
  const options = {credentials:"same-origin", cache:"no-store", headers:{"X-Primus-Control":"1"}};
  if(data !== undefined) { options.method="POST"; options.headers["Content-Type"]="application/json"; options.body=JSON.stringify(data); }
  const response = await fetch(path, options);
  const body = await response.json();
  if(!response.ok) {
    if(response.status === 401) { state.signedIn=false; $("signin").hidden=false; }
    throw new Error(body.error || "Local request failed");
  }
  return body;
}
function renderList() {
  $("mission-list").replaceChildren();
  if(!state.missions.length) $("mission-list").append(el("p","muted small","Your missions will appear here."));
  for(const mission of state.missions) {
    const button = el("button", "mission-item" + (state.selected===mission.id ? " selected" : ""));
    button.append(el("strong","",mission.title || "Mission " + mission.id.slice(0,8)), el("small","",label(mission.status) + " · " + mission.id.slice(0,8)));
    button.addEventListener("click",()=>{state.selected=mission.id;state.mission=null; refresh();});
    $("mission-list").append(button);
  }
  const active = state.missions.filter(m=>!terminal.has(m.status)).length;
  $("active-count").textContent=active;
  $("complete-count").textContent=state.missions.filter(m=>m.status==="completed").length;
  $("launch").disabled=!state.signedIn || active>0;
}
function renderApproval(mission) {
  const pending=mission.pending;
  $("approval").hidden=!pending;
  $("approval-notice").hidden=!pending;
  if(!pending) { state.pending=null; return; }
  if(state.pending!==pending.id) {
    state.pending=pending.id;
    $("approval-prompt").textContent=pending.prompt;
    $("approve").disabled=false;$("reject").disabled=false;
    requestAnimationFrame(()=>$("approval").scrollIntoView({block:"center"}));
  }
  const remaining=Math.max(0,Math.ceil(pending.expires_at-Date.now()/1000));
  $("approval-expiry").textContent=remaining+"s to review";
  $("approval-notice-time").textContent=remaining?remaining+"s remaining · nothing runs before approval":"Approval expired · nothing was executed";
  if(!remaining) { $("approve").disabled=true; $("reject").disabled=true; }
}
function renderMission() {
  const mission=state.mission;
  if(!mission) return;
  $("mission-title").textContent=mission.title || mission.goal || "Mission";
  badge($("mission-status"), mission.status);
  const attention=["failed","needs_review","cancelled","interrupted"].includes(mission.status);
  $("mission-attention").hidden=!attention;
  $("mission-attention").textContent=attention?(mission.summary || mission.review?.summary || "This mission stopped. Review the activity and verification evidence below."):"";
  $("file-count").textContent=(mission.artifacts||[]).length;
  $("cancel").hidden=terminal.has(mission.status);
  $("workspace").textContent=mission.workspace;
  for(const role of ["planner","coder","verifier"]) {
    const info=(mission.roles||{})[role]||{status:"waiting",attempts:0};
    const card=$("agent-"+role);
    card.className="agent panel "+(statuses.has(info.status)?info.status:"");
    badge(card.querySelector(".role-status"),info.status);
    card.querySelector(".role-attempt").textContent=info.attempts?"PASS "+info.attempts:"";
  }
  $("plan").replaceChildren();$("criteria").replaceChildren();
  const plan=mission.plan;
  for(const step of plan?.steps||[]) $("plan").append(el("li","",step));
  for(const check of plan?.acceptance||[]) $("criteria").append(el("li","",check));
  if(!plan) { $("plan").append(el("li","empty","Waiting for a validated plan.")); $("criteria").append(el("li","empty","Acceptance criteria have not been defined.")); }
  const feed=$("events"), atBottom=feed.scrollHeight-feed.scrollTop-feed.clientHeight<60;
  feed.replaceChildren();
  for(const event of mission.events||[]) {
    const row=el("div","event "+event.kind+(event.ok===false?" failure":event.ok===true?" success":""));
    const timestamp=el("time","",new Date(event.time).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"}));
    const content=el("div");content.append(el("strong","",event.role||"system"),el("p","",event.message));row.append(timestamp,content);feed.append(row);
  }
  if(!(mission.events||[]).length) feed.append(el("p","empty","No session events available."));
  if(atBottom) feed.scrollTop=feed.scrollHeight;
  $("artifacts").replaceChildren();
  for(const [index, file] of (mission.artifacts||[]).entries()) {
    const row=el("div","artifact-row");
    const button=el("button","artifact");const info=el("span");
    info.append(el("strong","",file.name),el("small","",file.sha256.slice(0,16)+"…"));
    button.append(info,el("span","",file.bytes.toLocaleString()+" bytes ↗"));
    button.addEventListener("click",()=>openFile(mission.id,index));row.append(button);
    if(/\.(py|sh)$/i.test(file.name)) {
      const run=el("button","secondary run-artifact","Run project");
      run.setAttribute("aria-label","Run "+file.name+" in sandbox");
      run.disabled=!terminal.has(mission.status)||state.missions.some(m=>!terminal.has(m.status));
      run.addEventListener("click",()=>runArtifact(mission.id,index));row.append(run);
    }
    $("artifacts").append(row);
  }
  if(!(mission.artifacts||[]).length) $("artifacts").append(el("p","empty","No artifacts have been inspected yet."));
  $("artifact-count").textContent=(mission.artifacts||[]).length+" files";
  $("checks").replaceChildren();
  for(const check of mission.checks||[]) {
    const row=el("div","check "+(check.passed===false?"failed":check.passed===null?"unknown":""));
    const info=el("div");info.append(el("strong","",check.file+" · "+label(check.check)),el("small","",check.detail));
    if(check.output_dir) info.append(el("small","mono","Sandbox outputs: "+check.output_dir));
    row.append(el("span","icon",check.passed===true?"✓":check.passed===false?"×":"○"),info);$("checks").append(row);
  }
  if(!(mission.checks||[]).length) $("checks").append(el("p","empty","No verification evidence yet."));
  $("review").replaceChildren();
  if(mission.review) {
    $("review").append(el("strong","","INDEPENDENT MODEL REVIEW"),el("span","",mission.review.summary));
    const issues=el("ul");for(const issue of mission.review.issues||[]) issues.append(el("li","",issue));$("review").append(issues);
  }
  $("notes").replaceChildren();
  if(mission.summary) $("notes").append(el("p","",mission.summary));
  for(const note of mission.notes||[]) $("notes").append(el("p","",note));
  renderExecutions(mission);renderApproval(mission);renderElapsed();
}
function renderExecutions(mission) {
  const executions=mission.executions||[];
  $("execution-panel").hidden=!executions.length;
  const repairs=mission.runtime_repairs||{used:0,limit:1};
  $("runtime-repair-state").textContent="Automatic runtime repairs: "+repairs.used+"/"+repairs.limit+(state.autoApprove?". Corrections and reruns are authorized automatically.":". Corrections and reruns each require separate exact approval.");
  const holder=$("executions");holder.replaceChildren();
  for(const run of executions) {
    const card=el("article","execution");
    const heading=el("div","panel-heading");const status=el("span","pill",label(run.status));
    heading.append(el("h3","",run.file),status);card.append(heading);
    card.append(el("p","mono small","Exit code: "+(run.exit_code===null?"not available":run.exit_code)+" · "+label(run.kind)));
    if(run.project_files?.length) {
      const manifest=el("details","runtime-manifest");
      manifest.append(el("summary","","Read-only project snapshot · "+run.project_files.length+" files"));
      const list=el("pre","mono small");list.textContent=run.project_files.map(f=>f.name+" · "+f.sha256).join("\n");manifest.append(list);card.append(manifest);
    }
    if(run.error) card.append(el("p","execution-error",run.error));
    if(run.approval_status==="expired") {
      const index=(mission.artifacts||[]).findIndex(f=>f.name===run.file);
      if(index>=0) {
        const retry=el("button","secondary request-fresh-run","Request fresh run approval");
        retry.setAttribute("aria-label","Request fresh approval for "+run.file);
        retry.disabled=!terminal.has(mission.status)||state.missions.some(m=>!terminal.has(m.status));
        retry.addEventListener("click",()=>runArtifact(mission.id,index));card.append(retry);
      }
    }
    for(const stream of ["stdout","stderr"]) {
      card.append(el("h4","",stream.toUpperCase()+(run[stream+"_truncated"]?" · truncated at 4 KB":"")));
      const empty=run.exit_code!==null?"(empty)":run.status==="not_executed"?"Not executed; no output was captured.":["cancelled","failed"].includes(run.status)?"Execution stopped; no completed output was captured.":"Waiting for approval or execution; no output captured yet.";
      card.append(el("pre","runtime-"+stream,run[stream]||empty));
    }
    if(run.output_dir) card.append(el("p","workspace-path","Output folder: "+run.output_dir));
    holder.append(card);
  }
}
async function runArtifact(id,index) {
  error("");
  try {
    state.selected=id;
    state.mission=await api("/missions/"+id+"/run",{artifact_index:index});
    renderMission();await refresh();
    $(state.mission?.pending?"approval":"execution-panel").scrollIntoView({block:"center"});
  } catch(e) {error(e.message);}
}
function renderElapsed() {
  const m=state.mission;if(!m) return;
  const end=terminal.has(m.status)?Date.parse(m.updated_at):Date.now();
  const seconds=Math.max(0,Math.floor((end-Date.parse(m.created_at))/1000));
  $("elapsed").textContent=String(Math.floor(seconds/60)).padStart(2,"0")+":"+String(seconds%60).padStart(2,"0");
}
async function openFile(id,index) {
  try {
    const file=await api("/missions/"+id+"/files/"+index);
    $("file-title").textContent=file.path;$("file-content").textContent=file.content;
    $("file-hash").textContent=(file.changed?"Changed since verification · ":"SHA-256 · ")+file.sha256;
    $("file-dialog").showModal();
  } catch(e) {error(e.message);}
}
async function refresh() {
  if(state.busy||!state.signedIn) return;
  state.busy=true;
  try {
    const data=await api("/missions");state.missions=data.missions;
    badge($("connection"),data.stopped?"offline":"online");$("connection").textContent=data.stopped?"Kill switch active":"Local API connected";
    $("root-path").textContent=data.root;$("root-path").title=data.root;$("model").textContent=data.model;
    state.autoApprove=data.approval_mode==="automatic";
    $("approval-mode").textContent=state.autoApprove?"Automatic approval is ON — tool actions, file replacements and sandbox runs proceed without manual review.":"Manual approval is ON — sensitive actions wait for your exact confirmation.";
    if(!state.selected&&data.missions.length) state.selected=(data.missions.find(m=>!terminal.has(m.status))||data.missions[0]).id;
    renderList();
    if(state.selected) {state.mission=await api("/missions/"+state.selected);renderMission();}
    if(data.stopped) $("launch").disabled=true;
    $("history-list").replaceChildren();
    for(const task of data.history||[]) {
      const row=el("div","history-row");row.append(el("span","mono",task.id.slice(0,8)+" · "+(task.agent_label||"assistant")));
      const status=el("span");badge(status,task.status);row.append(status);$("history-list").append(row);
    }
  } catch(e) {badge($("connection"),"offline");$("connection").textContent="Connection needs attention";error(e.message);}
  finally {state.busy=false;}
}
$("mission-form").addEventListener("submit",async event=>{
  event.preventDefault();error("");$("launch").disabled=true;
  try {const mission=await api("/missions",{goal:$("goal").value.trim(),run_tests:$("runtime").checked});state.selected=mission.id;state.mission=mission;renderMission();await refresh();}
  catch(e){error(e.message);$("launch").disabled=false;}
});
for(const button of document.querySelectorAll("[data-example]")) button.addEventListener("click",()=>{$("goal").value=examples[button.dataset.example];$("goal").focus();});
$("cancel").addEventListener("click",async()=>{try{await api("/missions/"+state.selected+"/cancel",{});await refresh();}catch(e){error(e.message);}});
for(const [id,approved] of [["approve",true],["reject",false]]) $(id).addEventListener("click",async()=>{
  const pending=state.pending;if(!pending) return;
  $("approve").disabled=true;$("reject").disabled=true;
  try{await api("/missions/"+state.selected+"/approval",{approval_id:pending,approved});await refresh();}catch(e){error(e.message);}
});
$("close-file").addEventListener("click",()=>$("file-dialog").close());
$("review-action").addEventListener("click",()=>$("approval").scrollIntoView({block:"center"}));
async function boot() {
  const ticket=new URLSearchParams(location.hash.slice(1)).get("ticket");
  history.replaceState(null,"",location.pathname);
  try {
    if(ticket) await api("/mission/session",{ticket});
    state.signedIn=true;$("signin").hidden=true;await refresh();
  } catch(e){state.signedIn=false;$("signin").hidden=false;error(e.message);}
}
setInterval(()=>{refresh();renderElapsed();},1000);
boot();
