"use strict";
let editingUser=null,userEntries=[],usersBusy=false;
const usersMessage=text=>{$('userMessage').textContent=text;$('userMessage').title=text;};
async function usersApi(path,data){
  const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(10000),...(data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});
  if(!response.ok){const text=await response.text();throw Error(response.status===400?text:'User management unavailable. Open this page locally.');}
  return response.status===204?{}:response.json();
}
function editUser(user=null){
  editingUser=user?.id||null;$('userEditorTitle').textContent=user?'Edit user':'New user';
  $('userName').value=user?.name||'';$('userEmail').value=user?.email||'';$('userLimit').value=String(user?.limit||1);$('userEnabled').checked=user?.enabled??true;
}
async function loadUsers(){
  if(usersBusy)return;usersBusy=true;
  try{
    const data=await usersApi('/api/users');userEntries=data.users;$('userCapacity').textContent=`${data.allocated_connections} of ${data.max_viewers} connections allocated`;$('personalLinksOnly').checked=data.require_links;$('disconnectGeneralUsers').textContent=`Disconnect general viewers (${data.general_connections})`;$('disconnectGeneralUsers').disabled=data.general_connections===0;
    $('userRows').replaceChildren();
    for(const user of userEntries){
      const row=document.createElement('tr');
      row.dataset.search=[user.name,user.email,'User '+user.id,user.enabled?'Enabled':'Disabled',`${user.connections} / ${user.limit}`,user.link].join(' ');
      const identity=document.createElement('td');identity.textContent=user.name;
      const email=document.createElement('small');email.className='viewer-browser';email.textContent=user.email;identity.append(email);const code=document.createElement('small');code.className='viewer-browser';code.textContent='User '+user.id.slice(0,8);identity.append(code);row.append(identity);
      for(const value of [`${user.connections} / ${user.limit}`,user.enabled?'Enabled':'Disabled']){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}
      const linkCell=document.createElement('td'),link=document.createElement('textarea');link.rows=2;link.readOnly=true;link.value=user.link;link.className='user-link';link.setAttribute('aria-label',`Link for ${user.name}`);link.onclick=()=>link.select();linkCell.append(link);row.append(linkCell);
      const actions=document.createElement('td'),buttons=document.createElement('div');buttons.className='user-actions';
      for(const [label,handler] of [['Edit',()=>editUser(user)],['Copy link',async()=>{try{await navigator.clipboard.writeText(user.link);usersMessage(`Link copied for ${user.name}`);}catch{link.focus();link.select();usersMessage('Select and copy the link.');}}],['Replace link',async()=>{
        if(!confirm(`Replace the link for ${user.name}? The old link and its connections will stop working.`))return;
        try{await usersApi('/api/users/replace',{id:user.id});usersMessage(`Link replaced for ${user.name}`);await loadUsers();}catch(e){usersMessage(e.message);}
      }],['Delete',async()=>{
        if(!confirm(`Delete ${user.name} and revoke their link?`))return;
        try{const response=await fetch('/api/users/'+user.id,{method:'DELETE'});if(!response.ok)throw Error('Unable to delete user');if(editingUser===user.id)editUser();usersMessage('User deleted.');await loadUsers();}catch(e){usersMessage(e.message);}
      }]]){const button=document.createElement('button');button.type='button';button.textContent=label;if(label==='Replace link')button.title='Revokes/cancels this link and creates a new one. Existing connections using this link are disconnected.';if(label==='Delete')button.title='Deletes this user and revokes/cancels their link. Existing connections using this link are disconnected.';button.onclick=handler;buttons.append(button);}
      actions.append(buttons);row.append(actions);$('userRows').append(row);
    }
    $('userLinksSummary').textContent=`${userEntries.length} personal user links · ${userEntries.filter(user=>user.enabled).length} enabled · ${data.allocated_connections} allowed connections. Each slot becomes available again after disconnection.`;
    $('exportUserLinks').disabled=userEntries.length===0;$('exportUserMailtexts').disabled=userEntries.length===0;
    filterUsers();
  }catch(e){usersMessage(e.message);}finally{usersBusy=false;}
}
$('newUser').onclick=()=>{editUser();$('userName').focus();};$('cancelUser').onclick=()=>editUser();$('refreshUsers').onclick=loadUsers;
$('userForm').onsubmit=async event=>{
  event.preventDefault();$('saveUser').disabled=true;
  try{const result=await usersApi('/api/users',{id:editingUser,name:$('userName').value,email:$('userEmail').value,limit:Number($('userLimit').value),enabled:$('userEnabled').checked});usersMessage('User saved.');await loadUsers();editUser(userEntries.find(user=>user.id===result.id));}
  catch(e){usersMessage(e.message);}finally{$('saveUser').disabled=false;}
};
$('personalLinksOnly').onchange=async()=>{const box=$('personalLinksOnly');box.disabled=true;try{await usersApi('/api/users/access',{require_links:box.checked});usersMessage(box.checked?'Personal links required for new connections.':'General viewer link enabled.');}catch(e){box.checked=!box.checked;usersMessage(e.message);}finally{box.disabled=false;}};
window.addEventListener('hashchange',()=>{if(location.hash==='#users')loadUsers();});
setInterval(()=>{if(location.hash==='#users'&&!document.hidden)loadUsers();},5000);
if(location.hash==='#users')loadUsers();

function filterUsers(){
  const normalize=value=>value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  const words=normalize($('userSearch').value).trim().split(/\s+/).filter(Boolean);
  let visible=0;
  for(const row of $('userRows').children){const text=normalize(row.dataset.search||'');row.hidden=!words.every(word=>text.includes(word));if(!row.hidden)visible++;}
  $('userSearchCount').textContent=`(${visible}/${userEntries.length} users)`;
}
$('userSearch').oninput=filterUsers;

$('disconnectGeneralUsers').onclick=async()=>{
  const button=$('disconnectGeneralUsers');button.disabled=true;
  try{const result=await usersApi('/api/connections/disconnect-general',{});usersMessage(`${result.disconnected} general viewers disconnected.`);await loadUsers();}
  catch(e){usersMessage(e.message);button.disabled=false;}
};

$('reloadUserLinks').onclick=loadUsers;
$('exportUserLinks').onclick=async()=>{
  await loadUsers();
  if(!userEntries.length)return;
  const content=userEntries.map(user=>`${user.name}\t${user.link}`).join('\r\n')+'\r\n';
  const url=URL.createObjectURL(new Blob([content],{type:'text/plain;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download='FastVideo-user-links.txt';link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  usersMessage('User links exported.');
};
$('exportUserMailtexts').onclick=async()=>{
  const button=$('exportUserMailtexts');button.disabled=true;
  try{
    const response=await fetch('/api/users/export',{cache:'no-store',signal:AbortSignal.timeout(10000)});
    if(!response.ok)throw Error(response.status===400?await response.text():'Unable to export user emails. Open this page locally.');
    const url=URL.createObjectURL(await response.blob());
    const link=document.createElement('a');link.href=url;link.download='FastVideo-user-emails.txt';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    usersMessage('User emails and personal links exported.');
  }catch(e){usersMessage(e.message);}finally{button.disabled=userEntries.length===0;}
};
