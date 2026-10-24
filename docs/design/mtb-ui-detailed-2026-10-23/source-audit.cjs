// Read-only analysis of application sources. Output stays beside this script.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const root = path.resolve(__dirname, '../../..');
const ts = require(require.resolve('typescript', { paths: [path.join(root, 'frontend')] }));
const walk = d => fs.readdirSync(d, { withFileTypes: true }).flatMap(e => ['node_modules','.expo','.git','dist','build'].includes(e.name) ? [] : e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]);
const files = walk(path.join(root, 'frontend')).filter(f => !/node_modules|\.expo|[\\/]\.git[\\/]/.test(f) && /\.(tsx|ts)$/.test(f));
const sourceFiles = files.filter(f => /[\\/](app|src)[\\/]/.test(f) && !/[\\/]tests[\\/]/.test(f));
const rel = f => path.relative(root, f).replaceAll('\\', '/');
const squash = s => s.replace(/\s+/g, ' ').trim();
const title = s => s.replace(/\[|\]/g,'').split('-').map(x => x.charAt(0).toUpperCase()+x.slice(1)).join(' ');
const content = new Map(sourceFiles.map(f => [f, fs.readFileSync(f, 'utf8')]));
const controls = [], configs = [], routes = [], fields = [], snapshots = [], modules = [], optionSets = [];
for (const [file, text] of content) {
 const filePath = rel(file);
 snapshots.push({file:filePath,sha256:crypto.createHash('sha256').update(text).digest('hex')});
 const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith('tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
 const line = n => sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;
 const attrs = n => Object.fromEntries(n.attributes.properties.filter(ts.isJsxAttribute).map(a => [a.name.getText(sf), a.initializer ? (ts.isJsxExpression(a.initializer) && a.initializer.expression ? a.initializer.expression.getText(sf) : a.initializer.getText(sf)) : 'true']));
 const strings = n => { const out=[]; const visit=x=>{if(ts.isStringLiteral(x)||ts.isNoSubstitutionTemplateLiteral(x))out.push(x.text);ts.forEachChild(x,visit);}; visit(n);return [...new Set(out)]; };
 const routeStrings = n => strings(n).filter(x => /^\/(\(app\)|\(auth\)|[a-z])/.test(x) && !/^\/api\//.test(x));
 const readable = n => { const out=[]; const visit=x=>{if(ts.isJsxElement(x)){x.children.forEach(visit);return;}if(ts.isJsxSelfClosingElement(x))return;if(ts.isJsxText(x)){const t=squash(x.text);if(t)out.push(t);return;}if(ts.isJsxExpression(x)&&x.expression){const t=x.expression;if(ts.isStringLiteral(t))out.push(t.text);else if(ts.isIdentifier(t)||ts.isPropertyAccessExpression(t)||ts.isConditionalExpression(t)||ts.isTemplateExpression(t))out.push('{'+squash(t.getText(sf)).slice(0,110)+'}');}};visit(n);return squash(out.join(' ')); };
 const context = n => {const c=[];for(let a=n.parent,i=0;a&&i<14;a=a.parent,i++){if(ts.isConditionalExpression(a))c.push((n.pos>=a.whenTrue.pos&&n.end<=a.whenTrue.end?'TRUE: ':'FALSE: ')+squash(a.condition.getText(sf)));if(ts.isBinaryExpression(a)&&a.operatorToken.kind===ts.SyntaxKind.AmpersandAmpersandToken)c.push('AND: '+squash(a.left.getText(sf)));}return [...new Set(c)].map(s=>s.slice(0,220));};
 const imports = [];
 for (const n of sf.statements) if(ts.isImportDeclaration(n) && ts.isStringLiteral(n.moduleSpecifier)) {
  let target=n.moduleSpecifier.text;
  if(target.startsWith('@/'))target=path.join(root,'frontend',target.slice(2));else if(target.startsWith('.'))target=path.resolve(path.dirname(file),target);else continue;
  const resolved=[target+'.tsx',target+'.ts',path.join(target,'index.tsx'),path.join(target,'index.ts')].find(p=>content.has(p));
  if(resolved)imports.push(rel(resolved));
 }
 modules.push({file:filePath,imports});
 if(filePath.startsWith('frontend/app/')&&!/(?:_layout|\+html)\.tsx$/.test(filePath)){
  const internal='/'+filePath.replace('frontend/app/','').replace(/\.tsx$/,'').replace(/\/index$/,'');
  const browser=internal.replace(/\/\([^/]+\)/g,'')||'/';
  routes.push({id:internal,browser,file:filePath,title:title(internal.split('/').at(-1)||'Home'),role:internal.includes('(auth)')?'access':internal.split('/')[2]||'shared',imports,literalDestinations:routeStrings(sf),literalInboundReferences:0});
 }
 const visit = n => {
  if(ts.isJsxOpeningElement(n)||ts.isJsxSelfClosingElement(n)){
   const a=attrs(n), tag=n.tagName.getText(sf), events=Object.entries(a).filter(([k])=>/^on[A-Z]/.test(k));
   const full=ts.isJsxOpeningElement(n)?n.parent:n;
   if(events.length){
    const label=a.label||a.title||a.accessibilityLabel||readable(full)||a.testID||tag+' · '+events[0][0];
    controls.push({id:'C'+String(controls.length+1).padStart(4,'0'),file:filePath,line:line(n),tag,label:squash(label).slice(0,230),labelIsDynamic:/\{|\?|\.label|\.title/.test(label),kind:events.some(([k])=>/^on(Press|Click|Action|Save|Confirm|Submit|Open|Add|Delete|Remove|Edit|Decision|Accept|Reject|Send|Log|Select|Toggle|Retry|Back)/.test(k))?'action':'input-or-lifecycle',events:Object.fromEntries(events),testID:a.testID||null,disabled:a.disabled||null,conditions:context(n),destinations:routeStrings(full),source:squash(full.getText(sf)).slice(0,1500)});
   }
   if(/TextInput|Picker|Select|Switch|Checkbox|CheckBox|PhoneField|Input|Field|Dropdown/.test(tag)) fields.push({file:filePath,line:line(n),tag,label:a.label||a.placeholder||a.testID||a.value||tag,props:a});
  }
  if(ts.isArrayLiteralExpression(n)){
   const vals=strings(n);let a=n.parent;while(a&&!ts.isVariableDeclaration(a)&&!ts.isJsxExpression(a)&&!ts.isReturnStatement(a))a=a.parent;
   if(vals.length&&vals.length<80)optionSets.push({id:'O'+String(optionSets.length+1).padStart(4,'0'),file:filePath,line:line(n),label:a&&ts.isVariableDeclaration(a)?a.name.getText(sf):'Inline option / value array',values:vals,source:squash(n.getText(sf)).slice(0,1800)});
  }
  if(ts.isObjectLiteralExpression(n)){
   const props=n.properties.filter(ts.isPropertyAssignment);const keys=Object.fromEntries(props.map(p=>[p.name.getText(sf).replace(/['"]/g,''),p.initializer]));
   if((keys.label||keys.title)&&(keys.route||keys.href||keys.onPress||keys.onClick||keys.action||keys.key||keys.value))configs.push({file:filePath,line:line(n),label:(keys.label||keys.title).getText(sf),keys:Object.keys(keys),destinations:routeStrings(n),source:squash(n.getText(sf)).slice(0,1200)});
  }
  ts.forEachChild(n,visit);
 };visit(sf);
}
for(const route of routes){for(const [file,text] of content){if(rel(file)===route.file)continue;for(const s of [route.id,route.browser]){if(s!=='/'&&text.includes('"'+s+'"')||s!=='/'&&text.includes("'"+s+"'")){route.literalInboundReferences++;break;}}}}
const excluded = {'/(app)/sponsor/trial-detail':'Legacy redirect; no new standalone design. Keep redirect behavior.','/(app)/clinical/invite-patient':'Unreferenced legacy route per project context; preserve in source audit, use current Add Patient flow in main navigation.','/(app)/dev/uctsm-workbench':'Developer workbench; keep in developer-only catalog, exclude from product navigation.'};
for(const route of routes)route.reviewDisposition=excluded[route.id]||'Retain destination; source presence alone does not prove runtime reachability.';
for(const r of routes){const deps=new Set();const add=f=>{if(deps.has(f))return;deps.add(f);const m=modules.find(m=>m.file===f);if(m)m.imports.forEach(add);};add(r.file);r.sourceModules=[...deps];r.controlIds=controls.filter(c=>deps.has(c.file)).map(c=>c.id);}
const audit={generated:'2026-10-23',method:'TypeScript AST read-only source inventory; static declaration sites, not runtime button counts or verified permissions. Import closure is conservative: unused named exports may be included. Option arrays include non-UI value arrays for completeness.',counts:{sourceFiles:sourceFiles.length,routes:routes.length,controlSites:controls.length,actionSites:controls.filter(c=>c.kind==='action').length,configurationEntries:configs.length,inputSites:fields.length,optionSets:optionSets.length},routes,controls,configs,fields,optionSets,modules,snapshots};
fs.writeFileSync(path.join(__dirname,'source-inventory.json'),JSON.stringify(audit,null,2));
fs.writeFileSync(path.join(__dirname,'inventory-data.js'),'window.MTB_SOURCE_INVENTORY='+JSON.stringify(audit)+';');
console.log(JSON.stringify(audit.counts));
