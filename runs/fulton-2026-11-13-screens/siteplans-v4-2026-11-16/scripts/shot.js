const {chromium}=require('playwright'); const fs=require('fs'); const path=require('path');
(async()=>{const [svg,out,w]=process.argv.slice(2);const html=path.join(path.dirname(out),'_shot.html');
fs.writeFileSync(html,`<html><body style="margin:0;background:#fff"><img src="file://${svg}" style="display:block;width:${+w||1180}px"></body></html>`);
const b=await chromium.launch();const p=await b.newPage({viewport:{width:+w||1180,height:900}});
await p.goto('file://'+html);await p.waitForTimeout(500);await p.screenshot({path:out,fullPage:true});await b.close();})();
