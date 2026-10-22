/* SPEC-005：界面使用 textContent 渲染用户和模型文本，禁止注入 HTML。 */
'use strict';
const $ = id => document.getElementById(id);
let current = null;
let busy = false;
const questionNames = {};
const intentNames = {wifi:'Wi-Fi / App',recording:'存储卡 / 录像',power:'供电 / 启动',other_support:'其他产品问题',unrelated:'无关话题',unclear:'信息不足'};
const stateLabels = {collecting:'收集必要信息',ready:'准备生成建议',advised:'按顺序完成检查',resolved:'本次问题已解决',handoff:'已整理人工交接摘要'};

function message(text) { $('notice').textContent = text; }
async function request(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Support-Client':'local-web'},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '输入信息不完整或格式不正确，请检查后重试。');
  return result;
}
async function run(operation, pending) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(button => button.disabled = true);
  message(pending || '正在保存…');
  try { await operation(); message(''); }
  catch (error) { message(error.message || '请求失败，请重试。'); }
  finally { busy = false; document.querySelectorAll('button').forEach(button => button.disabled = false); }
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
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
  const panel=$('memory-panel'),form=$('memory-form');panel.replaceChildren();panel.hidden=!s.memory;form.hidden=!s.memory || ['resolved','handoff'].includes(s.status);
  if(!s.memory)return;
  const m=s.memory;panel.append(element('h3','本次会话记忆'),element('p','以下来自你的描述与反馈，尚未询问的事项不会记成“不知道”。','muted'));
  if(m.notice)panel.append(element('p',m.notice,'memory-notice'));
  if(m.clarification)panel.append(element('p','需要澄清：'+m.clarification,'memory-clarification'));
  for(const [key,fact] of Object.entries(m.facts)){
    const row=element('div','','memory-fact');row.append(element('strong',factLabels[key] || questionNames[key] || key),element('p',`${factStates[fact.status]}：${fact.status==='retracted' ? '当前尚未确认' : fact.value}`),element('small',`来源第 ${fact.source_turn} 轮：${fact.quote}`));panel.append(row);
  }
  const previous=$('action-target').value;$('action-target').replaceChildren(element('option','补充或纠正本次信息'));$('action-target').firstChild.value='';
  for(const [key,action] of Object.entries(m.actions)){
    panel.append(element('p',`${actionStates[action.status]}：${action.text}\n结果：${action.status==='retracted' ? '旧结果已撤回，当前待确认' : action.result}`,'memory-action'));
    const option=element('option',`${actionStates[action.status]} · ${action.text}`);option.value=key;$('action-target').append(option);
  }
  if(m.actions[previous])$('action-target').value=previous;
  $('memory-message').value='';
  const audit=element('details','','memory-audit');audit.append(element('summary',`查看原话与变更历史（记忆版本 ${m.revision}）`));
  for(const turn of m.turns)audit.append(element('p',`${turn.id}. ${turn.role==='user' ? '用户' : '助手'}：${turn.text}`));
  for(const event of m.events)audit.append(element('p',`事实事件 ${event.id}：${factLabels[event.key] || event.key} / ${factStates[event.status]} / ${event.value}${event.supersedes ? '；替代事件 '+event.supersedes : ''}`,'muted'));
  for(const event of m.action_events)audit.append(element('p',`操作 ${event.action_id}：${actionStates[event.status]}；${event.result}（来源第${event.source_turn}轮）`,'muted'));
  for(const old of m.advice_history)audit.append(element('p','已失效建议：'+old.reason+'\n'+old.content,'muted'));
  panel.append(audit);
}
function render(view) {
  current = view;
  const s = view.session;
  $('intent-reply').hidden=true;
  renderRetrieval(s.retrieval_history || [],s.memory?.revision);
  (s.questions || []).forEach(question=>questionNames[question.key]=question.text);
  renderMemory(s);
  localStorage.setItem('support-session', s.id);
  $('setup').hidden = true; $('conversation').hidden = false;
  $('stage-title').textContent = stateLabels[s.status];
  $('stage-label').textContent = `${s.model} / ${s.id.slice(0,8)}`;
  for (const part of ['model','question','advice','result']) $('progress-'+part).classList.remove('active');
  const part = s.status === 'collecting' ? 'question' : (s.status === 'resolved' || s.status === 'handoff') ? 'result' : 'advice';
  $('progress-'+part).classList.add('active');
  $('history').replaceChildren(element('div',(s.memory ? '最初描述（原话保留，当前以本次记忆为准）：' : '问题描述：')+s.description,'answer-row'));
  for (const [key,value] of Object.entries(s.answers)) {
    const row = element('div',value,'answer-row');
    row.prepend(element('small',questionNames[key] || ({wifi_display:'相机 Wi-Fi 状态',phone_connection:'手机连接状态',app_state:'App 进度',card:'存储卡规格',symptom:'录像现象',power_source:'供电方式',power_state:'相机状态'}[key])));
    $('history').append(row);
  }
  $('answer-form').hidden = !view.question;
  if (view.question) {
    const q = view.question; questionNames[q.key] = q.text;
    $('question-text').textContent = q.text; $('question-reason').textContent = q.reason;
    if(q.page) $('question-reason').append(element('span',` 依据：${q.chunk_id}，PDF 第 ${q.page} 页。`));
    $('answer').value = ''; $('options').replaceChildren();
    q.options.forEach(value => {const button = element('button',value);button.type='button';button.addEventListener('click',()=>{$('answer').value=value;$('answer').focus();});$('options').append(button);});
  }
  $('ready').hidden = s.status !== 'ready' || Boolean(s.memory?.clarification);
  $('advice').replaceChildren();
  if (s.advice) {
    $('advice').append(element('div',s.advice.summary,'summary'));
    s.advice.steps.forEach((step,i) => {
      const section = element('section','','step');
      section.append(element('h3',`步骤 ${i+1}`),element('p',step.action),element('p','观察结果：'+step.expected));
      step.evidence.forEach(evidence => {
        const link = element('a',`查看 PDF 第 ${evidence.page} 页`); link.href='/manual.pdf#page='+evidence.page;link.target='_blank';link.rel='noopener';
        section.append(link,element('blockquote',evidence.quote));
      });
      $('advice').append(section);
    });
    $('advice').append(element('h3','无法解决时'),element('p',s.advice.handoff));
  }
  $('outcomes').hidden = s.status !== 'advised';
  $('closed').hidden = !['resolved','handoff'].includes(s.status);
  $('closed').textContent = s.status === 'resolved' ? '已记录为解决。你可以下载本次问题、检查建议与引用记录。' : '请下载摘要交给人工客服继续处理。本工具尚未向任何人发送消息。';
  $('export').hidden = false; $('export').href = `/api/sessions/${s.id}/export`;
}
function fileData(file) { return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('图片读取失败。'));reader.readAsDataURL(file);}); }
$('identify').addEventListener('click',()=>run(async()=>{
  const file=$('photo').files[0];
  if (!file) throw new Error('请先选择设备图片。');
  if (file.size > 5*1024*1024) throw new Error('图片不能超过 5MB。');
  $('confirmed').checked=false; $('model').value='';
  const result=await request('/api/identify',{image:await fileData(file)});
  $('identify-result').textContent=result.matched_model ? `候选型号：${result.matched_model}。请与设备核对后勾选确认。可见文字：${result.visible_text}` : `未确认支持的型号。请补拍清晰铭牌，或核对后人工选择。识别文字：${result.visible_text || '未读到型号文字'}`;
  if (result.matched_model) $('model').value=result.matched_model;
},'正在识别型号，最长约 75 秒…'));
$('photo').addEventListener('change',()=>{$('confirmed').checked=false;$('model').value='';$('identify-result').textContent='';});
$('model').addEventListener('change',()=>{$('confirmed').checked=false;});
$('start-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{
  const result=await request('/api/intake',{model:$('model').value,confirmed:$('confirmed').checked,description:$('description').value});
  if(result.view)render(result.view);
  else {$('intent-reply').hidden=false;$('intent-reply').replaceChildren(element('strong','意图识别：'+intentNames[result.intent.name]),element('p',result.reply),element('small','程序固定回复；未执行知识检索或生成售后答案。'));}
},'正在识别意图、扩写查询并执行混合检索…');});
$('load-knowledge').addEventListener('click',()=>run(async()=>renderKnowledge(await request('/api/knowledge')),'正在读取知识库…'));
$('answer-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>render(await request(`/api/sessions/${current.session.id}/answer`,{version:current.session.version,key:current.question.key,answer:$('answer').value})));});
$('memory-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>render(await request(`/api/sessions/${current.session.id}/message`,{version:current.session.version,text:$('memory-message').value,action_id:$('action-target').value || null})),'正在结合本次对话核对记忆更新…');});
$('advise').addEventListener('click',()=>run(async()=>render(await request(`/api/sessions/${current.session.id}/advise`,{version:current.session.version})),'正在结合回答重新扩写、混合检索并生成建议…'));
for (const outcome of ['resolved','handoff']) $(outcome).addEventListener('click',()=>run(async()=>render(await request(`/api/sessions/${current.session.id}/outcome`,{version:current.session.version,outcome}))));
$('restart').addEventListener('click',()=>{localStorage.removeItem('support-session');location.reload();});
const saved=localStorage.getItem('support-session');
if(saved) run(async()=>render(await request('/api/sessions/'+encodeURIComponent(saved))),'正在恢复会话…');
else $('progress-model').classList.add('active');
