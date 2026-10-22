const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
// 只验证公开页面；不修改浏览器指纹，不处理验证码。
app.whenReady().then(async () => {
  const win = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
  const results = [];
  for (const url of ['https://www.amazon.com/dp/B0CC582PX1', 'https://www.amazon.de/dp/B0C5J4FXXH']) {
    try {
      await Promise.race([win.loadURL(url), new Promise((_, reject) => setTimeout(() => reject(new Error('页面加载超时')), 25000))]);
      const result = await win.webContents.executeJavaScript(`({title:document.title, url:location.href, reviews:document.querySelectorAll('[data-hook="review"]').length, product:document.querySelector('#productTitle')?.textContent.trim() || null, text:document.body.innerText.slice(0,1600)})`);
      const details = await win.webContents.executeJavaScript(`({reviews:[...document.querySelectorAll('[data-hook="review"]')].slice(0,2).map(x=>x.outerHTML), asin:document.querySelector('#ASIN')?.value, regions:[...document.querySelectorAll('#customerReviews,#reviewsMedley,#cm-cr-dp-review-list')].map(x=>x.id)})`);
      fs.mkdirSync(path.join(__dirname, '../data'), { recursive: true });
      fs.writeFileSync(path.join(__dirname, '../data', new URL(url).hostname + '-dom.json'), JSON.stringify(details, null, 2));
      results.push({ requested: url, ...result });
    } catch (error) { results.push({ requested: url, error: error.message }); }
  }
  fs.mkdirSync(path.join(__dirname, '../data'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, '../data/probe.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results));
  win.destroy(); app.quit();
});
