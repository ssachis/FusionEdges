'use strict';
// A top-level authorization return should never display a second repository UI.
if(parent===window && !new URLSearchParams(location.search).has('auth_error')) {
  document.documentElement.style.visibility='hidden';
  const connectionChannel=new BroadcastChannel('fusionedges-github-connection');
  connectionChannel.postMessage('refresh');
  if(window.opener)window.close();
  location.replace('http://127.0.0.1:8000/');
} else {
function initPicker(){
let session,repos=[], loading=false, watchTimer=null;
const watching=new URLSearchParams(location.search).has('watch');
const $=s=>document.querySelector(s), channel=new BroadcastChannel('fusionedges-github-connection');
function notify(data){if(parent!==window)parent.postMessage(data,'http://127.0.0.1:8000');}
async function api(path,body){const response=await fetch('/api/github/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-FusionEdges-CSRF':session.csrf}:{},body:body?JSON.stringify(body):undefined});const result=await response.json();if(!response.ok)throw Error(result.error||'Could not connect.');return result;}
function error(e){$('#notice').textContent=e.message;notify({type:'fusionedges-error',message:e.message});}
function render(){const list=$('#repos');list.replaceChildren();for(const r of repos.filter(r=>r.full_name.toLowerCase().includes($('#search').value.toLowerCase()))){const b=document.createElement('button');b.textContent=r.full_name+(r.private?' · private':'');b.onclick=()=>{notify({type:'fusionedges-repository',repo:r.full_name});if(parent===window)$('#notice').textContent='Open the repository picker in FusionEdges to select this repository.';};list.append(b);}if(!list.children.length)list.textContent=repos.length?'No repositories match your search.':'Finish selecting repository access in the GitHub window. This list updates automatically.';}
async function load(){session=await api('session');$('#connect').hidden=session.authenticated;$('#connected').hidden=!session.authenticated;$('#account').textContent=session.authenticated?'Connected as '+session.user.login:'GitHub is optional. You can always paste both links in FusionEdges.';$('#login').hidden=!session.github_login_configured;$('#setup').textContent=session.github_login_configured?'':'GitHub login needs one-time setup. A token is also supported.';notify({type:'fusionedges-connected',connected:session.authenticated});const access=$('#manage-access');access.hidden=!session.install_url;if(session.install_url&&/^https:\/\/github\.com\/apps\/[A-Za-z0-9_-]+\/installations\/new$/.test(session.install_url))access.href=session.install_url;if(session.authenticated){repos=(await api('repositories')).repositories;notify({type:'fusionedges-repositories',repositories:repos.map(r=>r.full_name)});render();if(repos.length){notify({type:'fusionedges-ready'});if(watchTimer){clearInterval(watchTimer);watchTimer=null;}}}}
$('#token-form').onsubmit=async e=>{e.preventDefault();const token=$('#token').value;$('#token').value='';try{await api('token',{token});await load();channel.postMessage('refresh');}catch(e){error(e);}};
$('#disconnect').onclick=async()=>{try{await api('disconnect',{});repos=[];await load();channel.postMessage('refresh');}catch(e){error(e);}};
$('#search').oninput=render;$('#refresh').onclick=()=>load().catch(error);channel.onmessage=()=>load().catch(error);
async function refreshConnection(){if(loading)return;loading=true;try{await load();}catch(e){error(e);}finally{loading=false;}}
load().then(()=>{
  const authError=new URLSearchParams(location.search).get('auth_error');if(authError)$('#notice').textContent=authError;
  if(parent===window&&session.authenticated)channel.postMessage('refresh');
  if(watching&&!repos.length){let attempts=0;watchTimer=setInterval(()=>{if(++attempts>120){clearInterval(watchTimer);watchTimer=null;return;}refreshConnection();},5000);}
}).catch(error);

}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initPicker,{once:true});else initPicker();
}
