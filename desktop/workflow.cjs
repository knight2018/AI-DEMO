const { sameProduct, productURL } = require('./policy.cjs');
class Workflow {
  constructor({ inspect, api, emit, sleep = ms => new Promise(r => setTimeout(r, ms)) }) {
    Object.assign(this, { inspect, api, emit, sleep });
    this.busy = false; this.paused = false; this.waiter = null;
  }
  state(status, message, extra = {}) { this.emit({ status, message, ...extra }); }
  pause() { if (this.busy) { this.paused = true; this.state('waiting_for_user', '已暂停后续采集操作，点击继续恢复'); } }
  resume() { this.paused = false; this.waiter?.(); this.waiter = null; }
  async checkpoint() { if (this.paused) await new Promise(resolve => { this.waiter = resolve; }); }
  async read(action = 'read') {
    await this.checkpoint();
    let page = await this.inspect('read');
    while (page.wait) {
      this.paused = true; this.state('waiting_for_user', page.wait);
      await this.checkpoint(); page = await this.inspect('read');
    }
    if (page.error) throw new Error(page.error);
    const location = productURL(page.url);
    if (location.asin !== page.asin || location.site !== page.site) throw new Error('product_identity_mismatch');
    if (this.source && !sameProduct(this.source, page)) throw new Error('product_changed_restart_required');
    if (action !== 'read') page = await this.inspect(action);
    return page;
  }
  async start(inputURL) {
    if (this.busy) throw new Error('task_running');
    this.busy = true; this.source = null; this.paused = false;
    let last = null;
    try {
      this.state('preparing', '正在核对当前商品与评论来源');
      this.source = await this.read();
      this.state('collecting', '正在定位评论并展开正文');
      let previous = ''; let stable = 0;
      for (let round = 0; round < 12; round++) {
        await this.read('scroll'); await this.sleep(800);
        await this.read('expand'); await this.sleep(600);
        last = await this.read();
        this.state('collecting', `已读取 ${last.reviews?.length || 0} 条评论，正在检查完整性`);
        // 采集时间不参加稳定性比较，避免每轮时间变化导致永不收敛。
        const contentSignature = JSON.stringify((last.reviews || []).map(({ captured_at, ...r }) => r));
        stable = contentSignature === previous ? stable + 1 : 0;
        previous = contentSignature;
        if (last.complete && stable >= 2) break;
      }
      if (!last?.complete || stable < 2) throw new Error(last?.recognized ? 'collection_incomplete' : 'review_section_unrecognized');
      const batch = await this.save(inputURL, last, true, null);
      if (batch.status !== 'validating') { this.state(batch.status, batch.error, { batch }); return batch; }
      this.state('analyzing', '采集完成，正在通过 LangChain 分析原文证据', { batch });
      const result = await this.api(`/batches/${batch.id}/analyze`, 'POST');
      this.state(result.status, result.status === 'completed' ? '分析完成，可以查看证据与导出' : `分析失败：${result.error}，可重试`, { batch: result });
      return result;
    } catch (error) {
      let batch = null;
      if (this.source?.asin) {
        try { batch = await this.save(inputURL, last || { ...this.source, reviews: [] }, false, error.message); } catch { /* 原始错误保留在界面，不生成成功批次。 */ }
      }
      this.state('collection_failed', error.message, { batch });
      return batch;
    } finally { this.busy = false; this.paused = false; }
  }
  async save(inputURL, page, complete, error) {
    this.state('validating', '正在校验字段、去重并保存批次');
    return this.api('/batches', 'POST', {
      input_url: inputURL, url: this.source.url, site: this.source.site, asin: this.source.asin,
      title: this.source.title, variant: this.source.variant, captured_at: new Date().toISOString(),
      complete, error, reviews: page.reviews || [],
    });
  }
}
module.exports = { Workflow };
