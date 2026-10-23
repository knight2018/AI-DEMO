const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { inspectPage } = require('../desktop/collector.cjs');
const { Workflow } = require('../desktop/workflow.cjs');

const review = id => `<div data-hook="review" id="${id}"><span data-hook="reviewRichContentContainer">正文 ${id}</span></div>`;
function page(html) {
  const dom = new JSDOM(`<input id="ASIN" value="B0CC582PX1"><h1 id="productTitle">商品</h1>${html}`, {
    url: 'https://www.amazon.com/dp/B0CC582PX1', runScripts: 'outside-only',
  });
  const targets = [];
  dom.window.HTMLElement.prototype.scrollIntoView = function () { targets.push(this); };
  dom.window.scrollBy = () => assert.fail('禁止按固定距离滚动');
  return { document: dom.window.document, targets, read: (action = 'read') => dom.window.eval(`(${inspectPage.toString()})(${JSON.stringify(action)})`) };
}

test('SPEC010-TC01 新布局与独立评论条目均可识别，摘要不是评论', () => {
  for (const html of [`<div id="customer-reviews_feature_div">${review('R1')}</div>`, review('R1')]) {
    const result = page(html).read();
    assert.equal(result.recognized, true);
    assert.equal(result.complete, true);
    assert.equal(result.reviews.length, 1);
  }
  const summary = page('<p data-hook="cr-insights-widget-summary">摘要</p>').read();
  assert.equal(summary.complete, false);
  assert.equal(summary.reviews.length, 0);
});

test('SPEC010-TC02/04 直接定位远处末条正文，新增条目后定位新末条', () => {
  const p = page(`<div id="customer-reviews_feature_div">${review('R1')}</div>`);
  const body = p.document.querySelector('[data-hook="reviewRichContentContainer"]');
  body.getBoundingClientRect = () => ({ top: 50000, bottom: 51000 });
  p.read('scroll');
  assert.equal(p.targets.at(-1), body);
  p.document.querySelector('#customer-reviews_feature_div').insertAdjacentHTML('beforeend', review('R2'));
  p.read('scroll');
  assert.equal(p.targets.at(-1).parentElement.id, 'R2');
});

test('SPEC010-TC03 空容器与同页评论锚点使用元素定位，未知页面不盲滚', () => {
  for (const id of ['reviewsMedley', 'customerReviews', 'customer-reviews_feature_div', 'cr-top-reviews']) {
    const p = page(`<div id="${id}"></div>`);
    p.read('scroll');
    assert.equal(p.targets.at(-1).id, id);
  }
  const p = page('<a id="acrCustomerReviewLink" href="#averageCustomerReviewsAnchor">评论</a><h2 id="averageCustomerReviewsAnchor">买家评论</h2>');
  p.read('scroll');
  assert.equal(p.targets.at(-1).id, 'averageCustomerReviewsAnchor');
  for (const href of ['/product-reviews/B0CC582PX1', 'https://example.com/#averageCustomerReviewsAnchor']) {
    const unknown = page(`<a id="acrCustomerReviewLink" href="${href}">评论</a><h2 id="averageCustomerReviewsAnchor">标题</h2>`);
    unknown.read('scroll');
    assert.equal(unknown.targets.length, 0);
    assert.equal(unknown.read().complete, false);
  }
});

test('SPEC010-TC04 空正文和隐藏评论不算完成，展开前定位按钮', () => {
  assert.equal(page('<div id="reviewsMedley"><div data-hook="review" id="R1"></div></div>').read().complete, false);
  assert.equal(page(`<div hidden>${review('hidden')}</div>`).read().complete, false);
  const p = page(`<div id="reviewsMedley">${review('R1')}</div>`);
  const node = p.document.querySelector('[data-hook="review"]');
  node.insertAdjacentHTML('beforeend', '<a class="a-expander-header" aria-expanded="false">展开</a>');
  const button = node.querySelector('a');
  button.onclick = () => {
    assert.equal(p.targets.at(-1), button);
    button.setAttribute('aria-expanded', 'true');
  };
  assert.equal(p.read().complete, false);
  p.read('expand');
  assert.equal(p.read().complete, true);
  node.insertAdjacentHTML('beforeend', '<a data-hook="seeOriginalReviewLink">查看原文</a>');
  assert.equal(p.read().complete, false);
});

test('SPEC010-TC05 明确可见的零评论与隐藏模板、未知结构区分', () => {
  assert.equal(page('<div id="customer-reviews_feature_div"><div data-hook="no-reviews-message">无评论</div></div>').read().complete, true);
  assert.equal(page('<div id="reviewsMedley"><div hidden data-hook="no-reviews-message">无评论</div></div>').read().complete, false);
});

test('SPEC010-TC06 新布局通过原稳定性检查后保存并分析', async () => {
  const p = page(`<div id="customer-reviews_feature_div">${review('R1')}</div>`);
  const calls = [];
  const flow = new Workflow({ inspect: async action => p.read(action), sleep: async () => {}, emit: () => {},
    api: async (route, method, payload) => {
      calls.push({ route, payload });
      return { id: '1', status: route === '/batches' ? 'validating' : 'completed' };
    } });
  await flow.start('https://www.amazon.com/dp/B0CC582PX1');
  assert.equal(calls[0].payload.complete, true);
  assert.equal(calls[1].route, '/batches/1/analyze');
});
