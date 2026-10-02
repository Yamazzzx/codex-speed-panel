"use strict";
const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search), demoMode = params.get("demo") === "1";
const number = new Intl.NumberFormat("zh-CN");
const clock = new Intl.DateTimeFormat("zh-CN", {hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false});
const exact = value => value == null ? "—" : number.format(value);
const compact = value => value == null ? "—" : value >= 1e6 ? (value/1e6).toFixed(2)+"M" : value >= 1e4 ? (value/1e3).toFixed(1)+"k" : value >= 1e3 ? (value/1e3).toFixed(2)+"k" : exact(value);
const speed = value => value == null ? "—" : Number(value).toFixed(1);
const duration = value => value == null ? "—" : value < 60 ? value.toFixed(1)+" 秒" : (value/60).toFixed(1)+" 分";
let selected = "", manualChat = "", choicesKey = "", data = null, mode = "turns", usageScope = "chat", openRecord = "", historyKey = "", chartKey = "", chartSamples = [], fetching = false, stopped = false, sourceLoaded = false, sourceReady = false, sourceUpdating = false, viewGeneration = 0;
if(params.has("thread")) { params.delete("thread"); history.replaceState(null,"",location.pathname+(params.size?"?"+params.toString():"")); }
$("chat-picker").addEventListener("change",()=>{ manualChat=$("chat-picker").value; viewGeneration++; clearData(); refresh(); });
function clearData() { data=null; selected=""; openRecord=""; historyKey=""; chartKey="";chartSamples=[];$("dashboard").hidden=true;$("chat-name").textContent="请选择要查看的聊天";document.title="Codex Pulse"; }
function renderChoices(threads) {
  if(manualChat && !threads.some(thread=>thread.id===manualChat))threads=[...threads,{id:manualChat,title:data?.thread.title||"已选择的聊天"}];
  const key=JSON.stringify(threads);if(key===choicesKey)return;choicesKey=key;
  const placeholder=textNode("option","选择聊天");placeholder.value="";
  const options=threads.map(thread=>{const label=thread.title.length>28?thread.title.slice(0,28)+"…":thread.title,option=textNode("option",label);option.value=thread.id;return option;});
  $("chat-picker").replaceChildren(placeholder,...options);$("chat-picker").value=manualChat;
}
function showSource(source) {
  sourceLoaded=true;sourceReady=!!source.ready;$("source-status").textContent=sourceReady?"已就绪":"需要设置";
  $("source-path").value=source.directory||"";$("source-message").textContent=source.message||"";$("source-settings").open=!sourceReady;
}
async function loadSource() {
  const response=await fetch("/api/source",{signal:AbortSignal.timeout(7000),cache:"no-store"}),source=await response.json();
  if(!response.ok||source.error)throw new Error(source.error||"暂时无法检查数据目录");showSource(source);
}
async function configureSource(directory) {
  if(sourceUpdating)return;sourceUpdating=true;viewGeneration++;manualChat="";choicesKey="";clearData();$("chat-picker").replaceChildren(textNode("option","选择聊天"));
  $("source-save").disabled=true;$("source-rescan").disabled=true;
  try {
    const response=await fetch("/api/source",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({directory}),signal:AbortSignal.timeout(10000)}),source=await response.json();
    if(!response.ok||source.error)throw new Error(source.error||"数据目录设置失败");showSource(source);$("error").hidden=true;
  } catch(error) {sourceLoaded=false;sourceReady=false;$("source-settings").open=true;$("source-message").textContent=error.message;}
  finally {sourceUpdating=false;$("source-save").disabled=false;$("source-rescan").disabled=false;refresh();}
}
$("source-form").addEventListener("submit",event=>{event.preventDefault();configureSource($("source-path").value.trim());});
$("source-rescan").addEventListener("click",()=>configureSource(""));
if(demoMode)$("source-settings").hidden=true;
function setTheme(value) { document.documentElement.dataset.theme = value; try {localStorage.setItem("codex-speed-theme",value);} catch {} }
try { const theme=localStorage.getItem("codex-speed-theme"); if(theme) setTheme(theme); } catch {}
$("theme").addEventListener("click",()=>{ const dark=document.documentElement.dataset.theme==="dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme:dark)").matches); setTheme(dark?"light":"dark"); });
function setUsageScope(value) { usageScope=value; $("scope-chat").setAttribute("aria-pressed",String(value==="chat")); $("scope-turn").setAttribute("aria-pressed",String(value==="turn")); renderUsage(); }
$("scope-chat").addEventListener("click",()=>setUsageScope("chat")); $("scope-turn").addEventListener("click",()=>setUsageScope("turn"));
function renderUsage() {
  const turn=data?.turns?.at(-1), usage=usageScope==="chat" ? data?.total_usage : turn?.requests ? turn : null;
  $("usage-label").textContent=usageScope==="chat" ? "整个聊天累计消耗" : turn?.partial ? "这条消息的消耗（记录不完整）" : "这条消息的消耗";
  $("usage-range").textContent=usageScope==="chat" ? "这条聊天从开始到现在，所有消息的累计消耗。" : "你最近发出的那条消息，以及 Codex 处理它时的全部模型调用。"+(turn?.partial?"较早记录不完整，这里只显示已记录的部分。":turn?.aborted?"处理已停止。":turn&&!turn.complete?"还在处理中，数字会继续更新。":"");
  $("usage-total").textContent=exact(usage?.total);
  for(const field of ["input","cached","output","reasoning"]) { $("usage-"+field).textContent=compact(usage?.[field]); $("usage-"+field).title=exact(usage?.[field])+" Token"; }
}
function setMode(value,focus=false) {
  mode=value; openRecord=""; historyKey="";
  for(const [id,key] of [["request-tab","requests"],["turn-tab","turns"]]) { const active=key===mode; $(id).setAttribute("aria-selected",String(active)); $(id).tabIndex=active?0:-1; if(active && focus) $(id).focus(); }
  $("history-panel").setAttribute("aria-labelledby",mode==="requests"?"request-tab":"turn-tab"); renderHistory();
}
for(const [id,key] of [["request-tab","requests"],["turn-tab","turns"]]) {
  $(id).addEventListener("click",()=>setMode(key));
  $(id).addEventListener("keydown",event=>{ if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return; event.preventDefault(); setMode(event.key==="Home"?"turns":event.key==="End"?"requests":mode==="requests"?"turns":"requests",true); });
}
function textNode(tag,value,className) { const element=document.createElement(tag); element.textContent=value; if(className)element.className=className; return element; }
function numberCell(value) { const cell=textNode("td",compact(value)); cell.title=exact(value)+" Token"; cell.setAttribute("aria-label",cell.title); return cell; }
function renderHistory() {
  const turns=mode==="turns", records=(turns?data?.turns:data?.samples) || [];
  const recent=records.slice(-8).reverse(), key=JSON.stringify([mode,openRecord,recent]); if(key===historyKey)return; historyKey=key;
  $("time-header").textContent=turns?"开始处理":"完成时间"; $("middle-header").textContent=turns?"模型调用":"速度";
  $("detail-count").textContent=`最近 ${recent.length} ${turns?"条消息":"次调用"}`; $("history-empty").hidden=recent.length>0;
  $("history-range").textContent=turns?"每一行对应你发的一条消息，合计包含处理这条消息时的所有模型调用。":"处理一条消息时，Codex 可能多次请求模型。这里每一行代表一次模型调用。";
  const rows=[];
  for(const record of recent) {
    const timestamp=turns?record.start:record.at, recordKey=mode+":"+timestamp, expanded=openRecord===recordKey, row=document.createElement("tr"); if(expanded)row.className="row-open";
    const toggleCell=document.createElement("td"), toggle=textNode("button",expanded?"⌄":"›","row-toggle"); toggle.type="button";
    toggle.setAttribute("aria-expanded",String(expanded)); toggle.setAttribute("aria-label",`${expanded?"收起":"查看"} ${clock.format(new Date(timestamp*1000))} 的 Token 构成`);
    toggle.addEventListener("click",()=>{ openRecord=expanded?"":recordKey; historyKey=""; renderHistory(); }); toggleCell.append(toggle); row.append(toggleCell);
    const timeCell=document.createElement("td"), date=new Date(timestamp*1000), time=textNode("time",clock.format(date).slice(0,5)); time.dateTime=date.toISOString(); timeCell.title=date.toLocaleString("zh-CN");
    if(turns && (!record.complete || record.partial)) { const dot=textNode("span","","time-status"); dot.title=record.partial?"较早记录不完整":record.aborted?"处理已停止":"正在处理这条消息"; timeCell.append(dot); } timeCell.append(time); row.append(timeCell);
    const middle=textNode("td",turns?record.requests+" 次":speed(record.tps)); middle.title=turns?(record.partial?"只统计已记录的调用":record.aborted?"处理已停止":record.complete?"这条消息已处理完":"正在处理这条消息"):duration(record.seconds)+" · Token/s"; row.append(middle);
    const known=!turns || record.requests>0; row.append(numberCell(known?record.output:null),numberCell(known?record.total:null)); rows.push(row);
    if(expanded) {
      const extra=document.createElement("tr"); extra.className="breakdown-row"; const cell=document.createElement("td"); cell.colSpan=5; const list=document.createElement("dl"); list.className="row-breakdown";
      for(const [field,label] of [["input","输入"],["cached","缓存输入"],["output","输出"],["reasoning","思考输出"]]) { const group=document.createElement("div"); group.append(textNode("dt",label),textNode("dd",exact(known?record[field]:null))); list.append(group); }
      const note=turns?`${record.partial?"较早记录不完整，显示已记录部分":record.aborted?"处理已停止":record.complete?"这条消息已处理完":"正在处理这条消息"} · 模型调用 ${record.requests} 次`:`模型耗时 ${duration(record.seconds)} · 可见输出 ${exact(record.visible)} Token`;
      list.append(textNode("small",note)); cell.append(list); extra.append(cell); rows.push(extra);
    }
  }
  $("history-rows").replaceChildren(...rows);
}
function svg(name,attrs={},label) { const element=document.createElementNS("http://www.w3.org/2000/svg",name); for(const [key,value]of Object.entries(attrs))element.setAttribute(key,value); if(label!=null)element.textContent=label; return element; }
function draw(samples) {
  chartSamples=samples; const chart=$("chart"); chart.replaceChildren(); chart.toggleAttribute("hidden",!samples.length); $("chart-empty").hidden=!!samples.length; if(!samples.length)return;
  const width=Math.max(250,chart.getBoundingClientRect().width||600),height=150,left=30,right=6,top=12,bottom=23,max=Math.max(5,Math.ceil(Math.max(...samples.map(s=>s.tps))/5)*5); chart.setAttribute("viewBox",`0 0 ${width} ${height}`);
  const x=i=>samples.length===1?(left+width-right)/2:left+i*(width-left-right)/(samples.length-1), y=value=>height-bottom-value/max*(height-top-bottom);
  for(let i=0;i<=2;i++){ const value=max*i/2,pos=y(value); chart.append(svg("line",{x1:left,x2:width-right,y1:pos,y2:pos,class:"grid"}),svg("text",{x:left-7,y:pos+3,"text-anchor":"end"},Math.round(value))); }
  const points=samples.map((s,i)=>[x(i),y(s.tps)]),path=points.map((p,i)=>(i?"L":"M")+p.join(",")).join(" ");
  if(points.length>1)chart.append(svg("path",{d:path+` L${points.at(-1)[0]},${height-bottom} L${points[0][0]},${height-bottom} Z`,class:"area"})); chart.append(svg("path",{d:path,class:"trend"}));
  points.forEach((p,i)=>{ const point=svg("circle",{cx:p[0],cy:p[1],r:i===points.length-1?3:2,class:"point"+(i===points.length-1?" last":"")}); point.append(svg("title",{},`${clock.format(new Date(samples[i].at*1000))} · ${speed(samples[i].tps)} Token/s`));chart.append(point); });
  for(const i of [...new Set([0,samples.length-1])])chart.append(svg("text",{x:x(i),y:height-5,"text-anchor":samples.length===1?"middle":i===0?"start":"end"},clock.format(new Date(samples[i].at*1000)).slice(0,5)));
}
function render(snapshot) {
  data=snapshot; $("dashboard").hidden=false; $("unbound").hidden=true; $("error").hidden=true;
  $("chat-name").textContent=data.thread.title; $("chat-name").title=data.thread.title; $("scope-label").textContent=data.demo?"合成数据 · 非实测":"手动查看"; $("demo-badge").hidden=!data.demo; $("stop-monitor").hidden=!!data.demo;$("chat-picker").hidden=!!data.demo;
  const last=data.latest; $("speed").textContent=speed(last?.tps); $("average").textContent=speed(data.average); $("duration").textContent=duration(last?.seconds); $("model").textContent=data.model||"等待模型数据"; $("effort").textContent=data.effort?"思考 "+data.effort:"";
  $("status").dataset.phase=data.phase; $("status").querySelector("span").textContent=({generating:"生成中",tools:"工具运行中",waiting:"处理中",idle:"等待新响应",demo:"演示数据"})[data.phase]||"已连接";
  $("activity").textContent=data.phase==="generating"?"已进行 "+duration(data.elapsed):last?"最近输出 "+compact(last.output)+" Token":"";
  const samples=data.samples.slice(-24),key=JSON.stringify(samples);if(key!==chartKey){chartKey=key;draw(samples);} $("sample-count").textContent=`最近 ${samples.length} 次模型调用`;
  $("plain-speed").textContent=data.plain?"正文输出 "+speed(data.plain.text_tps)+" Token/s":""; renderUsage();renderHistory();
  $("updated").textContent="本机读取 · "+clock.format(new Date(data.updated*1000)); document.title=last?`${speed(last.tps)} t/s · Codex Pulse`:"Codex Pulse";
}
async function refresh() {
  if(stopped||fetching||sourceUpdating||document.hidden)return; fetching=true;const generation=viewGeneration;
  try {
    if(!demoMode) {
      if(!sourceLoaded)await loadSource();if(generation!==viewGeneration)return;
      if(!sourceReady){clearData();$("unbound").hidden=false;$("detect-title").textContent="设置 Codex 数据目录";$("detect-note").textContent="在上方填写数据文件夹，或点击重新检测。";return;}
      const response=await fetch("/api/context",{signal:AbortSignal.timeout(5000),cache:"no-store"}),context=await response.json();
      if(generation!==viewGeneration)return;
      if(!response.ok||context.error)throw new Error(context.error||"暂时无法读取聊天列表");renderChoices(context.threads||[]);
      const next=manualChat;
      if(next!==selected) {clearData();selected=next;}
      if(!selected) {
        clearData();$("error").hidden=true;$("scope-label").textContent="选择聊天";$("unbound").hidden=false;
        $("detect-title").textContent="选择要查看的聊天";
        $("detect-note").textContent=context.threads?.length?"从上方菜单选择一条聊天，面板会显示它的速度和用量。":"还没有可查看的本地聊天，请先在 Codex 中发一条消息。";return;
      }
    }
    const endpoint=demoMode?"/api/demo":"/api/snapshot?thread="+encodeURIComponent(selected),response=await fetch(endpoint,{signal:AbortSignal.timeout(7000),cache:"no-store"}),snapshot=await response.json();
    if(generation!==viewGeneration)return;
    if(!response.ok||snapshot.error)throw new Error(snapshot.error||"暂时无法读取用量"); if(!demoMode && snapshot.thread.id!==selected)throw new Error("聊天编号不匹配，已停止展示这份数据"); render(snapshot);
  } catch(error) { if(generation!==viewGeneration)return;clearData();$("error").hidden=false;$("error").textContent="读取暂时中断："+error.message;$("status").dataset.phase="offline";$("status").querySelector("span").textContent="连接中断"; }
  finally{fetching=false;if(generation!==viewGeneration&&!sourceUpdating)queueMicrotask(refresh);}
}
$("stop-monitor").addEventListener("click",async()=>{ $("stop-monitor").disabled=true;try {const response=await fetch("/api/stop",{method:"POST"});if(!response.ok)throw new Error("关闭失败");stopped=true;$("status").querySelector("span").textContent="监测已关闭";$("stop-monitor").textContent="已关闭";}catch(error){$("stop-monitor").disabled=false;$("error").hidden=false;$("error").textContent=error.message;} });
document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});new ResizeObserver(()=>draw(chartSamples)).observe(document.querySelector('.chart-wrap'));setInterval(refresh,3000);refresh();
