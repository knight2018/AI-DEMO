/* SPEC-011：聊天消息使用安全文本渲染，直接消费服务端事件流。 */
'use strict';
const $ = id => document.getElementById(id);
let current = null, busy = false, deviceConfirmed = false, selectedAction = null, controller = null;
const questionNames = {};
const intentNames = {wifi:'Wi-Fi / App',recording:'存储卡 / 录像',power:'供电 / 启动',other_support:'其他产品问题',unrelated:'无关话题',unclear:'信息不足'};
function element(tag, text = '', className) {
  const node = document.createElement(tag); node.textContent = text;
  if (className) node.className = className;
  return node;
}
function message(text) { $('notice').textContent = text; }
function controls() {
  document.querySelectorAll('button').forEach(button => button.disabled = busy);
  $('chat-input').disabled = busy || ['resolved','handoff'].includes(current?.session.status);
  $('send').disabled = $('chat-input').disabled;
  $('choose-device').disabled = busy || Boolean(current);
  $('connection-state').textContent = busy ? '正在回复…' : current ? '本次对话已保存' : '随时可以描述你的问题';
}
function scrollChat() { const box=$('messages'); box.scrollTop=box.scrollHeight; }
function bubble(role, text, className = '') {
  const row=element('article','',`message ${role} ${className}`);
  row.append(element('div',role==='user' ? '你' : '售后助手','sender'));
  const body=element('div',text,'bubble'); row.append(body); $('messages').append(row); scrollChat(); return body;
}
function option(label, action, parent) {
  const button=element('button',label,'option');button.type='button';button.disabled=busy;
  button.addEventListener('click',action);parent.append(button);return button;
}
async function request(path, data) {
  const response=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Support-Client':'local-web'},body:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'输入格式不正确，请检查后重试。');
  return result;
}
async function run(operation,pending='') {
  if(busy)return;
  busy=true;controls();message(pending);
  try { await operation();message(''); }
  catch(error) { message(error.name==='AbortError'?'连接已中断，请恢复会话后继续。':error.message || '请求失败，请重试。'); }
  finally { busy=false;controls(); }
}
async function consumeSSE(response, receive) {
  if(!response.ok){const result=await response.json();throw new Error(typeof result.detail==='string'?result.detail:'请求未成功，请检查输入后重试。');}
  if(!response.body)throw new Error('当前连接没有返回消息流。');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',done=false;
  function parseFrames(){
    let match;
    while((match=/\r?\n\r?\n/.exec(buffer))){
      const frame=buffer.slice(0,match.index);buffer=buffer.slice(match.index+match[0].length);
      let event='message';const data=[];
      for(const line of frame.split(/\r?\n/)){
        if(line.startsWith('event:'))event=line.slice(6).trim();
        if(line.startsWith('data:'))data.push(line.slice(5).trimStart());
      }
      if(!data.length)continue;
      const payload=JSON.parse(data.join('\n'));
      if(event==='error')throw new Error(payload.text);
      receive(event,payload);
      if(event==='done')done=true;
    }
  }
  try {
    while(!done){const next=await reader.read();if(next.done){buffer+=decoder.decode();parseFrames();break;}buffer+=decoder.decode(next.value,{stream:true});parseFrames();}
    if(!done)throw new Error('回复连接中断，未完成的内容没有作为正式建议保存。请恢复会话后继续。');
  } finally { await reader.cancel();reader.releaseLock(); }
}
function draft(text) {
  let body=$('stream-draft');
  if(!body){body=bubble('assistant','');body.id='stream-draft';body.append(element('small','正在生成，建议尚未确认','draft-label'),element('p','','draft-text'));}
  body.querySelector('.draft-text').textContent=text;scrollChat();
}
function clearDraft() { $('stream-draft')?.closest('article').remove(); }
async function stream(path,payload) {
  controller=new AbortController();
  try {
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Support-Client':'local-web'},body:JSON.stringify(payload),signal:controller.signal});
    await consumeSSE(response,(event,data)=>{
      if(event==='status')message(data.text);
      if(event==='draft')draft(data.text);
      if(event==='state'){clearDraft();render(data);}
      if(event==='reply'){clearDraft();bubble('assistant',data.text);}
    });
  } finally {clearDraft();controller=null;}
}
function renderRetrieval(traces, currentRevision) {
  const container=$('retrieval');container.replaceChildren();container.hidden=!traces || traces.length===0;
  (traces || []).forEach((trace,index)=>{
    const details=element('details','','retrieval-trace');
    details.append(element('summary',`检索 ${index+1} · ${intentNames[trace.intent.name] || trace.intent.name} · BM25 + FAISS → RRF`));
    if(currentRevision!==undefined)details.append(element('p',trace.memory_revision===currentRevision ? `基于当前记忆版本 ${currentRevision}` : `历史检索（记忆版本 ${trace.memory_revision ?? '旧版'}），仅供追溯；当前记忆已更新。`,'muted'));
    details.append(element('p','意图依据：'+trace.intent.reason),element('p','原问题与回答：'+trace.original),element('p','中文扩写：'+trace.expansion.chinese),element('p','英文扩写：'+trace.expansion.english));
    if(trace.queries) details.append(element('p',`实际使用 ${trace.queries.length} 条查询。${currentRevision===undefined ? '含独立保留的最初问题。' : '根据检索当时的有效事实构建查询，已撤回内容不作为当前检索条件。'}纯“不确定”回答保留在会话中，不新增检索条件。`,'muted'));
    details.append(element('p',`限定型号 ${trace.model_filter} · ${trace.embedding_model} · ${trace.dimensions} 维 · RRF k=${trace.rrf_k}。以下是排序分数，不是准确率。`,'muted'));
    for(const [name,hits,scoreKey] of [['BM25 关键词召回',trace.bm25,'bm25_score'],['FAISS 向量召回',trace.vector,'vector_score'],['RRF 融合结果',trace.fused,'rrf_score']]) {
      const lane=element('details','','retrieval-lane');lane.append(element('summary',`${name}（${hits.length} 项）`));
      hits.forEach((hit,rank)=>{
        const item=element('details','','retrieval-hit');const value=hit[scoreKey];
        item.append(element('summary',`${rank+1}. ${hit.chunk_id} · PDF ${hit.page} 页 · ${typeof value==='number' ? value.toFixed(5) : '—'}`));
        if(hit.chapter_path?.length)item.append(element('p','章节：'+hit.chapter_path.join(' → ')));
        if(hit.source_id)item.append(element('p','原文坐标：'+hit.source_id,'muted'));
        item.append(element('p',`BM25 名次：${hit.bm25_rank ?? '未命中'}；向量名次：${hit.vector_rank ?? '未命中'}`,'muted'));
        const link=element('a','查看说明书原页');link.href='/manual.pdf#page='+hit.page;link.target='_blank';link.rel='noopener';item.append(link,element('blockquote',hit.text));
        (hit.search_questions || []).forEach(q=>item.append(element('p','预生成问法：'+q.chinese+' / '+q.english,'generated-question')));
        lane.append(item);
      });details.append(lane);
    }
    if(trace.linked_evidence.length) details.append(element('p','关联证据补充（不计入检索排名）：'+trace.linked_evidence.join('、'),'muted'));
    container.append(details);
  });
}
function renderKnowledge(data) {
  const box=$('knowledge');box.hidden=false;box.replaceChildren();const manifest=data.manifest;
  box.append(element('p',`${manifest.document_count} 个切片 · ${manifest.search_question_count} 个检索问法 · ${manifest.followup_count} 个追问候选`));
  if(manifest.passages)box.append(element('p',`${manifest.passages.length} 个索引单元（章节段落原文、问法）；召回结果按切片去重。`,'muted'));
  box.append(element('p',`${manifest.embedding_model} / ${manifest.dimensions} 维，${manifest.generator_model} 预生成`,'muted'));
  const select=document.createElement('select');select.setAttribute('aria-label','选择知识库切片');
  data.documents.forEach(doc=>{const option=element('option',`${doc.chapter_path?.join(' → ') || doc.chunk_id} · PDF 第 ${doc.page} 页 · ${doc.chunk_id}`);option.value=doc.chunk_id;select.append(option);});
  const content=element('div','','knowledge-content');
  function show(){const doc=data.documents.find(d=>d.chunk_id===select.value);content.replaceChildren(element('blockquote',doc.text),element('h3','用户可能提问'));
    if(doc.source_id)content.prepend(element('p',`原文坐标：${doc.source_id} · 字符 ${doc.start}–${doc.end}；保留完整小节及关联注意事项。`,'muted'));
    if(!doc.search_questions.length)content.append(element('p','此切片未生成检索问法。','muted'));
    doc.search_questions.forEach(q=>content.append(element('p',q.chinese),element('p',q.english,'muted'),element('blockquote',q.quote)));
    content.append(element('h3','客服追问候选'));
    if(!doc.followups.length)content.append(element('p','此切片未生成追问。','muted'));
    doc.followups.forEach(q=>content.append(element('p',q.text),element('p',q.reason,'muted'),element('blockquote',q.quote)));
  }
  select.addEventListener('change',show);box.append(select,content);show();
}
const factLabels={problem:'当前问题',phone_connection:'手机连接相机热点',wifi_display:'SSID与密码显示',wifi_led:'Wi-Fi指示灯',app_state:'手机App状态',pc_player_state:'电脑播放器状态',screen_state:'相机屏幕',screen_saver_setting:'屏保设置',recording_state:'本次时段录像状态',recording_files:'本次时段录像文件',card_present:'存储卡是否插入',card_specs:'存储卡规格',power_source:'供电方式',power_state:'相机开关机状态',pwr_led_state:'电源指示灯',rec_led_state:'录像指示灯',led_current_state:'指示灯状态',charger_cable_connected:'车充连接位置',recording_mode:'录制模式',motion_detection_enabled:'移动侦测',boot_delay_setting:'延迟启动'};
const factStates={known:'用户已说明',unknown:'用户明确不知道',retracted:'原结论已撤回，待确认'};
factLabels.app_setup_completed='App连接步骤是否完成';
const actionStates={suggested:'尚未报告执行',completed:'用户报告已执行',unable:'无法执行',retracted:'原结果已撤回'};
function renderMemory(s) {
  const panel=$('memory-panel');panel.replaceChildren();panel.hidden=!s.memory;
  if(!s.memory)return;
  const m=s.memory;panel.append(element('h3','本次会话记忆'),element('p','以下来自你的描述与反馈，尚未询问的事项不会记成“不知道”。','muted'));
  if(m.notice)panel.append(element('p',m.notice,'memory-notice'));
  if(m.clarification)panel.append(element('p','需要澄清：'+m.clarification,'memory-clarification'));
  for(const [key,fact] of Object.entries(m.facts)){
    const row=element('div','','memory-fact');row.append(element('strong',factLabels[key] || questionNames[key] || key),element('p',`${factStates[fact.status]}：${fact.status==='retracted' ? '当前尚未确认' : fact.value}`),element('small',`来源第 ${fact.source_turn} 轮：${fact.quote}`));panel.append(row);
  }
  for(const action of Object.values(m.actions))panel.append(element('p',`${actionStates[action.status]}：${action.text}\n结果：${action.status==='retracted' ? '旧结果已撤回，当前待确认' : action.result}`,'memory-action'));
  const audit=element('details','','memory-audit');audit.append(element('summary',`查看原话与变更历史（记忆版本 ${m.revision}）`));
  for(const turn of m.turns)audit.append(element('p',`${turn.id}. ${turn.role==='user' ? '用户' : '助手'}：${turn.text}`));
  for(const event of m.events)audit.append(element('p',`事实事件 ${event.id}：${factLabels[event.key] || event.key} / ${factStates[event.status]} / ${event.value}${event.supersedes ? '；替代事件 '+event.supersedes : ''}`,'muted'));
  for(const event of m.action_events)audit.append(element('p',`操作 ${event.action_id}：${actionStates[event.status]}；${event.result}（来源第${event.source_turn}轮）`,'muted'));
  for(const old of m.advice_history)audit.append(element('p','已失效建议：'+old.reason+'\n'+old.content,'muted'));
  panel.append(audit);
}
function renderAdvice(body,s) {
  body.replaceChildren(element('p',s.advice.summary));
  s.advice.steps.forEach((step,i)=>{
    const section=element('section','','advice-step');section.append(element('h3',`检查 ${i+1}`),element('p',step.action),element('p','观察：'+step.expected,'muted'));
    const sources=element('details');sources.append(element('summary','查看说明书依据'));
    step.evidence.forEach(e=>{const link=element('a',`PDF 第 ${e.page} 页`);link.href='/manual.pdf#page='+e.page;link.target='_blank';link.rel='noopener';sources.append(link,element('blockquote',e.quote));});
    section.append(sources);
    const action=Object.values(s.memory?.actions || {}).find(a=>a.text===step.action);
    if(action && !['resolved','handoff'].includes(s.status))option('反馈这项检查',()=>setTarget(action.action_id,action.text),section);
    body.append(section);
  });
  body.append(element('p',s.advice.handoff,'muted'));
}
function setTarget(id,text){selectedAction=id;$('feedback-target').hidden=!id;$('feedback-target').querySelector('span').textContent=id?'反馈检查：'+text:'';$('chat-input').focus();}
function render(view) {
  current=view;const s=view.session;deviceConfirmed=true;
  localStorage.setItem('support-session',s.id);
  $('choose-device').textContent=s.model+' · 已确认';$('model').value=s.model;$('confirmed').checked=true;
  $('export').hidden=false;$('export').href=`/api/sessions/${s.id}/export`;
  (s.questions || []).forEach(q=>questionNames[q.key]=q.text);
  renderRetrieval(s.retrieval_history || [],s.memory?.revision);renderMemory(s);
  $('feedback-options').replaceChildren();
  const actions=Object.entries(s.memory?.actions || {});
  $('feedback-picker').hidden=!actions.length || ['resolved','handoff'].includes(s.status);
  actions.forEach(([id,action])=>option(`${actionStates[action.status]} · ${action.text}`,()=>{setTarget(id,action.text);$('feedback-picker').open=false;},$('feedback-options')));
  $('messages').replaceChildren();
  bubble('assistant',`你好，我是你的售后助手。我们一起看看 ${s.model} 遇到了什么情况。`);
  if(s.memory){
    const turns=s.memory.turns || [];
    const adviceTurn=s.advice ? [...turns].reverse().find(t=>t.role==='assistant' && t.text.startsWith(s.advice.summary+'\n')) : null;
    for(const turn of turns){
      if(turn.role==='assistant' && turn.text==='已更新本次记忆；后续建议会结合最新事实和已执行检查。')continue;
      const body=bubble(turn.role,turn.text);
      if(turn.id===adviceTurn?.id)renderAdvice(body,s);
      else if(turn.role==='assistant' && (s.memory.advice_history || []).some(a=>{try{return turn.text.startsWith(JSON.parse(a.content).summary+'\n');}catch{return false;}}))body.prepend(element('small','之前的建议，已因新信息失效','archived'));
    }
    if(s.advice && !adviceTurn)renderAdvice(bubble('assistant',''),s);
  } else {
    bubble('user',s.description);
    for(const [key,value] of Object.entries(s.answers)){bubble('assistant',questionNames[key] || key);bubble('user',value);}
    if(s.advice)renderAdvice(bubble('assistant',''),s);
    else if(view.question)bubble('assistant',view.question.text);
  }
  const q=view.question;
  if(q && !s.memory?.clarification){
    let body=[...$('messages').querySelectorAll('.assistant .bubble')].at(-1);
    if(!body || !body.textContent.includes(q.text))body=bubble('assistant',q.text);
    const choices=element('div','','choices');q.options.forEach(value=>option(value,()=>submitChat(value),choices));body.append(choices);
    const reason=element('details','','question-source');reason.append(element('summary','为什么需要确认？'),element('p',q.reason));if(q.page)reason.append(element('p',`说明书第 ${q.page} 页`));body.append(reason);
  }
  if(s.status==='ready' && !s.memory?.clarification && !busy){const body=bubble('assistant','信息已保存，可以继续生成排查建议。');option('继续回复',()=>run(()=>stream(`/api/sessions/${s.id}/advise/stream`,{version:s.version})),body);}
  if(s.status==='advised'){
    const body=bubble('assistant','你可以直接告诉我检查结果，或选择上方具体检查后反馈。问题是否已解决？');
    const choices=element('div','','choices');for(const [label,outcome] of [['已解决','resolved'],['仍未解决，转人工','handoff']])option(label,()=>run(async()=>render(await request(`/api/sessions/${s.id}/outcome`,{version:s.version,outcome}))),choices);body.append(choices);
  }
  if(s.status==='resolved')bubble('assistant','已记录问题解决。需要时可下载本次摘要，其他问题可以开始新对话。');
  if(s.status==='handoff'){
    const body=bubble('assistant','已自动整理好给人工客服的简要信息，无需重新填写。');
    body.append(element('p',view.handoff_summary || '','handoff-summary'));
    option('复制交接信息',async()=>{
      try{await navigator.clipboard.writeText(view.handoff_summary);message('交接信息已复制，可直接交给人工客服。');}
      catch{message('复制失败，请使用下载简要信息。');}
    },body);
    const link=element('a','下载简要信息','option');link.href=`/api/sessions/${s.id}/handoff`;body.append(link);
    body.append(element('p','交接信息已准备好；当前尚未接入人工客服渠道，未向任何人发送。','muted'));
  }
  setTarget(null,'');controls();scrollChat();
}
async function submitChat(value) {
  const text=(value===undefined?$('chat-input').value:value).trim();
  if(!text || busy)return;
  if(!deviceConfirmed){$('chat-input').value=text;$('device-dialog').showModal();return;}
  await run(async()=>{
    const oldVersion=current?.session.version;
    bubble('user',text);$('chat-input').value='';
    const action=selectedAction;setTarget(null,'');
    try {
      if(!current)await stream('/api/intake/stream',{model:$('model').value,confirmed:true,description:text});
      else if(current.session.workflow_version===3)await stream(`/api/sessions/${current.session.id}/message/stream`,{version:current.session.version,text,action_id:action});
      else if(current.question)await stream(`/api/sessions/${current.session.id}/answer/stream`,{version:current.session.version,key:current.question.key,answer:text});
      else throw new Error('这是旧版会话，可查看已有记录；继续排查请新建对话。');
    } catch(error) {
      clearDraft();
      // 请求可能已保存输入；重新读取版本，避免把同一条消息重复提交。
      if(current){try{render(await request('/api/sessions/'+encodeURIComponent(current.session.id)));}catch{ /* 保留本机已知记录，等待用户恢复。 */ }}
      if(current?.session.version===oldVersion)$('chat-input').value=text;
      const body=bubble('assistant','本次回复未完成。已保存的对话仍然保留。','failed');
      if(current)option('恢复会话',()=>run(async()=>render(await request('/api/sessions/'+encodeURIComponent(current.session.id)))),body);
      if(current?.session.status==='ready' && !current.session.memory?.clarification)option('重新生成回复',()=>run(()=>stream(`/api/sessions/${current.session.id}/advise/stream`,{version:current.session.version})),body);
      throw error;
    }
  });
}
function fileData(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('图片读取失败。'));reader.readAsDataURL(file);});}
$('chat-form').addEventListener('submit',event=>{event.preventDefault();void submitChat();});
$('chat-input').addEventListener('keydown',event=>{if(event.key==='Enter' && !event.shiftKey && !event.isComposing){event.preventDefault();void submitChat();}});
$('choose-device').addEventListener('click',()=>{$('device-dialog').showModal();});
$('close-device').addEventListener('click',()=>{$('device-dialog').close();});
$('device-form').addEventListener('submit',event=>{event.preventDefault();if(!$('confirmed').checked || !$('model').value)return;deviceConfirmed=true;$('choose-device').textContent=$('model').value+' · 已确认';$('device-dialog').close();if($('chat-input').value.trim())void submitChat();else $('chat-input').focus();});
$('model').addEventListener('change',()=>{deviceConfirmed=false;$('confirmed').checked=false;});
$('photo').addEventListener('change',()=>{deviceConfirmed=false;$('confirmed').checked=false;$('model').value='';$('identify-result').textContent='';});
$('identify').addEventListener('click',()=>run(async()=>{
  const file=$('photo').files[0];if(!file)throw new Error('请先选择设备图片。');if(file.size>5*1024*1024)throw new Error('图片不能超过 5MB。');
  deviceConfirmed=false;$('confirmed').checked=false;$('model').value='';$('identify-result').textContent='正在识别照片中的型号…';
  try{const result=await request('/api/identify',{image:await fileData(file)});$('identify-result').textContent=result.matched_model?`识别到 ${result.matched_model}。请核对设备并勾选确认。`:`未确认支持的型号，请补拍照片或人工核对。${result.visible_text || ''}`;if(result.matched_model)$('model').value=result.matched_model;}
  catch(error){$('identify-result').textContent=error.message;throw error;}
}));
$('clear-target').addEventListener('click',()=>setTarget(null,''));
$('load-knowledge').addEventListener('click',()=>run(async()=>renderKnowledge(await request('/api/knowledge')),'正在读取知识库…'));
$('restart').addEventListener('click',()=>{localStorage.removeItem('support-session');location.reload();});
window.addEventListener('pagehide',()=>controller?.abort());
const saved=localStorage.getItem('support-session');
if(saved)void run(async()=>render(await request('/api/sessions/'+encodeURIComponent(saved))),'正在恢复聊天记录…');
else {bubble('assistant','你好，我是说明书售后助手。请告诉我设备遇到了什么问题，我会结合说明书和你的实际情况逐步排查。');const body=bubble('assistant','当前可以协助排查 VIOFO A119 MINI 2。先确认设备型号，或直接描述现象。');option('确认我的设备',()=>{$('device-dialog').showModal();},body);}
