'use strict';
// Connection profiles: a disk button per card opens one shared dialog for that connection type.
// Loading only fills the form. A running output keeps its settings until it is restarted,
// and the card says so until then.
const DISK_ICON='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M5 3h11l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v6h9V3"/><rect x="7" y="13" width="10" height="8" rx="1"/></svg>';
const PROFILE_TITLES={whip:'WHIP send · saved settings',srt:'SRT send · saved settings',receive:'Receive · saved settings'};
let profileKind=null,profileRows=[];

function profileFormSettings(kind){
  const settings={};
  for(const [key,suffix] of Object.entries(FORM_FIELDS[kind])){
    const el=$(kind+suffix);if(!el)continue;
    settings[key]=el.type==='checkbox'?el.checked:NUMERIC_FORM_FIELDS.has(key)?(key==='sdi_device'&&el.value===''?null:Number(el.value)):el.value;
  }
  return settings;
}

function applyProfileSettings(kind,settings){
  for(const [key,suffix] of Object.entries(FORM_FIELDS[kind])){
    const el=$(kind+suffix);if(!el||!(key in settings)||settings[key]===null)continue;
    if(el.type==='checkbox')el.checked=!!settings[key];
    else el.value=key==='bitrate'?Math.min(settings[key],MAX_BITRATE):settings[key];
    el.dispatchEvent(new Event('change',{bubbles:true}));
  }
  if(kind==='receive'&&typeof updateReceiveMode==='function')updateReceiveMode();
}

function showProfilePending(kind,name){
  const note=$(kind+'ProfilePending');
  note.textContent=outputEnabled[kind]
    ?`Profile “${name}” loaded – not active yet. Stop and start this output to apply it.`
    :`Profile “${name}” loaded into the form – used at the next start.`;
  note.hidden=false;
}

function renderProfiles(selectedId){
  const list=$('profileList');list.replaceChildren();
  for(const row of profileRows){
    const when=row.updated?new Date(row.updated).toLocaleString([], {dateStyle:'short',timeStyle:'short'}):'';
    list.append(new Option(when?`${row.name}   ·   ${when}`:row.name,row.id));
  }
  list.value=selectedId&&profileRows.some(r=>r.id===selectedId)?selectedId:'';
  const empty=profileRows.length===0;
  for(const id of ['profileLoad','profileOverwrite','profileRename','profileDelete'])$(id).disabled=empty||!list.value;
  if(empty)$('profileNotice').textContent='No saved profiles yet. Enter a name and press Save as new.';
}

async function profileCall(body){
  const data=await api('/api/profiles/'+profileKind,body?'POST':'GET',body);
  profileRows=data.profiles||[];
  return data;
}

async function openProfiles(kind){
  profileKind=kind;$('profileTitle').textContent=PROFILE_TITLES[kind];$('profileNotice').textContent='';$('profileName').value='';
  $('profileHelp').textContent=outputEnabled[kind]
    ?'This output is running. Loading a profile fills the form only; it becomes active after Stop and Start.'
    :'Save the current form as a named profile, or load a profile into the form.';
  $('profileDialog').showModal();
  try{await profileCall();renderProfiles();}catch(e){$('profileNotice').textContent=e.message;}
}

function selectedProfile(){return profileRows.find(r=>r.id===$('profileList').value);}

async function profileAction(work){
  const buttons=document.querySelectorAll('.profile-actions button');
  for(const b of buttons)b.disabled=true;
  try{await work();}catch(e){$('profileNotice').textContent=e.message;}
  finally{for(const b of buttons)b.disabled=false;renderProfiles($('profileList').value);}
}

$('profileList').onchange=()=>{const row=selectedProfile();if(row)$('profileName').value=row.name;$('profileNotice').textContent='';renderProfiles(row?.id);};
$('profileList').ondblclick=()=>$('profileLoad').click();
$('profileClose').onclick=()=>$('profileDialog').close();

$('profileLoad').onclick=()=>profileAction(async()=>{
  const row=selectedProfile();if(!row)throw Error('Choose a saved profile');
  const data=await profileCall({action:'load',id:row.id});
  applyProfileSettings(profileKind,data.settings);
  showProfilePending(profileKind,data.profile.name);
  $('profileDialog').close();
});
$('profileSave').onclick=()=>profileAction(async()=>{
  const name=$('profileName').value.trim();if(!name)throw Error('Enter a name for the new profile');
  const data=await profileCall({action:'save',name,settings:profileFormSettings(profileKind)});
  renderProfiles(data.saved.id);$('profileNotice').textContent=`Saved “${data.saved.name}”.`;
});
$('profileOverwrite').onclick=()=>profileAction(async()=>{
  const row=selectedProfile();if(!row)throw Error('Choose the profile to overwrite');
  if(!confirm(`Overwrite “${row.name}” with the current form?`))return;
  const data=await profileCall({action:'save',id:row.id,name:$('profileName').value.trim()||row.name,settings:profileFormSettings(profileKind)});
  renderProfiles(data.saved.id);$('profileNotice').textContent=`Updated “${data.saved.name}”.`;
});
$('profileRename').onclick=()=>profileAction(async()=>{
  const row=selectedProfile(),name=$('profileName').value.trim();
  if(!row)throw Error('Choose the profile to rename');if(!name||name===row.name)throw Error('Enter the new name');
  const data=await profileCall({action:'rename',id:row.id,name});
  renderProfiles(data.saved.id);$('profileNotice').textContent=`Renamed to “${data.saved.name}”.`;
});
$('profileDelete').onclick=()=>profileAction(async()=>{
  const row=selectedProfile();if(!row)throw Error('Choose the profile to delete');
  if(!confirm(`Delete “${row.name}”? This cannot be undone.`))return;
  await profileCall({action:'delete',id:row.id});
  $('profileName').value='';renderProfiles();$('profileNotice').textContent=`Deleted “${row.name}”.`;
});

for(const button of document.querySelectorAll('[data-profiles]')){
  button.innerHTML=DISK_ICON;
  button.onclick=()=>openProfiles(button.dataset.profiles);
}
// Starting the output applies the form, so the "not active yet" note is no longer true.
for(const kind of ['whip','srt','receive'])$(kind+'Form').addEventListener('submit',()=>{$(kind+'ProfilePending').hidden=true;});
