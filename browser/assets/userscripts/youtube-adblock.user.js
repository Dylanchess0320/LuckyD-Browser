// ==UserScript==
// @name        YouTube Ad-Block
// @match       *://www.youtube.com/*
// @match       *://m.youtube.com/*
// @match       *://music.youtube.com/*
// @run-at      document-start
// ==/UserScript==
//
// LuckyD built-in YouTube ad blocker (rewritten 2026 - short-circuit method).
//   1. RESPONSE SCRUB - strip ad placements from /youtubei/v1/player fetch &
//      XHR responses BEFORE the player parses them. With server-side ad
//      insertion the ad media rides in on googlevideo.com like content, so
//      the only reliable kill is deleting the ad schedule data itself. Runs
//      at document-start so the hooks predate YouTube's first request.
//   2. SHORT-CIRCUIT - jump currentTime to the end of a playing ad (finishes
//      unskippable ads instantly; works even with no skip button).
//   3. DATA - strip ad slots from the ytInitialPlayerResponse global.
//   4. NETWORK - block dedicated ad hosts (IMA SDK, DoubleClick). The C++
//      interceptor also handles these; this is the same-origin fallback.
//   5. COSMETIC - remove ad DOM + dismiss the "ad blockers violate ToS" popup.
// NOTE: never delete the `params` field from player requests - that broke video
// loading in the previous version.
(()=>{
'use strict';
if(window.__ldYtAdBlock)return;window.__ldYtAdBlock=true;

const TAG='[LuckyD] YouTube Ad-Block';
let adShortCircuits=0;

// ---------------- 1. player-response data strip ----------------
const ADKEYS=['adPlacements','playerAds','adBreakHeartbeatParams','adSlots'];
function stripAdSlots(node){
if(!node||typeof node!=='object')return;
if(Array.isArray(node)){for(const it of node)stripAdSlots(it);return;}
for(const k of ADKEYS){if(k in node){try{delete node[k];}catch(_){node[k]=undefined;}}}
for(const k in node){const v=node[k];if(v&&typeof v==='object')stripAdSlots(v);}
}
function scrubAdsFromPlayerResponse(){
for(const key of ['ytInitialPlayerResponse','ytInitialData']){
const obj=window[key];
if(obj&&typeof obj==='object'){stripAdSlots(obj);}
}
let cache=window.ytInitialPlayerResponse;
try{
Object.defineProperty(window,'ytInitialPlayerResponse',{
configurable:true,
get(){return cache;},
set(v){stripAdSlots(v);cache=v;}
});
}catch(_){/* non-configurable on some builds - periodic rescrub covers it */}
}

// ---------------- 2. fetch/XHR: block ad hosts, scrub player responses ----
function isAdHost(u){
return u.includes('imasdk.googleapis.com')||
u.includes('doubleclick.net')||
u.includes('googlesyndication.com/pagead')||
u.includes('youtube.com/pagead/')||
u.includes('youtube.com/get_midroll_info')||
u.includes('youtube.com/ptracking')||
u.includes('youtube.com/api/stats/ads')||
u.includes('adservice.google.com')||
u.includes('fundingchoicesmessages.google.com');}
function isPlayerApi(u){
return u.includes('youtubei/v1/player');}
// Rebuild a fetch Response with scrubbed JSON. Content-encoding/length MUST
// be dropped: text() already decoded the body, and our JSON differs in size.
function scrubbedFetchResponse(resp,text){
try{
const json=JSON.parse(text);
stripAdSlots(json);
const h=new Headers(resp.headers);
h.delete('content-encoding');h.delete('content-length');
return new Response(JSON.stringify(json),{status:resp.status,statusText:resp.statusText,headers:h});
}catch(_){return new Response(text,{status:resp.status,statusText:resp.statusText});}
}

const _F=window.fetch;
if(_F){window.fetch=function(i,o){
let u='';
try{u=('object'==typeof i&&i&&i.url?i.url:String(i)).toLowerCase();}catch(_){}
if(u&&isAdHost(u)){return Promise.resolve(new Response('',{status:204,statusText:'No Content'}));}
const p=_F.apply(this,arguments);
if(u&&isPlayerApi(u)){
return p.then(resp=>{
if(!resp||!resp.ok)return resp;
return resp.clone().text().then(text=>scrubbedFetchResponse(resp,text)).catch(()=>resp);
});
}
return p;};}

const _O=XMLHttpRequest.prototype.open;
const _S=XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open=function(m,u){this.__ldu=String(u||'');return _O.apply(this,arguments);};
XMLHttpRequest.prototype.send=function(){
const u=(this.__ldu||'').toLowerCase();
if(u&&isAdHost(u)){try{this.abort();}catch(_){}return;}
if(u&&isPlayerApi(u)){
this.addEventListener('readystatechange',()=>{
if(this.readyState!==4)return;
try{
const text=this.responseText;
const json=JSON.parse(text);
stripAdSlots(json);
if(this.responseType==='json'){
Object.defineProperty(this,'response',{get:()=>json});
}else{
const out=JSON.stringify(json);
Object.defineProperty(this,'responseText',{get:()=>out});
Object.defineProperty(this,'response',{get:()=>out});
}
}catch(_){/* not JSON or already read - leave it alone */}});
}
return _S.apply(this,arguments);};


// ---------------- 3. short-circuit + skip + mute ----------------
function videoEl(){return document.querySelector('video.html5-main-video')||document.querySelector('video');}
function playerEl(){return document.querySelector('.html5-video-player')||document.getElementById('movie_player');}
function isAdPlaying(p){
if(!p)return false;
if(p.classList.contains('ad-showing')||p.classList.contains('ad-interrupting'))return true;
return !!document.querySelector('.ytp-ad-player-overlay,.ytp-ad-player-overlay-instream-info,.ytp-ad-text-overlay,.ytp-ad-message-overlay');}
// Content-position guard: ads share the <video> element with the content, so
// a short-circuit (or a skip click) leaves the position polluted — pre-rolls
// then start mid-video instead of at 0. Capture the content position when an
// ad starts; restore it when the ad ends (0 for pre-rolls, the resume point
// for mid-rolls). Also restores the pre-ad mute state.
let preAdTime=null,preAdMuted=null,preAdUrl='';
function trackAdBoundary(){
const v=videoEl();const p=playerEl();
if(!v)return;
const adNow=isAdPlaying(p);
if(adNow){
if(preAdTime===null){
preAdUrl=location.href;
preAdMuted=v.muted;
let t=NaN;
try{if(p&&typeof p.getCurrentTime==='function')t=p.getCurrentTime();}catch(_){}
preAdTime=(isFinite(t)&&t>=0)?t:(v.currentTime||0);
}
return;
}
if(preAdTime!==null&&location.href===preAdUrl){
try{
if(isFinite(v.duration)&&v.duration>0&&Math.abs(v.currentTime-preAdTime)>1){
v.currentTime=Math.max(0,preAdTime);
}
if(preAdMuted!==null&&v.muted!==preAdMuted)v.muted=preAdMuted;
}catch(_){}
preAdTime=null;preAdMuted=null;}}
function finishAd(){
const v=videoEl();const p=playerEl();
if(!v)return;
const skip=document.querySelector('.ytp-ad-skip-button,.ytp-ad-skip-button-modern,.ytp-skip-ad-button,.ytp-ad-skip-button-container button,.ytp-ad-skip-button-slot button');
if(skip&&skip.offsetParent!==null){skip.click();return;}
if(isAdPlaying(p)){
try{
v.muted=true;
if(isFinite(v.duration)&&v.duration>0&&v.currentTime<v.duration-0.15){
v.currentTime=Math.max(0,v.duration-0.1);adShortCircuits++;}
if(v.paused){v.play().catch(()=>{});}
}catch(_){}}
const close=document.querySelector('.ytp-ad-overlay-close-button');
if(close)close.click();}

// ---------------- 4. cosmetic removal + popup ----------------
const AD_SELECTORS=['#masthead-ad','.ytd-video-masthead-ad-v3-renderer',
'.ytd-ad-slot-renderer','.ytd-in-feed-ad-layout-renderer',
'.ytd-promoted-sparkles-web-renderer','.ytd-promoted-sparkles-text-search-renderer',
'.ytd-statement-banner-renderer','.ytd-companion-slot-renderer',
'.ytd-search-pyv-renderer','.ytd-compact-promoted-video-renderer',
'.ytd-promoted-video-renderer','.ytd-display-ad-renderer',
'.video-ads','.ytp-ad-player-overlay','.ytp-ad-image-overlay','.ytp-ad-text-overlay',
'.ytp-ad-action-interstitial','#player-ads','.ad-container','.ytd-merch-shelf-renderer',
'ytd-ad-slot-renderer','ytd-in-feed-ad-layout-renderer','ytd-promoted-video-renderer',
'iframe[src*="doubleclick.net"]','iframe[src*="googlesyndication.com"]'];
function removeAdDom(){
for(const s of AD_SELECTORS){try{for(const e of document.querySelectorAll(s))e.remove();}catch(_){}}}
function dismissBlockPopup(){
const dlg=document.querySelector('ytd-enforcement-message-view-model,tp-yt-paper-dialog.yt-popup, ytd-popup-container tp-yt-paper-dialog');
if(dlg){
const btn=dlg.querySelector('button[aria-label*="lose" i],button[aria-label*="ismiss" i],#dismiss-button button,.yt-spec-button-shape-next--dismiss');
if(btn)btn.click();else dlg.remove();}
for(const e of document.querySelectorAll('ytd-enforcement-message-view-model')){try{e.remove();}catch(_){}}}

// ---------------- observers + timers ----------------
// (observing `document`, not documentElement: at document-start the root
// element may not exist yet)
scrubAdsFromPlayerResponse();
removeAdDom();
new MutationObserver(()=>{removeAdDom();dismissBlockPopup();})
.observe(document,{childList:true,subtree:true});
setInterval(()=>{trackAdBoundary();finishAd();dismissBlockPopup();},150);
setInterval(()=>{scrubAdsFromPlayerResponse();removeAdDom();},1500);
window.__ldYtStats=()=>({shortCircuits:adShortCircuits});
console.info(TAG+' active (response-scrub + short-circuit method)');
})();

