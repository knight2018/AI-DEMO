const { app, BrowserWindow, WebContentsView, ipcMain, dialog, session } = require('electron');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { pathToFileURL } = require('node:url');
const { allowedNavigation, productURL } = require('./policy.cjs');
const { inspectPage } = require('./collector.cjs');
const { Workflow } = require('./workflow.cjs');
let win, view, backend, workflow, baseURL, browserVisible = true, inputURL = '', analyzing = false;
const token = randomBytes(32).toString('hex');
const root = path.resolve(__dirname, '..');
if (!app.requestSingleInstanceLock()) app.quit();
app.on('second-instance', () => { if (win) { if(win.isMinimized())win.restore();win.focus(); } });
const localURL = pathToFileURL(path.join(__dirname, 'ui/index.html')).href;
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function api(route, method = 'GET', body) {
  const response = await fetch(baseURL + route, { method, headers: { 'x-demo-token': token, 'content-type': 'application/json' }, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(120000) });
  if (!response.ok) throw new Error(`本地服务请求失败(${response.status})`);
  return response.json();
}
function emit(state) { if (win && !win.isDestroyed()) win.webContents.send('state', state); }
function layout() {
  if (!win || !view) return;
  const [width, height] = win.getContentSize();
  const left = Math.round(width * .48);
  view.setBounds(browserVisible ? { x: left, y: 154, width: width - left - 14, height: Math.max(0, height - 168) } : { x: 0, y: 0, width: 0, height: 0 });
}
function handle(name, callback) {
  ipcMain.handle(name, async (event, ...args) => {
    if (event.sender !== win.webContents || event.senderFrame.url !== localURL) throw new Error('调用来源不受信任');
    return callback(...args);
  });
}
async function startBackend() {
  const port = await new Promise(resolve => { const server = net.createServer(); server.listen(0, '127.0.0.1', () => { const port = server.address().port; server.close(() => resolve(port)); }); });
  baseURL = `http://127.0.0.1:${port}`;
  backend = spawn('python', ['-m', 'uvicorn', 'backend.app:create_app', '--factory', '--host', '127.0.0.1', '--port', String(port), '--no-access-log'], {
    cwd: root, windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'],
    env: { ...process.env, DEMO_API_TOKEN: token, PYTHONUTF8: '1', LANGCHAIN_TRACING_V2: 'false', LANGSMITH_TRACING: 'false' },
  });
  backend.stderr.on('data', () => {});
  let exited = false; backend.on('exit', () => { exited = true; });
  for (let i = 0; i < 60; i++) {
    if (exited) throw new Error('Python后端启动失败，请安装 requirements.txt 中的依赖');
    try { return await api('/health'); } catch { await sleep(300); }
  }
  throw new Error('Python后端启动超时');
}
app.whenReady().then(async () => {
  win = new BrowserWindow({ width: 1440, height: 960, minWidth: 1100, minHeight: 740, title: 'ReviewFlow · 评论洞察工作台', backgroundColor: '#f4f6f5', webPreferences: { preload: path.join(__dirname, 'preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false } });
  const remoteSession = session.fromPartition('persist:amazon-review-flow');
  remoteSession.setPermissionRequestHandler((_, __, callback) => callback(false));
  remoteSession.setPermissionCheckHandler(() => false);
  remoteSession.on('will-download', event => event.preventDefault());
  view = new WebContentsView({ webPreferences: { session: remoteSession, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true } });
  win.contentView.addChildView(view); layout(); win.on('resize', layout);
  view.webContents.setWindowOpenHandler(({ url }) => { if (allowedNavigation(url)) view.webContents.loadURL(url).catch(() => emit({ status: 'collection_failed', message: '页面加载失败' })); return { action: 'deny' }; });
  for (const eventName of ['will-navigate', 'will-redirect']) view.webContents.on(eventName, (event, url) => { if (!allowedNavigation(url)) event.preventDefault(); });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', event => event.preventDefault());
  workflow = new Workflow({ inspect: action => view.webContents.executeJavaScriptInIsolatedWorld(1001, [{ code: `(${inspectPage.toString()})(${JSON.stringify(action)})` }]), api, emit });
  handle('open-product', async value => {
    if (workflow.busy || analyzing) throw new Error('当前任务运行中');
    const product = productURL(value); inputURL = product.url;
    emit({ status: 'preparing', message: '正在打开商品，可在右侧手动登录或选择规格' });
    try { await view.webContents.loadURL(inputURL); emit({ status: 'idle', message: '页面已打开，准备好后点击开始采集' }); }
    catch { emit({ status: 'collection_failed', message: '商品页面加载失败，请检查网络后重新打开' }); }
  });
  handle('collect', () => { if (!inputURL) throw new Error('请先打开商品'); if (workflow.busy || analyzing) throw new Error('当前任务运行中'); void workflow.start(inputURL); });
  handle('pause', () => workflow.pause()); handle('resume', () => workflow.resume());
  handle('history', () => api('/batches'));
  handle('retry-analysis', async id => {
    if (!/^[a-f0-9]{32}$/.test(id) || workflow.busy || analyzing) throw new Error('无法启动重试');
    analyzing = true; emit({ status: 'analyzing', message: '使用原固定样本重新分析' });
    try { const batch = await api(`/batches/${id}/analyze`, 'POST'); emit({ status: batch.status, message: batch.error || '分析完成', batch }); }
    finally { analyzing = false; }
  });
  handle('browser-visible', visible => { browserVisible = !!visible; layout(); });
  handle('clear-session', async () => {
    if (workflow.busy || analyzing) throw new Error('请等待任务结束');
    await view.webContents.loadURL('about:blank'); await remoteSession.clearStorageData(); await remoteSession.clearCache(); inputURL = '';
    emit({ status: 'idle', message: '本机浏览器会话已清除' });
  });
  handle('export', async (id, kind) => {
    if (!/^[a-f0-9]{32}$/.test(id) || !['csv', 'html'].includes(kind)) throw new Error('导出参数无效');
    const choice = await dialog.showSaveDialog(win, { defaultPath: `reviews-${id.slice(0, 8)}.${kind}`, filters: [{ name: kind.toUpperCase(), extensions: [kind] }] });
    if (choice.canceled) return false;
    const response = await fetch(`${baseURL}/batches/${id}/export/${kind}`, { headers: { 'x-demo-token': token } });
    if (!response.ok) throw new Error('导出失败，分析报告可能尚未生成');
    await fs.promises.writeFile(choice.filePath, await response.text(), 'utf8'); return true;
  });
  await win.loadFile(path.join(__dirname, 'ui/index.html'));
  try { const health = await startBackend(); emit({ status: 'idle', message: health.model_configured ? '服务就绪 · 输入商品地址开始' : '采集可用；未配置AGI_KEY，分析将失败' }); }
  catch (error) { emit({ status: 'collection_failed', message: error.message }); }
  win.on('closed', () => { view.webContents.close(); backend?.kill(); });
}).catch(error => { dialog.showErrorBox('启动失败', error.message); app.quit(); });
app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => backend?.kill());
