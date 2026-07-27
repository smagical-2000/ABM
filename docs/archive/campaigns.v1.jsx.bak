const { useState, useEffect, useMemo, useRef } = React;

// ── Campaigns console (Phase 3) ───────────────────────────────────────────────
// The WRITE side of the loop: scored + in-market accounts flow into the right
// Reply.io email sequence. This tab is decision support for a non-technical
// operator: WHO is ready (with the reasons), WHAT was enrolled (the ledger),
// and WHICH Reply.io campaign each group maps to. Reads /api/campaigns/board;
// posts /run, /enroll, /mapping, /settings. Mirrors the engagement console's
// visual language (same primitives, same palette, no new design system).

const SegmentBadge = window.SegmentBadge;
const _ICON_ALIAS = { alert: 'info', help: 'info', doc: 'ext' };
function Icon({ name, size = 14, style, className }) {
  const C = window.Icons[name] || window.Icons[_ICON_ALIAS[name]] || window.Icons.info;
  return <C width={size} height={size} style={style} className={className} />;
}

const _CSS = `
:root{--font-sans:'Inter',system-ui,-apple-system,sans-serif;}
@keyframes _cspin{to{transform:rotate(360deg)}}
.cspin{animation:_cspin .8s linear infinite;transform-origin:center}
@keyframes _cpulse{0%,100%{opacity:1}50%{opacity:.3}}
.cpulse{animation:_cpulse 1.1s ease-in-out infinite}
@keyframes _cfade{from{opacity:0}to{opacity:1}}
.cfade{animation:_cfade .15s ease}
@keyframes _cpop{from{transform:scale(.97);opacity:0}to{transform:none;opacity:1}}
.cpop{animation:_cpop .16s cubic-bezier(.16,1,.3,1)}
`;

const TX = {
  strong:{fontSize:14,fontWeight:500,color:'#18181b',lineHeight:1.4},
  body:{fontSize:13,fontWeight:400,color:'#52525b',lineHeight:1.4},
  meta:{fontSize:12,fontWeight:400,color:'#a1a1aa',lineHeight:1.3},
  label:{fontSize:11,fontWeight:600,color:'#a1a1aa',textTransform:'uppercase',letterSpacing:'.06em'},
};
const HEAT = {
  Hot:   { fg:'#b45309', bg:'#fffbeb', ring:'#fde68a' },
  Warm:  { fg:'#047857', bg:'#ecfdf5', ring:'#a7f3d0' },
  Some:  { fg:'#0369a1', bg:'#f0f9ff', ring:'#bae6fd' },
  Lower: { fg:'#71717a', bg:'#fafafa', ring:'#e4e4e7' },
};
const FIT = { high:{label:'High fit',fg:'#4338ca',bg:'#eef2ff',ring:'#c7d2fe'},
              medium:{label:'Medium fit',fg:'#0f766e',bg:'#f0fdfa',ring:'#99f6e4'} };

function relTime(iso){ if(!iso)return'—'; const d=Math.max(0,Date.now()-new Date(iso).getTime()),m=Math.round(d/60000); if(m<2)return'just now'; if(m<60)return`${m}m ago`; const h=Math.round(m/60); if(h<24)return`${h}h ago`; const dy=Math.round(h/24); return dy<30?`${dy}d ago`:new Date(iso).toLocaleDateString('en-US',{month:'short',day:'numeric'}); }

function Chip({ fg, bg, ring, children, title }){
  return <span title={title} style={{display:'inline-flex',alignItems:'center',gap:4,borderRadius:6,padding:'1px 7px',fontSize:11,fontWeight:500,color:fg,background:bg,boxShadow:`inset 0 0 0 1px ${ring}`,whiteSpace:'nowrap'}}>{children}</span>;
}

function Toggle({ on, onFlip, activeColor='#10b981' }){
  return (
    <span onClick={onFlip} style={{position:'relative',width:34,height:20,borderRadius:999,background:on?activeColor:'#e4e4e7',cursor:'pointer',flexShrink:0,transition:'background .15s',display:'inline-block'}}>
      <span style={{position:'absolute',top:2,left:on?16:2,width:16,height:16,borderRadius:'50%',background:'#fff',boxShadow:'0 1px 2px rgba(0,0,0,.2)',transition:'left .15s'}}/>
    </span>
  );
}

// ── READY (the worklist: who qualifies right now, and why) ────────────────────
const RCOLS='minmax(0,1fr) 210px 190px 120px 96px';
function ReadyRow({ e, live, onEnroll }){
  const [hov,setHov]=useState(false);
  const hc=HEAT[e.heat_tier]||HEAT.Lower;
  const fit=FIT[e.fit_band]||FIT.medium;
  return (
    <div onMouseEnter={()=>setHov(true)} onMouseLeave={()=>setHov(false)}
      style={{display:'grid',gridTemplateColumns:RCOLS,alignItems:'center',gap:20,padding:'13px 28px',borderBottom:'1px solid #f4f4f5',background:hov?'rgba(244,244,245,.55)':'#fff',transition:'background .1s ease'}}>
      <div style={{minWidth:0}}>
        <div style={{display:'flex',alignItems:'center',gap:8}}>
          <span style={{...TX.strong,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{e.name}</span>
          {e.segment&&SegmentBadge&&<SegmentBadge segment={e.segment==='health_system'?'health_system':(e.segment.includes('payer')?'payer':'specialty')}/>}
        </div>
        <div style={{display:'flex',alignItems:'center',gap:6,marginTop:5,flexWrap:'wrap'}}>
          <Chip {...fit}>{e.fit_label||fit.label}</Chip>
          {e.heat_score>0&&<Chip fg={hc.fg} bg={hc.bg} ring={hc.ring}>{e.heat_tier} · {e.heat_score} pts</Chip>}
          {e.intent_tier==='hot'&&<Chip fg='#be123c' bg='#fff1f2' ring='#fecdd3'>Hot intent</Chip>}
        </div>
      </div>
      <div style={{minWidth:0}}>
        <div style={{...TX.body,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{e.route||e.sequence_label}{e.rule_id&&<span style={{marginLeft:6,fontSize:10,fontWeight:600,color:'#6d28d9',background:'#f5f3ff',borderRadius:5,padding:'1px 5px'}}>RULE</span>}</div>
        {e.mapped
          ? <div style={{...TX.meta,marginTop:2,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{e.campaign_name||('campaign '+(e.campaign_id||''))}</div>
          : <div style={{fontSize:11.5,color:'#b45309',marginTop:2,fontStyle:'italic'}}>sequence not set up yet</div>}
      </div>
      <div style={{...TX.meta,minWidth:0,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}} title={(e.reasons||[]).join(' · ')}>{(e.reasons||[]).join(' · ')}</div>
      <div style={{...TX.body,fontVariantNumeric:'tabular-nums'}}>
        {e.contacts_ready} contact{e.contacts_ready===1?'':'s'}
      </div>
      <div style={{display:'flex',justifyContent:'flex-end'}}>
        <button onClick={()=>onEnroll(e)} disabled={!e.mapped||!e.contacts_ready}
          title={!e.mapped?'Assign a Reply.io campaign in Sequences first':!e.contacts_ready?'No sendable contacts in Reply.io for this account':(live?'Enroll these contacts into the sequence now':'Testing mode: shows what would be enrolled, sends nothing')}
          style={{background:(!e.mapped||!e.contacts_ready)?'#fafafa':(live?'#fffbeb':'#fff'),border:`1px solid ${(!e.mapped||!e.contacts_ready)?'#f4f4f5':(live?'#fde68a':'#e4e4e7')}`,fontFamily:'var(--font-sans)',fontSize:12,fontWeight:500,color:(!e.mapped||!e.contacts_ready)?'#d4d4d8':(live?'#b45309':'#3f3f46'),cursor:(!e.mapped||!e.contacts_ready)?'default':'pointer',padding:'5px 12px',borderRadius:6}}>
          {live?'Enroll':'Preview'}
        </button>
      </div>
    </div>
  );
}

function ReadyView({ eligible, live, onEnroll }){
  return (
    <>
      <div style={{display:'grid',gridTemplateColumns:RCOLS,gap:20,padding:'8px 28px',borderBottom:'1px solid #f4f4f5',background:'#fafafa'}}>
        <div style={TX.label}>Account</div><div style={TX.label}>Sequence</div>
        <div style={TX.label}>Why it qualifies</div><div style={TX.label}>Will enroll</div><div/>
      </div>
      {eligible.length===0
        ? <div style={{padding:'46px 28px',textAlign:'center'}}>
            <div style={{...TX.body,fontWeight:500}}>No accounts are ready to enroll right now.</div>
            <div style={{...TX.meta,marginTop:6,maxWidth:520,margin:'6px auto 0'}}>An account becomes ready when it is scored a High or Medium fit AND is showing buying behavior (Warm or Hot engagement, or Hot buying intent). Accounts already enrolled do not reappear.</div>
          </div>
        : eligible.map(e=><ReadyRow key={e.account_id} e={e} live={live} onEnroll={onEnroll}/>)}
    </>
  );
}

// ── ENROLLED (the ledger, grouped per account) ────────────────────────────────
function groupLedger(rows){
  const m=new Map();
  rows.forEach(r=>{
    const key=r.account_id+'|'+r.campaign_id;
    const g=m.get(key)||{account_id:r.account_id,name:r.account_name||r.account_id,
      sequence_key:r.sequence_key,campaign_id:r.campaign_id,enrolled:0,already:0,failed:0,
      trigger:r.trigger,ts:''};
    if(r.status==='enrolled')g.enrolled+=1; else if(r.status==='skipped_409')g.already+=1; else g.failed+=1;
    if((r.enrolled_at||'')>g.ts){g.ts=r.enrolled_at;g.trigger=r.trigger;}
    m.set(key,g);
  });
  return [...m.values()].sort((a,b)=>(b.ts||'').localeCompare(a.ts||''));
}

const LCOLS='minmax(0,1fr) 170px 220px 110px 96px';
function LedgerView({ enrollments, sequences }){
  const groups=useMemo(()=>groupLedger(enrollments),[enrollments]);
  const seqLabel=(key)=>{ const s=sequences.find(x=>x.sequence_key===key); return s?s.label:key; };
  return (
    <>
      <div style={{display:'grid',gridTemplateColumns:LCOLS,gap:20,padding:'8px 28px',borderBottom:'1px solid #f4f4f5',background:'#fafafa'}}>
        <div style={TX.label}>Account</div><div style={TX.label}>Sequence</div>
        <div style={TX.label}>Contacts</div><div style={TX.label}>How</div>
        <div style={{...TX.label,textAlign:'right'}}>When</div>
      </div>
      {groups.length===0
        ? <div style={{padding:'46px 28px',textAlign:'center'}}>
            <div style={{...TX.body,fontWeight:500}}>Nothing has been enrolled yet.</div>
            <div style={{...TX.meta,marginTop:6}}>Every contact pushed into a Reply.io sequence is recorded here, so there is always a paper trail.</div>
          </div>
        : groups.map((g,i)=>(
          <div key={i} style={{display:'grid',gridTemplateColumns:LCOLS,alignItems:'center',gap:20,padding:'12px 28px',borderBottom:'1px solid #f4f4f5'}}>
            <div style={{...TX.strong,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{g.name}</div>
            <div style={{...TX.body,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{seqLabel(g.sequence_key)}</div>
            <div style={{display:'flex',gap:6,flexWrap:'wrap'}}>
              {g.enrolled>0&&<Chip fg='#047857' bg='#ecfdf5' ring='#a7f3d0'>{g.enrolled} enrolled</Chip>}
              {g.already>0&&<Chip fg='#71717a' bg='#fafafa' ring='#e4e4e7' title='Reply.io reported these contacts were already in a sequence — they were left as-is'>{g.already} already in a sequence</Chip>}
              {g.failed>0&&<Chip fg='#be123c' bg='#fff1f2' ring='#fecdd3'>{g.failed} failed</Chip>}
            </div>
            <div style={TX.meta}>{g.trigger==='manual'?'Manual':'Automatic'}</div>
            <div style={{...TX.meta,textAlign:'right'}}>{relTime(g.ts)}</div>
          </div>
        ))}
    </>
  );
}

// ── ROUTING (rules: who goes to which campaign) ──────────────────────────────
// One mental model, made visual: every row is a RULE. The 7 group rules are
// fixed; custom rules (Hot, Warm+, ...) sit on top and win first. Pickers are
// real dropdowns with the suggested campaign grouped first; saving is automatic.
function Picker({ value, options, suggestKey, placeholder, accent='#4f46e5', onPick, width=270 }){
  const suggested=(options||[]).filter(c=>!c.mapped&&suggestKey&&c.suggested_key===suggestKey);
  const rest=(options||[]).filter(c=>!suggested.includes(c)).slice().sort((a,b)=>String(a.name||'').localeCompare(String(b.name||'')));
  const cur=(options||[]).find(c=>String(c.id)===String(value));
  return (
    <span style={{position:'relative',display:'inline-flex',alignItems:'center',width}}>
      <select value={value||''} onChange={ev=>{const c=(options||[]).find(x=>String(x.id)===String(ev.target.value)); onPick(ev.target.value||null, c?c.name:null);}}
        style={{appearance:'none',width:'100%',borderRadius:8,border:`1.5px solid ${value?'#e4e4e7':'#fde68a'}`,background:value?'#fff':'#fffbeb',padding:'7px 30px 7px 11px',fontFamily:'var(--font-sans)',fontSize:12.5,fontWeight:value?500:400,color:value?'#18181b':'#92400e',cursor:'pointer'}}>
        <option value="">{placeholder}</option>
        {suggested.length>0&&<optgroup label="Suggested for this group">
          {suggested.map(c=><option key={c.id} value={String(c.id)}>{c.name}</option>)}
        </optgroup>}
        <optgroup label={suggested.length?'All campaigns':'Campaigns'}>
          {rest.map(c=><option key={c.id} value={String(c.id)}>{c.name}</option>)}
          {value&&!cur&&<option value={String(value)}>campaign {value}</option>}
        </optgroup>
      </select>
      <span style={{position:'absolute',right:9,pointerEvents:'none',color:value?'#a1a1aa':'#b45309'}}><Icon name="chevron" size={13}/></span>
    </span>
  );
}

const HEAT_MIN=[['any','Any activity'],['some','Some+'],['warm','Warm+'],['hot','Hot only']];
function RuleCard({ r, sequences, replyio, heyreach, onChange, onDelete }){
  const set=(k,v)=>onChange({...r,[k]:v});
  return (
    <div style={{display:'flex',alignItems:'center',gap:14,padding:'12px 24px',background:'#fbfaff',borderBottom:'1px solid #eef2ff'}}>
      <span style={{width:8,height:8,borderRadius:'50%',background:r.enabled?'#8b5cf6':'#d4d4d8',flexShrink:0}}/>
      <div style={{flex:1,minWidth:0}}>
        <input value={r.name} onChange={ev=>set('name',ev.target.value)} placeholder="Rule name"
          style={{border:'none',background:'none',fontFamily:'var(--font-sans)',fontSize:13.5,fontWeight:600,color:'#18181b',outline:'none',width:'100%'}}/>
        <div style={{display:'flex',alignItems:'center',gap:6,marginTop:5,flexWrap:'wrap',fontSize:12,color:'#52525b'}}>
          <span>If heat is</span>
          <select value={r.heat_min} onChange={ev=>set('heat_min',ev.target.value)} style={{borderRadius:6,border:'1px solid #ddd6fe',background:'#fff',padding:'2px 6px',fontSize:11.5,fontWeight:600,color:'#6d28d9'}}>
            {HEAT_MIN.map(([v,l])=><option key={v} value={v}>{l}</option>)}
          </select>
          <span>and fit is</span>
          <select value={r.fit} onChange={ev=>set('fit',ev.target.value)} style={{borderRadius:6,border:'1px solid #ddd6fe',background:'#fff',padding:'2px 6px',fontSize:11.5,fontWeight:600,color:'#6d28d9'}}>
            <option value="high_med">High or Medium</option><option value="high">High only</option>
          </select>
          <span>in</span>
          <select value={(r.groups&&r.groups[0])||''} onChange={ev=>set('groups',ev.target.value?[ev.target.value]:[])} style={{borderRadius:6,border:'1px solid #ddd6fe',background:'#fff',padding:'2px 6px',fontSize:11.5,fontWeight:600,color:'#6d28d9',maxWidth:170}}>
            <option value="">any group</option>
            {sequences.map(s=><option key={s.sequence_key} value={s.sequence_key}>{s.label}</option>)}
          </select>
        </div>
      </div>
      <Picker value={r.campaign_id} options={replyio} placeholder="Pick email campaign" width={240}
        onPick={(id,name)=>onChange({...r,campaign_id:id,campaign_name:name})}/>
      <Picker value={r.li_campaign_id} options={(heyreach&&heyreach.campaigns)||[]} placeholder="LinkedIn (optional)" width={190}
        onPick={(id,name)=>onChange({...r,li_campaign_id:id,li_campaign_name:name})}/>
      <Toggle on={r.enabled} onFlip={()=>set('enabled',!r.enabled)} activeColor='#8b5cf6'/>
      <button onClick={onDelete} title="Delete rule" style={{border:'none',background:'none',color:'#a1a1aa',cursor:'pointer',padding:4}}><Icon name="trash" size={14}/></button>
    </div>
  );
}

function SequencesView({ sequences, replyio, replyioErr, onSave, saving, onRefresh, refreshing, heyreach, channelMap, onSaveChannel, customRules, onSaveRules }){
  const liByKey=useMemo(()=>{ const m={}; (channelMap||[]).forEach(r=>{ if(r.channel==='linkedin') m[r.sequence_key]=r; }); return m; },[channelMap]);
  const liSenders=(heyreach&&heyreach.senders)||[];
  const [rules,setRules]=useState(customRules||[]);
  useEffect(()=>{ setRules(customRules||[]); },[customRules]);
  const dirty=JSON.stringify(rules)!==JSON.stringify(customRules||[]);
  const addRule=()=>setRules(rs=>[...rs,{id:'r'+Date.now().toString(36),name:'Hot accounts fast-track',enabled:true,heat_min:'hot',fit:'high_med',groups:[],campaign_id:null,campaign_name:null,li_campaign_id:null,li_campaign_name:null}]);
  return (
    <>
      {/* one calm explainer + the refresh action */}
      <div style={{display:'flex',alignItems:'center',gap:10,padding:'11px 24px',background:'rgba(238,242,255,.45)',borderBottom:'1px solid #e0e7ff'}}>
        <Icon name="info" size={13} style={{color:'#4f46e5',flexShrink:0}}/>
        <span style={{fontSize:12.5,color:'#3730a3',flex:1}}>
          Emails live in Reply.io — here you only decide <b>who goes where</b>. Custom rules run first; first match wins, the group rule is the fallback.
          {liSenders.length===0&&heyreach&&<span style={{color:'#92400e'}}> LinkedIn is dormant until an account is connected in HeyReach.</span>}
        </span>
        <button onClick={onRefresh} disabled={refreshing}
          style={{display:'inline-flex',alignItems:'center',gap:6,flexShrink:0,borderRadius:7,background:'#fff',border:'1px solid #c7d2fe',padding:'5px 11px',fontFamily:'var(--font-sans)',fontSize:12,fontWeight:600,color:'#4338ca',cursor:refreshing?'default':'pointer',opacity:refreshing?.6:1}}>
          <Icon name="refresh" size={13} className={refreshing?'cspin':''}/>{refreshing?'Checking…':'Check for new campaigns'}
        </button>
      </div>
      {replyioErr&&<div style={{padding:'9px 24px',borderBottom:'1px solid #fef3c7',background:'rgba(255,251,235,.6)',fontSize:12.5,color:'#92400e'}}>Reply.io list unavailable: {replyioErr}</div>}

      {/* custom rules — on top because they win first */}
      <div style={{display:'flex',alignItems:'center',gap:8,padding:'12px 24px 8px'}}>
        <span style={{...TX.label,color:'#6d28d9'}}>Custom rules · run first</span>
        <button onClick={addRule} style={{display:'inline-flex',alignItems:'center',gap:5,marginLeft:'auto',borderRadius:7,background:'#f5f3ff',border:'1px solid #ddd6fe',padding:'4px 10px',fontFamily:'var(--font-sans)',fontSize:12,fontWeight:600,color:'#6d28d9',cursor:'pointer'}}>+ Add rule</button>
        {dirty&&<button onClick={()=>onSaveRules(rules)} disabled={saving} style={{borderRadius:7,background:'#6d28d9',border:'none',padding:'4px 12px',fontFamily:'var(--font-sans)',fontSize:12,fontWeight:600,color:'#fff',cursor:'pointer'}}>Save rules</button>}
      </div>
      {rules.length===0
        ? <div style={{padding:'4px 24px 14px',...TX.meta}}>None yet. Example: "Hot accounts, any group → the fast-track campaign."</div>
        : rules.map((r,i)=><RuleCard key={r.id||i} r={r} sequences={sequences} replyio={replyio} heyreach={heyreach}
            onChange={nr=>setRules(rs=>rs.map((x,j)=>j===i?nr:x))} onDelete={()=>setRules(rs=>rs.filter((_,j)=>j!==i))}/>)}

      {/* the 7 fixed group rules */}
      <div style={{padding:'12px 24px 8px',borderTop:'1px solid #f4f4f5',...TX.label}}>Account groups · the fallback route</div>
      <div style={{display:'grid',gridTemplateColumns:'minmax(0,1fr) 270px 190px',gap:14,padding:'2px 24px 6px'}}>
        <span/><span style={{...TX.label,fontSize:10}}>Email · Reply.io</span><span style={{...TX.label,fontSize:10}}>LinkedIn · HeyReach</span>
      </div>
      {sequences.map(sq=>{
        const li=liByKey[sq.sequence_key]||{};
        const ready=!!sq.campaign_id;
        return (
          <div key={sq.sequence_key} style={{display:'grid',gridTemplateColumns:'minmax(0,1fr) 270px 190px',alignItems:'center',gap:14,padding:'11px 24px',borderBottom:'1px solid #f4f4f5'}}>
            <div style={{minWidth:0,display:'flex',alignItems:'center',gap:9}}>
              <span title={ready?'Routing':'Needs a campaign'} style={{width:8,height:8,borderRadius:'50%',background:ready?'#10b981':'#f59e0b',flexShrink:0}}/>
              <div style={{minWidth:0}}>
                <div style={{...TX.strong,fontSize:13.5}}>{sq.label}</div>
                <div style={{...TX.meta,marginTop:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{sq.hint}</div>
              </div>
            </div>
            <Picker value={sq.campaign_id} options={replyio} suggestKey={sq.sequence_key} placeholder="Pick email campaign"
              onPick={(id,name)=>onSave(sq.sequence_key,id,name)}/>
            <Picker value={li.campaign_id} options={(heyreach&&heyreach.campaigns)||[]} suggestKey={sq.sequence_key}
              placeholder={((heyreach&&heyreach.campaigns)||[]).length?'Pick LinkedIn campaign':'None yet'} width={190}
              onPick={(id,name)=>onSaveChannel(sq.sequence_key,'linkedin',id,name)}/>
          </div>
        );
      })}
    </>
  );
}

// ── MAIN ─────────────────────────────────────────────────────────────────────
function CampaignsView({ pushToast }){
  const [board,setBoard]=useState(null);
  const [loading,setLoading]=useState(true);
  const [tab,setTab]=useState('ready');
  const [replyio,setReplyio]=useState(null);      // Reply.io campaign list (lazy)
  const [replyioErr,setReplyioErr]=useState(null);
  const [saving,setSaving]=useState(false);
  const [settingsOpen,setSettingsOpen]=useState(false);
  const [runBusy,setRunBusy]=useState(false);

  function load(){ return window.API.campaignsBoard().then(b=>{ setBoard(b); setLoading(false); }); }
  useEffect(()=>{ load().catch(e=>{ setLoading(false); pushToast&&pushToast(`Couldn't load campaigns: ${e.message}`,'danger'); }); },[]);

  // lazy-load the Reply.io + HeyReach campaign lists the first time Sequences opens
  const [heyreach,setHeyreach]=useState(null);
  useEffect(()=>{
    if(tab!=='sequences') return;
    if(replyio===null)
      window.API.campaignsReplyio().then(r=>setReplyio(r.campaigns||[]))
        .catch(e=>{ setReplyio([]); setReplyioErr(e.message); });
    if(heyreach===null)
      window.API.campaignsHeyreach().then(r=>setHeyreach(r))
        .catch(()=>setHeyreach({campaigns:[],senders:[]}));
  },[tab]);  // eslint-disable-line

  // on-demand re-check, so a sequence Galyna created a minute ago shows up
  const [refreshing,setRefreshing]=useState(false);
  function refreshReplyio(){
    if(refreshing) return;
    setRefreshing(true); setReplyioErr(null);
    window.API.campaignsReplyio().then(r=>{
      setReplyio(r.campaigns||[]); setRefreshing(false);
      const fresh=(r.campaigns||[]).filter(c=>!c.mapped).length;
      pushToast&&pushToast(`Reply.io checked — ${fresh} campaign${fresh===1?'':'s'} not connected yet`,'muted');
    }).catch(e=>{ setRefreshing(false); setReplyioErr(e.message); });
  }

  // poll while a live run is in flight (same pattern as the engagement sync pill)
  const pollRef=useRef(false);
  function pollRun(){
    if(pollRef.current) return; pollRef.current=true;
    let tries=0;
    const tick=()=>{
      window.API.campaignsBoard().then(b=>{
        setBoard(b);
        if(b.running&&tries++<40){ setTimeout(tick,6000); }
        else{ pollRef.current=false;
          const lr=b.last_run||{}; const st=lr.stats||{};
          pushToast&&pushToast(`Enrollment run complete — ${st.accounts_enrolled||0} account(s), ${st.contacts_enrolled||0} contact(s) enrolled`,'success'); }
      }).catch(()=>{ pollRef.current=false; });
    };
    setTimeout(tick,4000);
  }
  useEffect(()=>{ if(board&&board.running) pollRun(); },[board&&board.running]);  // eslint-disable-line

  const settings=(board&&board.settings)||{auto_enroll:false,live:false,run_cap:10};
  const sequences=(board&&board.sequences)||[];
  const eligible=(board&&board.eligible)||[];
  const enrollments=(board&&board.enrollments)||[];
  const unmapped=sequences.filter(s=>!s.campaign_id).length;
  // only warn about unmapped sequences that eligible accounts actually need
  const neededUnmapped=useMemo(()=>{
    const need=new Set(eligible.filter(e=>!e.mapped).map(e=>e.sequence_key));
    return need.size;
  },[eligible]);

  function saveSettings(patch){
    window.API.campaignsSettings(patch).then(r=>{ setBoard(b=>({...b,settings:r.settings})); })
      .catch(e=>pushToast&&pushToast(`Couldn't save settings: ${e.message}`,'danger'));
  }
  function toggleLive(){
    const next=!settings.live;
    if(next&&!window.confirm('Go LIVE?\n\nEnrollment will actually push contacts into Reply.io sequences (emails will start sending on Reply.io’s schedule). Turning this on sends nothing by itself.\n\nContinue?')) return;
    saveSettings({live:next});
    pushToast&&pushToast(next?'Live mode ON — enrollments now really push to Reply.io':'Testing mode — everything previews, nothing is sent','muted');
  }
  function toggleAuto(){
    const next=!settings.auto_enroll;
    if(next&&!window.confirm('Turn auto-enroll ON?\n\nAfter every engagement sync, accounts that qualify (High/Medium fit AND Warm/Hot engagement or Hot intent) are enrolled automatically, up to the per-run cap.'+(settings.live?'\n\nLive mode is ON, so this will really send.':'\n\nTesting mode is on, so it will only record what WOULD be sent.'))) return;
    saveSettings({auto_enroll:next});
    pushToast&&pushToast(next?'Auto-enroll ON — qualifying accounts flow in after each sync':'Auto-enroll OFF — enrollment is manual only','muted');
  }
  function editCap(){
    const v=window.prompt('How many accounts may one run enroll? (keeps each run a drip, not a dump)',String(settings.run_cap));
    if(v===null) return;
    const n=parseInt(v,10);
    if(!n||n<1){ pushToast&&pushToast('Cap must be a number of 1 or more','danger'); return; }
    saveSettings({run_cap:n});
  }
  function runNow(){
    if(settings.live){
      if(!window.confirm(`Run enrollment LIVE now?\n\nUp to ${settings.run_cap} qualifying account(s) will have their contacts pushed into Reply.io sequences.`)) return;
      setRunBusy(true);
      window.API.campaignsRun({dry_run:false}).then(r=>{
        setRunBusy(false);
        if(r.busy){ pushToast&&pushToast('A run is already in progress','muted'); return; }
        setBoard(b=>({...b,running:true})); pushToast&&pushToast('Live enrollment started…','muted'); pollRun();
      }).catch(e=>{ setRunBusy(false); pushToast&&pushToast(`Run failed: ${e.message}`,'danger'); });
    } else {
      setRunBusy(true);
      window.API.campaignsRun({}).then(r=>{
        setRunBusy(false);
        const st=(r.result&&r.result.stats)||{};
        pushToast&&pushToast(`Preview: ${st.would_enroll_accounts||0} account(s), ${st.would_enroll_contacts||0} contact(s) would be enrolled${st.unmapped_sequence?` — ${st.unmapped_sequence} blocked on sequence setup`:''}. Nothing was sent.`,'success');
      }).catch(e=>{ setRunBusy(false); pushToast&&pushToast(`Preview failed: ${e.message}`,'danger'); });
    }
  }
  function enrollOne(e){
    if(settings.live){
      if(!window.confirm(`Enroll ${e.name} now?\n\n${e.contacts_ready} contact(s) will be pushed into "${e.campaign_name||e.sequence_label}" and Reply.io will start the sequence.`)) return;
      window.API.campaignsEnroll({account_id:e.account_id,dry_run:false}).then(r=>{
        const x=r.result||{};
        pushToast&&pushToast(`${e.name}: ${x.enrolled||0} enrolled${x.skipped_409?`, ${x.skipped_409} already in a sequence`:''}${x.failed?`, ${x.failed} failed`:''}`,'success');
        load();
      }).catch(err=>pushToast&&pushToast(`Enroll failed: ${err.message}`,'danger'));
    } else {
      window.API.campaignsEnroll({account_id:e.account_id}).then(r=>{
        const x=r.result||{};
        pushToast&&pushToast(`Testing preview — ${e.name}: ${x.planned||0} contact(s) would be enrolled into ${x.campaign_name||e.sequence_label}. Nothing was sent.`,'muted');
      }).catch(err=>pushToast&&pushToast(`Preview failed: ${err.message}`,'danger'));
    }
  }
  function saveMapping(key, campaign_id, campaign_name){
    setSaving(true);
    window.API.campaignsMapping({sequence_key:key,campaign_id,campaign_name}).then(()=>{
      setSaving(false);
      pushToast&&pushToast(campaign_id?'Sequence connected — accounts in this group can now enroll':'Sequence cleared','success');
      load();
    }).catch(e=>{ setSaving(false); pushToast&&pushToast(`Couldn't save: ${e.message}`,'danger'); });
  }
  function saveRules(rules){
    setSaving(true);
    window.API.campaignsRules({rules}).then(r=>{ setSaving(false);
      setBoard(b=>({...b,custom_rules:r.custom_rules})); load();
      pushToast&&pushToast('Routing rules saved — they apply to the next run','success');
    }).catch(e=>{ setSaving(false); pushToast&&pushToast(`Couldn't save rules: ${e.message}`,'danger'); });
  }
  function saveChannelMapping(key, channel, campaign_id, campaign_name){
    setSaving(true);
    window.API.campaignsChannelMapping({sequence_key:key,channel,campaign_id,campaign_name}).then(()=>{
      setSaving(false);
      pushToast&&pushToast(campaign_id?`${channel==='linkedin'?'LinkedIn':'SMS'} channel connected for this group`:'Channel mapping cleared','success');
      load();
    }).catch(e=>{ setSaving(false); pushToast&&pushToast(`Couldn't save: ${e.message}`,'danger'); });
  }

  const lr=board&&board.last_run;
  const lrStats=(lr&&lr.stats)||{};
  const running=!!(board&&board.running);

  function Tab({ id, label, count }){ const act=tab===id; return <button onClick={()=>setTab(id)} style={{position:'relative',display:'inline-flex',alignItems:'center',gap:6,padding:'10px 14px',border:'none',background:'none',cursor:'pointer',fontFamily:'var(--font-sans)',fontSize:13,fontWeight:act?500:400,color:act?'#18181b':'#a1a1aa',marginBottom:-1}}>{label}<span style={{fontSize:11,fontVariantNumeric:'tabular-nums',borderRadius:999,padding:'1px 6px',background:act?'#18181b':'#f4f4f5',color:act?'#fff':'#a1a1aa'}}>{count}</span>{act&&<span style={{position:'absolute',insetInline:0,bottom:-1,height:2,borderRadius:999,background:'#18181b'}}/>}</button>; }

  return (
    <div style={{color:'#18181b'}}>
      <style>{_CSS}</style>
      <main style={{maxWidth:1120,margin:'0 auto',padding:'26px 32px'}}>
        <div style={{display:'flex',alignItems:'flex-end',justifyContent:'space-between',gap:16,marginBottom:22}}>
          <div>
            <h1 style={{margin:0,fontSize:24,fontWeight:600,letterSpacing:'-.02em',color:'#18181b'}}>Campaigns</h1>
            <p style={{margin:'5px 0 0',fontSize:14,color:'#71717a',maxWidth:660}}>Scored, in-market accounts are enrolled into the right Reply.io email sequence — automatically. Reply.io does the sending; this decides who and when.</p>
          </div>
          <div style={{display:'flex',alignItems:'center',gap:10}}>
            {running&&<span style={{display:'inline-flex',alignItems:'center',gap:6,fontSize:12.5,fontWeight:600,color:'#b45309',background:'#fffbeb',border:'1px solid #fde68a',borderRadius:999,padding:'5px 11px'}}>
              <span className="cpulse" style={{width:7,height:7,borderRadius:'50%',background:'#f59e0b',display:'inline-block'}}/>Enrolling…
            </span>}
            {/* settings gear — live mode, auto-enroll, cap. Red dot = LIVE. */}
            <div style={{position:'relative'}}>
              <button onClick={()=>setSettingsOpen(v=>!v)} title="Settings: live mode, auto-enroll, per-run cap"
                style={{display:'inline-flex',alignItems:'center',gap:7,borderRadius:8,background:settingsOpen?'#f4f4f5':'#fff',border:'1px solid #e4e4e7',padding:'8px 11px',fontFamily:'var(--font-sans)',fontSize:13,fontWeight:600,color:'#3f3f46',cursor:'pointer'}}>
                <span style={{fontSize:14,lineHeight:1}}>⚙</span>
                <span title={settings.live?'Live':'Testing'} style={{display:'inline-block',width:7,height:7,borderRadius:'50%',background:settings.live?'#ef4444':'#d4d4d8'}}/>
              </button>
              {settingsOpen&&<>
                <div onClick={()=>setSettingsOpen(false)} style={{position:'fixed',inset:0,zIndex:40}}/>
                <div className="cpop" style={{position:'absolute',right:0,top:'calc(100% + 8px)',zIndex:41,width:300,background:'#fff',border:'1px solid #e4e4e7',borderRadius:12,boxShadow:'0 10px 30px rgba(24,24,27,.13)',padding:6}}>
                  <div style={{display:'flex',alignItems:'center',gap:10,padding:'9px 10px',borderRadius:8}}>
                    <div style={{flex:1}}>
                      <div style={{fontSize:13,fontWeight:600,color:settings.live?'#b91c1c':'#18181b'}}>{settings.live?'Live — really enrolls':'Testing only'}</div>
                      <div style={{fontSize:11.5,color:'#a1a1aa',marginTop:2,lineHeight:1.35}}>{settings.live?'Enrollment pushes contacts into Reply.io':'Everything previews; nothing reaches Reply.io'}</div>
                    </div>
                    <Toggle on={settings.live} onFlip={toggleLive} activeColor='#ef4444'/>
                  </div>
                  <div style={{display:'flex',alignItems:'center',gap:10,padding:'9px 10px',borderRadius:8}}>
                    <div style={{flex:1}}>
                      <div style={{fontSize:13,fontWeight:600,color:'#18181b'}}>Auto-enroll</div>
                      <div style={{fontSize:11.5,color:'#a1a1aa',marginTop:2,lineHeight:1.35}}>After each engagement sync, qualifying accounts enroll on their own</div>
                    </div>
                    <Toggle on={settings.auto_enroll} onFlip={toggleAuto}/>
                  </div>
                  <div onClick={editCap} style={{display:'flex',alignItems:'center',gap:10,padding:'9px 10px',borderRadius:8,cursor:'pointer'}}>
                    <div style={{flex:1}}>
                      <div style={{fontSize:13,fontWeight:600,color:'#18181b'}}>Per-run cap</div>
                      <div style={{fontSize:11.5,color:'#a1a1aa',marginTop:2}}>At most {settings.run_cap} account{settings.run_cap===1?'':'s'} per run — a drip, not a dump</div>
                    </div>
                    <span style={{fontSize:12,fontWeight:600,color:'#6366f1'}}>Edit</span>
                  </div>
                </div>
              </>}
            </div>
            <button onClick={runNow} disabled={running||runBusy}
              title={settings.live?'Enroll every qualifying account now (up to the cap)':'See exactly who would be enrolled — sends nothing'}
              style={{display:'inline-flex',alignItems:'center',gap:7,borderRadius:8,background:'#4f46e5',border:'none',padding:'8px 14px',fontFamily:'var(--font-sans)',fontSize:13,fontWeight:600,color:'#fff',cursor:'pointer',boxShadow:'0 1px 2px rgba(24,24,27,.05)',opacity:(running||runBusy)?.6:1}}>
              <Icon name={settings.live?'zap':'play'} size={15} className={(running||runBusy)?'cspin':''}/>
              {settings.live?'Run enrollment':'Preview run'}
            </button>
          </div>
        </div>

        {loading
          ? <div style={{padding:'80px 0',textAlign:'center',...TX.meta}}>Loading campaigns…</div>
          : <>

        {/* "what needs you" action bar */}
        <div style={{display:'flex',alignItems:'center',marginBottom:20,padding:'14px 22px',background:'#fff',borderRadius:12,border:'1px solid #e4e4e7'}}>
          {(eligible.length||neededUnmapped)?(
            <>
              {eligible.length>0&&(
                <button onClick={()=>setTab('ready')} style={{display:'flex',alignItems:'center',gap:11,background:'none',border:'none',cursor:'pointer',padding:0}}>
                  <span style={{width:30,height:30,borderRadius:8,background:'#fffbeb',display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0}}><Icon name="zap" size={15} style={{color:'#b45309'}}/></span>
                  <span style={{display:'flex',alignItems:'baseline',gap:6}}><b style={{fontSize:17,fontWeight:700,color:'#b45309',fontVariantNumeric:'tabular-nums'}}>{eligible.length}</b><span style={{fontSize:13,color:'#3f3f46'}}>account{eligible.length===1?'':'s'} ready to enroll</span></span>
                </button>
              )}
              {neededUnmapped>0&&(
                <>
                  {eligible.length>0&&<span style={{width:1,height:28,background:'#f4f4f5',margin:'0 22px'}}/>}
                  <button onClick={()=>setTab('sequences')} style={{display:'flex',alignItems:'center',gap:11,background:'none',border:'none',cursor:'pointer',padding:0}}>
                    <span style={{width:30,height:30,borderRadius:8,background:'#eef2ff',display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0}}><Icon name="info" size={15} style={{color:'#4f46e5'}}/></span>
                    <span style={{display:'flex',alignItems:'baseline',gap:6}}><b style={{fontSize:17,fontWeight:700,color:'#4f46e5',fontVariantNumeric:'tabular-nums'}}>{neededUnmapped}</b><span style={{fontSize:13,color:'#3f3f46'}}>sequence{neededUnmapped===1?'':'s'} need{neededUnmapped===1?'s':''} a Reply.io campaign</span></span>
                  </button>
                </>
              )}
            </>
          ):(
            <span style={{display:'inline-flex',alignItems:'center',gap:8,fontSize:13,color:'#71717a'}}><Icon name="check" size={15} style={{color:'#10b981'}}/>You're all caught up — qualifying accounts will appear here on their own.</span>
          )}
          <span style={{marginLeft:'auto',fontSize:12,color:'#d4d4d8'}}>
            {board.enrolled_accounts} account{board.enrolled_accounts===1?'':'s'} enrolled so far
            {lr&&lr.ran_at?` · last run ${relTime(lr.ran_at)}${lr.dry_run?' (preview)':''}${lr.trigger==='auto_after_sync'?' (auto)':''}`:''}
          </span>
        </div>

        {/* last-run summary strip (only when it did / would do something) */}
        {lr&&((lrStats.would_enroll_accounts||0)>0||(lrStats.accounts_enrolled||0)>0)&&(
          <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:20,padding:'10px 22px',background:lr.dry_run?'rgba(238,242,255,.5)':'rgba(236,253,245,.6)',borderRadius:10,border:`1px solid ${lr.dry_run?'#e0e7ff':'#a7f3d0'}`,fontSize:12.5,color:lr.dry_run?'#3730a3':'#065f46'}}>
            <Icon name={lr.dry_run?'info':'check'} size={14}/>
            {lr.dry_run
              ? <>Last {lr.trigger==='auto_after_sync'?'automatic':'preview'} run would have enrolled <b style={{margin:'0 4px'}}>{lrStats.would_enroll_accounts||0} account(s), {lrStats.would_enroll_contacts||0} contact(s)</b> — Testing mode held it back.</>
              : <>Last run enrolled <b style={{margin:'0 4px'}}>{lrStats.accounts_enrolled||0} account(s), {lrStats.contacts_enrolled||0} contact(s)</b>{lrStats.contacts_409?` (${lrStats.contacts_409} already in sequences)`:''}.</>}
          </div>
        )}

        <div style={{background:'#fff',borderRadius:12,border:'1px solid #e4e4e7',overflow:'hidden'}}>
          <div style={{display:'flex',alignItems:'stretch',borderBottom:'1px solid #f4f4f5',padding:'0 16px'}}>
            <Tab id="ready" label="Ready to enroll" count={eligible.length}/>
            <Tab id="enrolled" label="Enrolled" count={board.enrolled_accounts}/>
            <Tab id="sequences" label="Sequences" count={`${sequences.length-unmapped}/${sequences.length}`}/>
          </div>
          {tab==='ready'&&<ReadyView eligible={eligible} live={settings.live} onEnroll={enrollOne}/>}
          {tab==='enrolled'&&<LedgerView enrollments={enrollments} sequences={sequences}/>}
          {tab==='sequences'&&<SequencesView sequences={sequences} replyio={replyio} replyioErr={replyioErr} onSave={saveMapping} saving={saving} onRefresh={refreshReplyio} refreshing={refreshing} heyreach={heyreach} channelMap={board&&board.channel_sequences} onSaveChannel={saveChannelMapping} customRules={board&&board.custom_rules} onSaveRules={saveRules}/>}
        </div>
        </>}
      </main>
    </div>
  );
}
window.CampaignsView = CampaignsView;
