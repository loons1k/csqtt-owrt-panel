'use strict';'require baseclass';var CATALOG_URL=L.resource('view/csqtt/i18n.ru.json');var REQUEST_TIMEOUT=6000;var catalog=null;var pending=null;var reported=false;function normalize(s){return String(s).trim().replace(/[ \t\n]+/g,' ');}
function t(s,c){if(catalog&&c==null){var key=normalize(s);if(Object.prototype.hasOwnProperty.call(catalog,key))
return catalog[key];}
return s;}
function request(){var opts={credentials:'same-origin',cache:'no-cache'};var ctrl=null;var timer=null;var timedOut=false;if(typeof AbortController==='function'){ctrl=new AbortController();opts.signal=ctrl.signal;timer=window.setTimeout(function(){timedOut=true;ctrl.abort();},REQUEST_TIMEOUT);}
function done(){if(timer!=null){window.clearTimeout(timer);timer=null;}}
return window.fetch(CATALOG_URL,opts).then(function(res){done();if(!res.ok)
throw new Error('HTTP '+res.status);return res.json();}).catch(function(err){done();if(timedOut){var e=new Error('i18n catalog request timed out');e.timedOut=true;throw e;}
throw err;});}
function fetchCatalog(){return request().catch(function(err){if(err&&err.timedOut)
throw err;return request();});}
function reportFailure(){if(reported)
return;reported=true;try{console.error('CSQTT i18n: could not load '+CATALOG_URL);}catch(e){}
L.require('ui').then(function(ui){if(!ui||typeof ui.addNotification!=='function')
return;var p=document.createElement('p');p.textContent='CSQTT: the Russian interface could not be loaded '+'(view/csqtt/i18n.ru.json). The panel is shown in English. '+'Reload the page or reinstall luci-app-csqtt.';ui.addNotification(null,p,'error');}).catch(function(){});}
function load(){if(catalog)
return Promise.resolve(catalog);if(!pending){pending=fetchCatalog().then(function(data){if(!data||typeof data!=='object'||Array.isArray(data))
throw new Error('invalid i18n catalog');catalog=data;return catalog;}).catch(function(){pending=null;reportFailure();return null;});}
return pending;}
return baseclass.extend({load:load,t:t});