const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { productURL, allowedNavigation } = require('../desktop/policy.cjs');
const { inspectPage } = require('../desktop/collector.cjs');
const { Workflow } = require('../desktop/workflow.cjs');
const fs = require('node:fs');
const path = require('node:path');

test('SPEC004-TC02 行动建议按顺序显示且不执行HTML，历史报告保留', () => {
  const dom = new JSDOM(fs.readFileSync(path.join(__dirname,'../desktop/ui/index.html'),'utf8'), {runScripts:'outside-only'});
  let receive;
  dom.window.desktop = {onState: fn => { receive=fn; }};
  dom.window.eval(fs.readFileSync(path.join(__dirname,'../desktop/ui/app.js'),'utf8'));
  const batch = {id:'batch',status:'completed',snapshot:{title:'测试',site:'amazon.de',asin:'B0C5J4FXXH',captured_at:'2026-10-22'},validation:{valid:[],duplicates:0,excluded:[]},report:{scope:'样本',summary:'综合判断',topics:[],priorities:[{title:'先验证',reason:'影响较大',next_step:'<img src=x onerror=alert(1)>',owner:'产品',timing:'第一阶段',success_criteria:'形成对照结果',topic_names:['连接']}]}};
  receive({status:'completed',batch});
  const report=dom.window.document.querySelector('#report');
  assert.ok(report.textContent.includes('优先 1：先验证'));
  assert.ok(report.textContent.includes('形成对照结果'));
  assert.ok(report.textContent.includes('依据主题（见下方原文证据）：连接'));
  assert.equal(report.querySelector('img'),null);
  delete batch.report.priorities;
  receive({status:'completed',batch});
  assert.ok(report.textContent.includes('历史报告尚未生成行动建议'));
});
test('SPEC002-TC01/02 去除演示说明，保留演示提示与业务入口', () => {
  const html=fs.readFileSync(path.join(__dirname,'../desktop/ui/index.html'),'utf8');
  const document=new JSDOM(html).window.document;
  assert.equal(document.querySelector('#guide,[data-tab="guide"]'),null);
  assert.equal(document.querySelector('.demo-notice').textContent,'仅供演示使用');
  for(const id of ['overview','reviews','history','collect','pause','resume'])assert.ok(document.getElementById(id));
});
test('TC01 地址白名单与商品身份', () => {
  assert.equal(productURL('https://www.amazon.de/dp/B0C5J4FXXH').site,'amazon.de');
  for (const url of ['file:///C:/secret','https://amazon.com.evil.com/dp/B0C5J4FXXH','https://www.amazon.co.uk/dp/B0C5J4FXXH','https://www.amazon.de/s?k=test']) assert.throws(()=>productURL(url));
  assert.equal(allowedNavigation('https://www.amazon.com/ap/signin'),true);
});
function page(html) {
  const dom = new JSDOM(`<input id="ASIN" value="B0C5J4FXXH"><h1 id="productTitle">测试商品</h1><div id="reviewsMedley">${html}</div>`, { url:'https://www.amazon.de/dp/B0C5J4FXXH',runScripts:'outside-only' });
  return dom;
}
test('TC04/07 新结构评论正文，不混入AI摘要；站点和地区分开', () => {
  const dom = page('<p data-hook="cr-insights-widget-summary">AI summary</p><div data-hook="review" id="R1"><h5 data-hook="reviewTitle">Good</h5><i data-hook="review-star-rating"><span class="a-icon-alt">4,0 von 5 Sternen</span></i><div data-hook="reviewRichContentContainer">Good camera</div><span data-hook="review-date">Rezension aus den Vereinigten Staaten vom 1. Januar 2026</span></div>');
  const result = dom.window.eval(`(${inspectPage.toString()})()`);
  assert.equal(result.reviews.length,1); assert.equal(result.reviews[0].body,'Good camera');
  assert.equal(result.reviews[0].rating,4); assert.equal(result.reviews[0].verified,null); assert.equal(result.reviews[0].country,'den Vereinigten Staaten');
});
test('TC04/05 经典正文展开与展开失败状态', () => {
  const dom = page('<div data-hook="review" id="R2"><span data-hook="review-body">完整正文</span><a class="a-expander-header" aria-expanded="false">more</a></div>');
  assert.equal(dom.window.eval(`(${inspectPage.toString()})()`).complete,false);
  dom.window.document.querySelector('a').onclick = event => event.target.setAttribute('aria-expanded','true');
  dom.window.eval(`(${inspectPage.toString()})('expand')`);
  assert.equal(dom.window.eval(`(${inspectPage.toString()})()`).complete,true);
});
test('TC06 识别明确零评论和未知结构', () => {
  const dom = page('<div data-hook="no-reviews-message">No reviews</div>');
  assert.equal(dom.window.eval(`(${inspectPage.toString()})()`).complete,true);
  dom.window.document.querySelector('#reviewsMedley').remove();
  assert.equal(dom.window.eval(`(${inspectPage.toString()})()`).recognized,false);
});
test('TC07 真实页面的德文与中文日期地区格式', () => {
  for (const [date,region] of [['Bewertet in Deutschland am 27. Januar 2026','Deutschland'],['2025年8月20日在美国发布评论','美国']]) {
    const dom=page(`<div data-hook="review" id="R1"><span data-hook="review-date">${date}</span></div>`);
    assert.equal(dom.window.eval(`(${inspectPage.toString()})()`).reviews[0].country,region);
  }
});
test('TC04 手动翻译不能冒充原文', () => {
  const dom=page('<div data-hook="review" id="R1"><span data-hook="review-body">译文</span><a data-hook="seeOriginalReviewLink">查看原文</a></div>');
  assert.equal(dom.window.eval(`(${inspectPage.toString()})()`).complete,false);
});
test('TC02 隐藏的Cookie容器不应阻断正常采集', () => {
  const dom=page('<div style="display:none"><div id="sp-cc"><button id="sp-cc-accept">Accept</button></div></div>');
  assert.equal(dom.window.eval(`(${inspectPage.toString()})()`).wait,undefined);
  dom.window.document.querySelector('#sp-cc').parentElement.style.display='block';
  assert.match(dom.window.eval(`(${inspectPage.toString()})()`).wait,/Cookie/);
});
const base = () => ({site:'amazon.com',asin:'B0CC582PX1',url:'https://www.amazon.com/dp/B0CC582PX1',title:'Camera',variant:null,recognized:true,complete:true,reviews:[{review_id:'R1',body:'Good',rating:5,expanded:true}]});
test('TC12/13 自动采集分析固定快照，无审核关卡', async () => {
  const calls=[]; const states=[];
  const flow = new Workflow({inspect:async()=>base(),sleep:async()=>{},emit:s=>states.push(s.status),api:async(route,method,payload)=>{calls.push({route,payload});return route==='/batches'?{id:'1',status:'validating'}:{id:'1',status:'completed'};}});
  await flow.start(base().url);
  assert.equal(calls.length,2);assert.equal(calls[1].route,'/batches/1/analyze');assert.equal(states.at(-1),'completed');
});
test('TC02/18 登录暂停继续与并发拒绝', async () => {
  let login=true; let waiting;
  const signal=new Promise(resolve=>waiting=resolve);
  const flow=new Workflow({inspect:async()=>login?{wait:'登录'}:base(),sleep:async()=>{},emit:s=>{if(s.status==='waiting_for_user')waiting();},api:async()=>({id:'1',status:'no_valid_reviews'})});
  const running=flow.start(base().url); await signal;
  await assert.rejects(()=>flow.start(base().url),/task_running/);
  login=false;flow.resume();await running;assert.equal(flow.busy,false);
});
test('TC03 来源变更停止且不分析', async () => {
  let count=0;const calls=[];
  const flow=new Workflow({inspect:async()=>({...base(),variant:count++>1?'changed':null}),sleep:async()=>{},emit:()=>{},api:async(route,method,payload)=>{calls.push(payload);return {status:'collection_failed'};}});
  await flow.start(base().url);assert.equal(calls.length,1);assert.equal(calls[0].complete,false);assert.equal(calls[0].error,'product_changed_restart_required');
});
test('TC05 无法定位时停止，不发布成功结果', async () => {
  let saved;
  const flow=new Workflow({inspect:async()=>({...base(),recognized:false,complete:false,reviews:[]}),sleep:async()=>{},emit:()=>{},api:async(route,method,payload)=>{saved=payload;return {status:'collection_failed'};}});
  await flow.start(base().url);assert.equal(saved.error,'review_section_unrecognized');
});
