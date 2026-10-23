// SPEC005-TC08：安全渲染、状态流转和恢复。
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync('support_demo/web/index.html','utf8');
const script=fs.readFileSync('support_demo/web/app.js','utf8');
function setup(){const dom=new JSDOM(html,{url:'http://127.0.0.1:8766',runScripts:'outside-only'});dom.window.eval(script+'\nwindow.render=render;window.run=run;window.renderRetrieval=renderRetrieval;window.renderKnowledge=renderKnowledge;');return dom;}
test('SPEC005-TC08：确认、逐项追问、建议及结果安全渲染',()=>{
 const dom=setup(), w=dom.window, d=w.document;
 assert.match(d.body.textContent,/仅供演示使用/);
 const session={id:'example',model:'A119 MINI 2',status:'collecting',description:'<img src=x onerror=alert(1)>',answers:{},version:0};
 w.render({session,question:{key:'wifi_display',text:'Wi-Fi？',reason:'说明书',options:['已显示','未显示']}});
 assert.equal(d.querySelectorAll('#messages img').length,0);
 assert.match(d.getElementById('messages').textContent,/Wi-Fi？/);
 assert.equal(d.querySelector('.choices button').textContent,'已显示');
 session.status='ready';w.render({session,question:null});assert.match(d.getElementById('messages').textContent,/继续回复/);
 session.status='advised';session.advice={summary:'整体判断',steps:[{action:'<script>bad()</script>',expected:'观察',evidence:[{page:10,quote:'<b>quote</b>'}]}],handoff:'交接'};
 w.render({session,question:null});assert.equal(d.querySelectorAll('#messages script').length,0);assert.equal(d.querySelector('.advice-step a').getAttribute('href'),'/manual.pdf#page=10');
 assert.match(d.getElementById('messages').textContent,/已解决/);
 session.status='handoff';w.render({session,question:null});assert.equal(d.getElementById('chat-input').disabled,true);assert.match(d.getElementById('messages').textContent,/尚未/);
 assert.equal(w.localStorage.getItem('support-session'),'example');dom.window.close();
});
test('SPEC005-TC08：等待锁定和错误释放',async()=>{
 const dom=setup(),w=dom.window,d=w.document;
 let release;const pending=new Promise(resolve=>release=resolve);
 const running=w.run(async()=>{await pending;throw new Error('服务异常');},'等待');
 assert.equal(d.getElementById('send').disabled,true);assert.equal(d.getElementById('notice').textContent,'等待');
 release();await running;assert.equal(d.getElementById('send').disabled,false);assert.equal(d.getElementById('notice').textContent,'服务异常');dom.window.close();
});
test('SPEC007-TC08：两路召回、融合、扩写与预生成问题真实字段安全展示',()=>{
 const dom=setup(),w=dom.window,d=w.document;
 const question={chinese:'手机连接不上？',english:'Cannot connect?',quote:'Manual quote'};
 const hit={chunk_id:'p10-2-s04',source_id:'p10-2',chapter_path:['Review and Control on Smartphone','Connect VIOFO APP'],page:10,text:'<img src=x onerror=bad()>',bm25_rank:1,bm25_score:7.1,vector_rank:2,vector_score:0.8,rrf_score:1/61+1/62,search_questions:[question]};
 w.renderRetrieval([{intent:{name:'wifi',reason:'连接'},original:'用户原话',expansion:{chinese:'扩写中文',english:'expanded query'},model_filter:'A119 MINI 2',embedding_model:'test-vector',dimensions:3,rrf_k:60,bm25:[hit],vector:[hit],fused:[hit],linked_evidence:['p5-2']}]);
 const text=d.getElementById('retrieval').textContent;
 for(const expected of ['BM25 关键词召回','FAISS 向量召回','RRF 融合结果','用户原话','expanded query','7.10000','0.80000','p5-2'])assert.ok(text.includes(expected),expected);
 assert.equal(d.querySelectorAll('#retrieval img').length,0);
 assert.ok(text.includes('Connect VIOFO APP') && text.includes('原文坐标：p10-2'),'SPEC008-TC06 章节及来源');
 w.renderKnowledge({manifest:{document_count:1,search_question_count:1,followup_count:1,embedding_model:'test-vector',dimensions:3,generator_model:'test-generator'},documents:[{chunk_id:'p10-2',page:10,text:'manual',search_questions:[question],followups:[{text:'手机连接了吗？',reason:'确认阶段',quote:'source'}]}]});
 assert.match(d.getElementById('knowledge').textContent,/用户可能提问/);assert.match(d.getElementById('knowledge').textContent,/客服追问候选/);
 dom.window.close();
});

test('SPEC009-TC10：有效记忆、撤回历史、操作状态与澄清安全展示',()=>{
 const dom=setup(),w=dom.window,d=w.document;
 const memory={revision:2,notice:'记忆已更新',clarification:'请说明哪项操作？',facts:{recording_files:{status:'retracted',value:'原来有录像',quote:'看错了，是昨天的视频',source_turn:3}},actions:{recording_files:{text:'查看已有录像',status:'completed',result:'<img src=x onerror=bad()>'}},turns:[{id:1,role:'user',text:'<script>bad()</script>'}],events:[{id:2,key:'recording_files',status:'retracted',value:'当前未知',supersedes:1}],action_events:[{action_id:'recording_files',status:'completed',result:'已检查',source_turn:2}],advice_history:[{reason:'用户纠正信息',content:'旧建议'}]};
 const s={id:'memory-test',model:'A119 MINI 2',status:'ready',description:'旧描述',answers:{},version:1,workflow_version:3,memory};
 w.render({session:s,question:null});
 assert.equal(d.getElementById('memory-panel').hidden,false);
 assert.equal(d.getElementById('chat-input').disabled,false);
 assert.doesNotMatch(d.getElementById('messages').textContent,/继续回复/);
 const text=d.getElementById('memory-panel').textContent;
 for(const expected of ['原结论已撤回','来源第 3 轮','用户报告已执行','需要澄清','替代事件 1','已失效建议'])assert.ok(text.includes(expected),expected);
 assert.equal(d.querySelectorAll('#memory-panel img,#memory-panel script').length,0);
 assert.equal(d.querySelector('#feedback-options button').textContent,'用户报告已执行 · 查看已有录像');
 memory.clarification='';w.render({session:s,question:null});assert.match(d.getElementById('messages').textContent,/继续回复/);
 s.status='handoff';w.render({session:s,question:null});assert.equal(d.getElementById('chat-input').disabled,true);
 s.memory=null;s.workflow_version=2;w.render({session:s,question:null});assert.equal(d.getElementById('memory-panel').hidden,true);
 w.renderRetrieval([{intent:{name:'wifi',reason:'连接'},memory_revision:1,original:'当时事实',expansion:{chinese:'查询',english:'query'},queries:['查询'],model_filter:'A119 MINI 2',embedding_model:'test-vector',dimensions:3,rrf_k:60,bm25:[],vector:[],fused:[],linked_evidence:[]}],2);
 assert.match(d.getElementById('retrieval').textContent,/历史检索/);
 assert.match(d.getElementById('retrieval').textContent,/已撤回内容不作为当前检索条件/);
 assert.doesNotMatch(d.getElementById('retrieval').textContent,/含独立保留的最初问题/);
 dom.window.close();
});
