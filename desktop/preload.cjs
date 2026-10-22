const { contextBridge, ipcRenderer } = require('electron');
// 仅向本地界面暴露固定能力，远程商品页没有预加载脚本。
contextBridge.exposeInMainWorld('desktop', {
  open: (url) => ipcRenderer.invoke('open-product', url),
  collect: () => ipcRenderer.invoke('collect'),
  pause: () => ipcRenderer.invoke('pause'),
  resume: () => ipcRenderer.invoke('resume'),
  retry: (id) => ipcRenderer.invoke('retry-analysis', id),
  history: () => ipcRenderer.invoke('history'),
  export: (id, kind) => ipcRenderer.invoke('export', id, kind),
  clear: () => ipcRenderer.invoke('clear-session'),
  showBrowser: (visible) => ipcRenderer.invoke('browser-visible', visible),
  onState: (callback) => ipcRenderer.on('state', (_, state) => callback(state)),
});
