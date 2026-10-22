const HOSTS = new Set(['amazon.com', 'www.amazon.com', 'amazon.de', 'www.amazon.de']);
function allowedNavigation(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && HOSTS.has(url.hostname) && !url.username && !url.password && !url.port; }
  catch { return false; }
}
function productURL(value) {
  if (!allowedNavigation(value)) throw new Error('仅支持美国站和德国站商品地址');
  const url = new URL(value);
  const match = url.pathname.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})(?:\/|$)/i);
  if (!match) throw new Error('请输入包含商品ASIN的详情页地址');
  return { site: url.hostname.replace(/^www\./, ''), asin: match[1].toUpperCase(), url: url.href };
}
function sameProduct(a, b) { return a.site === b.site && a.asin === b.asin && a.variant === b.variant; }
module.exports = { allowedNavigation, productURL, sameProduct };
