const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
global.window={};require('../site/data.js');const d=window.BW_DEMO;
assert(d.articles.length>=8);assert(d.articles.every(a=>a.demo));assert(d.activities.every(a=>a.demo));
assert.equal(new Set(d.articles.map(a=>a.id)).size,d.articles.length);
for(const a of d.articles){assert(fs.existsSync('site/'+a.cover));if(a.kind==='review'){assert(a.score>=0&&a.score<=10&&Number.isInteger(a.score*2));assert(a.pros.length&&a.cons.length)}}
const stub=()=>({innerHTML:'',textContent:'',style:{},classList:{remove(){},toggle(){},contains(){return false}},setAttribute(){},value:'',hidden:true,close(){}});
const elements=new Map();const doc={querySelector(s){if(['#listing','#filter-q','#filter-category','#filter-platform','#filter-min','#filter-sort','#hot-note'].includes(s))return null;return elements.get(s)||elements.set(s,stub()).get(s)},documentElement:{},addEventListener(){}};
const ctx={window:{BW_DEMO:d,addEventListener(){}},document:doc,localStorage:{getItem(){return null},setItem(){}},location:{hash:'#/',protocol:'file:',hostname:''},structuredClone,URLSearchParams,console,setTimeout,clearTimeout};vm.createContext(ctx);vm.runInContext(fs.readFileSync('site/app.js','utf8'),ctx);
assert.equal(ctx.window.BW_TEST.esc('<img onerror="x">'),'&lt;img onerror=&quot;x&quot;&gt;');assert.equal(ctx.window.BW_TEST.safeUrl('javascript:alert(1)'),'assets/embers.svg');assert(elements.get('#main').innerHTML.includes('编辑精选'));
for(const route of ['/reviews','/news','/article/1','/activities','/activity/1','/business','/about','/account','/publisher','/legal/privacy','/admin','/missing']){ctx.location.hash='#'+route;vm.runInContext('render()',ctx);assert(elements.get('#main').innerHTML.length>50,route)}
ctx.window.BW_TEST.state.lang='en';vm.runInContext('render()',ctx);assert.equal(doc.documentElement.lang,'en');console.log('P1: data, assets, all public routes, English and XSS escaping passed.');
