// SPEC-011：聊天交互与真实分块读取，不使用打字定时器。
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
function setup(){
  const dom=new JSDOM(fs.readFileSync('support_demo/web/index.html','utf8'),{url:'http://127.0.0.1:8766',runScripts:'outside-only'});
  Object.assign(dom.window,{TextDecoder,AbortController});
  dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;};
  dom.window.eval(fs.readFileSync('support_demo/web/app.js','utf8')+'\nwindow.testAPI={consumeSSE,stream,render,submitChat,run};');
  return dom;
}
const frame=(event,data)=>`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const state=(status='collecting')=>({session:{id:'s',version:0,status,model:'A119 MINI 2',workflow_version:3,description:'无法连接',answers:{},memory:{revision:1,turns:[{id:1,role:'user',text:'无法连接'},{id:2,role:'assistant',text:'连接到热点了吗？'}],facts:{},actions:{},events:[],action_events:[],advice_history:[]}},question:status==='collecting'?{key:'phone_connection',text:'连接到热点了吗？',reason:'核对连接阶段',options:['已连接','没有']} : null});

test('SPEC011 流式中文跨字节、跨帧到达时立即显示草稿，结束前不等待完整回复',async()=>{
  const dom=setup(),w=dom.window,d=w.document;let push;
  const body=new ReadableStream({start(c){push=c;}});
  w.fetch=async()=>({ok:true,body});
  const pending=w.testAPI.stream('/test',{});
  const bytes=new TextEncoder().encode(frame('draft',{text:'正在核对中文<script>无脚本</script>'}));
  for(const byte of bytes)push.enqueue(new Uint8Array([byte]));
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(d.querySelector('.draft-text').textContent,/正在核对中文/);
  assert.equal(d.querySelectorAll('#messages script').length,0);
  push.enqueue(new TextEncoder().encode(frame('done',{})));await pending;
  assert.equal(d.querySelector('#stream-draft'),null);dom.window.close();
});

test('SPEC011 断流或错误撤去草稿，不冒充完成',async()=>{
  for(const suffix of ['',frame('error',{text:'模型暂不可用'})]){
    const dom=setup(),w=dom.window;
    w.fetch=async()=>({ok:true,body:new ReadableStream({start(c){c.enqueue(new TextEncoder().encode(frame('draft',{text:'未确认草稿'})+suffix));c.close();}})});
    await assert.rejects(w.testAPI.stream('/test',{}),/中断|不可用/);
    assert.equal(w.document.querySelector('#stream-draft'),null);dom.window.close();
  }
});

test('SPEC011 型号在弹层确认，追问选项直接作为聊天消息发送',async()=>{
  const dom=setup(),w=dom.window,d=w.document;
  d.getElementById('chat-input').value='App没画面';await w.testAPI.submitChat();
  assert.equal(d.getElementById('device-dialog').open,true);
  assert.equal(d.getElementById('chat-input').value,'App没画面');
  w.testAPI.render(state());let sent;
  w.fetch=async(path,options)=>{sent={path,payload:JSON.parse(options.body)};return {ok:true,body:new ReadableStream({start(c){c.enqueue(new TextEncoder().encode(frame('state',state())+frame('done',{})));c.close();}})};};
  [...d.querySelectorAll('.choices button')].find(b=>b.textContent==='没有').click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(sent.payload.text,'没有');assert.match(sent.path,/message\/stream$/);assert.equal(d.getElementById('send').disabled,false);
  assert.equal(d.getElementById('start-form'),null);assert.equal(d.getElementById('answer-form'),null);dom.window.close();
});

test('SPEC011 已保存状态在后续模型失败时保留，能够继续生成回复',async()=>{
  const dom=setup(),w=dom.window,d=w.document;
  w.testAPI.render(state());const ready=state('ready');ready.session.version=1;
  w.fetch=async(path,options)=>options?{ok:true,body:new ReadableStream({start(c){c.enqueue(new TextEncoder().encode(frame('state',ready)+frame('draft',{text:'草稿'})+frame('error',{text:'服务异常'})));c.close();}})}:{ok:true,json:async()=>ready};
  await w.testAPI.submitChat('已连接');
  assert.match(d.getElementById('notice').textContent,/服务异常/);
  assert.match(d.getElementById('messages').textContent,/重新生成回复/);
  assert.equal(d.querySelector('#stream-draft'),null);assert.equal(d.getElementById('chat-input').value,'');
  assert.equal(w.localStorage.getItem('support-session'),'s');dom.window.close();
});
