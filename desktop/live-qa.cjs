const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { inspectPage } = require('./collector.cjs');
const { Workflow } = require('./workflow.cjs');
// 真实站点验证脚本与应用复用采集流程；遇到需要人工操作时记录阻塞并停止该站点。
app.whenReady().then(async () => {
  const win = new BrowserWindow({ show:false, width:730,height:780,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,partition:'persist:amazon-review-flow'} });
  const directory=path.join(__dirname,'../data/qa',new Date().toISOString().replace(/[:.]/g,'-'));fs.mkdirSync(directory,{recursive:true});
  const results=[];
  for(const url of ['https://www.amazon.com/dp/B0CC582PX1','https://www.amazon.de/dp/B0C5J4FXXH']) {
    let snapshot;const events=[];
    try {
      await win.loadURL(url);
      const flow=new Workflow({inspect:async action=>{
        const result=await win.webContents.executeJavaScriptInIsolatedWorld(1001,[{code:`(${inspectPage.toString()})(${JSON.stringify(action)})`}]);
        if(result.wait)throw new Error(result.wait);
        return result;
      },emit:state=>events.push({status:state.status,message:state.message}),api:async(route,method,payload)=>{
        snapshot=payload;return {id:'qa',status:payload.complete?'qa_collected':'collection_failed'};
      }});
      await flow.start(url);
      const site=new URL(url).hostname;
      if(snapshot)fs.writeFileSync(path.join(directory,`${site}-snapshot.json`),JSON.stringify(snapshot,null,2),'utf8');
      const screen=await win.webContents.capturePage();fs.writeFileSync(path.join(directory,`${site}-page.png`),screen.toPNG());
      results.push({url,complete:snapshot?.complete||false,count:snapshot?.reviews?.length||0,events});
    } catch(error){results.push({url,error:error.message});}
  }
  fs.writeFileSync(path.join(directory,'live-qa.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
  win.destroy();app.quit();
});
