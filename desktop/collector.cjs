// 此函数在隔离世界读取页面，不访问Cookie或浏览器存储。
function inspectPage(action = 'read') {
  const text = (root, selector) => root.querySelector(selector)?.textContent.trim() || null;
  const visible = (element) => {
    if (!element) return false;
    for (let current=element; current; current=current.parentElement) {
      const style=getComputedStyle(current);
      if(current.hidden || style.display==='none' || style.visibility==='hidden') return false;
    }
    return true;
  };
  const collapsedButton = (node) => {
    const modern = node.querySelector('[data-hook="reviewExpandButtonContainer"]');
    if (modern && visible(modern) && visible(modern.querySelector('.a-see-more-text'))) return modern.querySelector('a[role="button"]');
    const classic = node.querySelector('.a-expander-header');
    return classic && visible(classic) && classic.getAttribute('aria-expanded') === 'false' ? classic : null;
  };
  if (document.querySelector('#captchacharacters,form[action*="validateCaptcha"],#ap_email,#ap_password')) return { wait: '请在右侧完成登录或验证码，再点击继续' };
  if (visible(document.querySelector('#sp-cc-accept'))) return { wait: '请在右侧自行选择Cookie偏好，再点击继续' };
  const asin = document.querySelector('#ASIN')?.value;
  const title = text(document, '#productTitle');
  const variant = [...document.querySelectorAll('#twister .selection')].map(x => x.textContent.trim()).join(' / ') || null;
  const source = { url: location.href, site: location.hostname.replace(/^www\./, ''), asin, title, variant };
  if (!asin || !title) return { ...source, error: 'product_unrecognized' };
  const section = document.querySelector('#reviewsMedley, #customerReviews');
  if (action === 'scroll') {
    if (section) {
      // 第一次定位评论区，随后继续向下滚动，让评论正文的懒加载区域真正进入视口。
      const bounds=section.getBoundingClientRect();
      if(bounds.top>innerHeight || bounds.bottom<0) section.scrollIntoView({behavior:'instant',block:'start'});
      else window.scrollBy(0,Math.max(innerHeight*.7,400));
    }
    else window.scrollBy(0, Math.max(innerHeight * 0.8, 500));
    return source;
  }
  const nodes = [...document.querySelectorAll('[data-hook="review"]')];
  if (action === 'expand') {
    let clicked = 0;
    for (const node of nodes) {
      const button = collapsedButton(node);
      if (button) { button.click(); clicked++; }
    }
    return { ...source, clicked };
  }
  const reviews = nodes.map(node => {
    // 公开页面存在新旧两种结构，按明确的data-hook分别解析；不猜测未知结构。
    const bodyNode = node.querySelector('[data-hook="reviewRichContentContainer"], [data-hook="review-body"]');
    const body = bodyNode?.innerText?.trim() || bodyNode?.textContent.trim() || '';
    const ratingText = text(node, '[data-hook="review-star-rating"] .a-icon-alt, [data-hook="cmps-review-star-rating"] .a-icon-alt');
    const ratingMatch = ratingText?.match(/([1-5])(?:[.,]0)?/);
    const dateText = text(node, '[data-hook="review-date"]');
    const regionMatch = dateText?.match(/Reviewed in (.+?) on |Rezension aus (.+?) vom |Bewertet in (.+?) am |在(.+?)(?:发布评论|于|，|审核)/);
    const country = regionMatch ? regionMatch.slice(1).find(Boolean) : null;
    const titleNode = node.querySelector('[data-hook="reviewTitle"], [data-hook="review-title"]');
    const link = titleNode?.closest('a');
    const titleParts = titleNode ? [...titleNode.querySelectorAll('span:not(.a-icon-alt)')].map(x=>x.textContent.trim()).filter(Boolean) : [];
    // 用户手动开启页面翻译后不能把译文当原文，要求恢复原文后重试。
    const originalLink = node.querySelector('[data-hook="seeOriginalReviewLink"]');
    const translated = !!originalLink && visible(originalLink);
    const expanded = !collapsedButton(node) && !translated;
    return {
      review_id: node.id || null, url: link?.href || null,
      rating: ratingMatch ? Number(ratingMatch[1]) : null,
      title: titleNode?.getAttribute('data-hook') === 'reviewTitle' ? titleNode.textContent.trim() : titleParts.at(-1) || null, body,
      date_text: dateText, country, variant: text(node, '[data-hook="format-strip"]'),
      verified: node.querySelector('[data-hook="avp-badge"]') ? true : null,
      expanded, captured_at: new Date().toISOString(),
    };
  });
  const noReviews = !!document.querySelector('[data-hook="no-reviews-message"]');
  return { ...source, reviews, recognized: !!section, noReviews, complete: !!section && (reviews.length > 0 || noReviews) && reviews.every(x => x.expanded), captured_at: new Date().toISOString() };
}
module.exports = { inspectPage };
