// SPEC-012：自动展示和复制交接信息，不要求用户再次输入。
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
test('转人工自动显示简报，安全渲染并复制同一内容',async()=>{
 const dom=new JSDOM(fs.readFileSync('support_demo/web/index.html','utf8'),{url:'http://127.0.0.1:8766',runScripts:'outside-only'});
 const w=dom.window,d=w.document;let copied;
 Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async text=>{copied=text;}}});
 w.eval(fs.readFileSync('support_demo/web/app.js','utf8')+'\nwindow.render=render;');
 const text='人工客服交接简报\n设备：A119 MINI 2\n问题：<img src=x>无画面';
 w.render({session:{id:'test',model:'A119 MINI 2',status:'handoff',description:'无画面',answers:{}},question:null,handoff_summary:text});
 assert.equal(d.querySelector('.handoff-summary').textContent,text);
 assert.equal(d.querySelectorAll('#messages img').length,0);
 assert.equal(d.getElementById('chat-input').disabled,true);
 const copy=[...d.querySelectorAll('button')].find(x=>x.textContent==='复制交接信息');copy.click();
 await new Promise(resolve=>setImmediate(resolve));assert.equal(copied,text);
 assert.equal(d.querySelector('a[href="/api/sessions/test/handoff"]').textContent,'下载简要信息');
 assert.match(d.getElementById('messages').textContent,/尚未接入人工客服渠道/);
 dom.window.close();
});
