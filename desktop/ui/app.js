const $ = id => document.getElementById(id);
let current = null, wide = false;
const labels = { idle:'就绪',preparing:'准备',collecting:'采集',validating:'校验',analyzing:'分析',completed:'完成',waiting_for_user:'等待人工',collection_failed:'采集失败',analysis_failed:'分析失败',no_valid_reviews:'无有效评论' };
const errors = { collection_incomplete:'评论尚未完整加载或展开，请检查右侧页面后重新采集', review_section_unrecognized:'无法识别评论区域，请检查页面是否正常加载', product_changed_restart_required:'商品或变体已改变，请重新开始采集', product_identity_mismatch:'页面商品与地址不一致，请重新打开商品', OpenAITimeoutError:'模型请求超时，原始评论已保留，可重试分析', TimeoutError:'分析超过时间限制，原始评论已保留，可重试分析', evidence_mismatch:'模型引用未通过原文校验，请重新分析', no_valid_reviews:'本次没有有效评论，不调用模型', sample_limit_exceeded:'样本超过首版数量或字符限制', interrupted:'上次分析被中断，可使用原样本重试' };
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
errors.action_topic_mismatch = '行动建议引用了不存在的主题，请重新分析';
function status(state) {
  $('status').querySelector('span').textContent = errors[state.message] || state.message || labels[state.status];
  $('status').className = state.status?.includes('failed') ? 'error' : state.status === 'waiting_for_user' ? 'waiting' : '';
  document.querySelectorAll('[data-step]').forEach(node => node.classList.toggle('active', node.dataset.step === state.status));
  const busy = ['preparing','collecting','validating','analyzing','waiting_for_user'].includes(state.status);
  $('collect').disabled = busy; $('open').disabled = busy; $('clear').disabled = busy;
  $('pause').disabled = !['collecting','preparing'].includes(state.status); $('resume').disabled = state.status !== 'waiting_for_user';
  if (state.batch) renderBatch(state.batch);
}
async function action(task) { try { return await task(); } catch (error) { $('status').querySelector('span').textContent = error.message; $('status').className = 'error'; } }
function switchTab(name) { document.querySelectorAll('.panel').forEach(node => node.classList.toggle('hidden', node.id !== name)); document.querySelectorAll('[data-tab]').forEach(node => node.classList.toggle('active', node.dataset.tab === name)); }
function button(text, callback) { const node = element('button', text); node.addEventListener('click', () => action(callback)); return node; }
function renderBatch(batch) {
  current = batch; $('empty').classList.add('hidden'); const report = $('report'); report.replaceChildren();
  report.append(element('h2', batch.snapshot.title || batch.snapshot.asin));
  report.append(element('div', `${batch.snapshot.site} · ${batch.snapshot.asin} · ${batch.snapshot.captured_at} · ${labels[batch.status] || batch.status}`, 'meta'));
  const metrics = element('div', undefined, 'metrics');
  for (const [name,count] of [['有效评论',batch.validation.valid.length],['重复记录',batch.validation.duplicates],['排除记录',batch.validation.excluded.length]]) { const metric = element('div', undefined, 'metric'); metric.append(element('b',count),element('span',name)); metrics.append(metric); }
  report.append(metrics);
  const exports = element('div', undefined, 'row'); exports.append(button('导出评论 CSV', () => window.desktop.export(batch.id,'csv')));
  if (batch.report) exports.append(button('导出报告 HTML', () => window.desktop.export(batch.id,'html')));
  if (batch.status === 'analysis_failed') exports.append(button('重试原样本分析', () => window.desktop.retry(batch.id)));
  report.append(exports);
  if (batch.error) report.append(element('p', `任务信息：${errors[batch.error] || batch.error}`, 'meta'));
  if (batch.report) {
    report.append(element('p', batch.report.scope, 'meta'), element('h2', '整体结论'), element('p', batch.report.summary));
    report.append(element('h2', '优先建议与后续行动'), element('p', '以下为建议执行顺序；负责角色、阶段和验收标准均为建议。', 'meta'));
    if (!batch.report.priorities) report.append(element('p', '历史报告尚未生成行动建议。'));
    else batch.report.priorities.forEach((item, index) => {
      const card = element('article', undefined, 'card');
      card.append(element('h3', `优先 ${index + 1}：${item.title}`));
      for (const [label,key] of [['排序理由','reason'],['下一步','next_step'],['建议负责','owner'],['建议阶段','timing'],['验收标准','success_criteria']]) card.append(element('p', `${label}：${item[key]}`));
      card.append(element('p', `依据主题（见下方原文证据）：${item.topic_names.join('、')}`, 'meta'));
      report.append(card);
    });
    report.append(element('h2', '主题与原文证据'));
    for (const topic of batch.report.topics) {
      const card = element('article', undefined, 'card');
      card.append(element('h3', `${topic.name} · ${topic.count} 条 / ${topic.percent}%`));
      card.append(element('span', ({positive:'好评',negative:'问题',mixed:'混合评价',neutral:'中性'})[topic.sentiment], 'tag'));
      card.append(element('p', `观察：${topic.observation}`), element('p', `建议：${topic.suggestion}`));
      if (topic.hypothesis) card.append(element('p', `待核实原因：${topic.hypothesis}`));
      const details = element('details'); details.append(element('summary', `查看 ${topic.evidence.length} 条原文证据`));
      for (const evidence of topic.evidence) details.append(element('blockquote', `${evidence.review_id}\n${evidence.quote}`));
      card.append(details); report.append(card);
    }
  }
  const list = $('review-list'); list.replaceChildren(); list.className='';
  const rows = [...batch.validation.valid.map(review => ({review,reason:'有效'})), ...batch.validation.excluded];
  for (const {review,reason} of rows) {
    const card = element('article', undefined, 'card'); card.append(element('h3',review.title || '无标题'));
    card.append(element('div', `${review.rating ?? '未知'} / 5 · ${review.review_id || '缺少评论ID'} · ${reason}`, 'meta'));
    card.append(element('div', `${review.date_text || '日期未知'} · 地区：${review.country || '未知'} · 评论变体：${review.variant || '未知'} · 购买认证：${review.verified === true ? '已标注' : '未显示'}`, 'meta'));
    card.append(element('p',review.body, 'raw-body'));
    card.append(element('div',review.url || batch.snapshot.url,'meta')); list.append(card);
  }
}
window.desktop.onState(status);
$('open').onclick = () => action(() => window.desktop.open($('url').value.trim()));
$('collect').onclick = () => action(() => window.desktop.collect());
$('pause').onclick = () => action(() => window.desktop.pause()); $('resume').onclick = () => action(() => window.desktop.resume());
$('clear').onclick = () => action(() => window.desktop.clear());
$('toggle').onclick = () => action(async () => { wide=!wide; document.body.classList.toggle('wide',wide); $('toggle').textContent=wide?'显示商品浏览器':'展开工作台'; await window.desktop.showBrowser(!wide); });
document.querySelectorAll('[data-tab]').forEach(node => node.onclick = () => switchTab(node.dataset.tab));
$('sample-us').onclick = () => { $('url').value='https://www.amazon.com/dp/B0CC582PX1'; };
$('sample-de').onclick = () => { $('url').value='https://www.amazon.de/dp/B0C5J4FXXH'; };
$('refresh').onclick = () => action(async () => { const list=$('history-list'); list.replaceChildren(); for (const batch of await window.desktop.history()) { const row=button(`${labels[batch.status] || batch.status} · ${batch.snapshot.site}\n${batch.snapshot.title}\n${batch.created_at}`,async () => {renderBatch(batch);switchTab('overview');});row.className='history-row';list.append(row); } });
