const { useState, useEffect, useMemo, useRef } = React;

// ── Engagement console (redesign) ────────────────────────────────────────────
// Momentum-first heat board. Default "Activity" tab = the worklist (accounts that
// moved recently, what changed, newest first); "Accounts" = the full list by heat;
// "Inbox" = grouped touches. Plus a "what needs you" action bar, a detail drawer
// (momentum hero + breakdown + timeline) and an Activate → Slack flow. Reads
// /api/engagement (+ /inbox, /{id}); posts /{id}/activate.
// Ported from the standalone design; primitives mapped to the app's window.Icons /
// SegmentBadge, mock data swapped for the live API.

// ── primitives (map the design-system imports onto the app's) ─────────────────
const SegmentBadge = window.SegmentBadge;
const _ICON_ALIAS = { alert: 'info', help: 'info', doc: 'ext' };
function Icon({ name, size = 14, style, className }) {
  const C = window.Icons[name] || window.Icons[_ICON_ALIAS[name]] || window.Icons.info;
  return <C width={size} height={size} style={style} className={className} />;
}
function Button({ variant = 'secondary', size = 'md', iconLeft, disabled, onClick, children }) {
  const base = { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
    fontFamily: 'var(--font-sans)', fontWeight: 600, cursor: disabled ? 'default' : 'pointer',
    borderRadius: 8, padding: size === 'lg' ? '10px 16px' : '8px 14px',
    fontSize: 13, opacity: disabled ? 0.5 : 1, transition: 'background .1s,border-color .1s' };
  const skin = {
    primary:   { background: '#4f46e5', color: '#fff', border: '1px solid #4f46e5' },
    secondary: { background: '#fff', color: '#3f3f46', border: '1px solid #e4e4e7' },
    ghost:     { background: 'none', color: '#71717a', border: '1px solid transparent' },
  }[variant] || {};
  return (
    <button onClick={disabled ? undefined : onClick} disabled={disabled} style={{ ...base, ...skin }}>
      {iconLeft && <Icon name={iconLeft} size={14} />}{children}
    </button>
  );
}

// ── CSS the design relies on (keyframes + a few classes + font var) ───────────
const _CSS = `
:root{--font-sans:'Inter',system-ui,-apple-system,sans-serif;--zinc-50:#fafafa;}
@keyframes _spin{to{transform:rotate(360deg)}}
.spin{animation:_spin .8s linear infinite;transform-origin:center}
@keyframes _pulse{0%,100%{opacity:1}50%{opacity:.3}}
.pulse{animation:_pulse 1.1s ease-in-out infinite}
@keyframes _draw{to{stroke-dashoffset:0}}
@keyframes _slide{from{transform:translateX(18px);opacity:.5}to{transform:none;opacity:1}}
@keyframes _fade{from{opacity:0}to{opacity:1}}
.fade{animation:_fade .15s ease}
@keyframes _pop{from{transform:scale(.97);opacity:0}to{transform:none;opacity:1}}
.pop{animation:_pop .16s cubic-bezier(.16,1,.3,1)}
`;

// ── palette / scales (heat thresholds match auto_search/engagement/scoring.py) ─
const HEAT = {
  Hot:   { fg:'#b45309', solid:'#f59e0b', soft:'rgba(245,158,11,.10)', row:'rgba(255,251,235,.5)' },
  Warm:  { fg:'#047857', solid:'#10b981', soft:'rgba(16,185,129,.10)', row:'transparent' },
  Some:  { fg:'#0369a1', solid:'#0ea5e9', soft:'rgba(14,165,233,.10)', row:'transparent' },
  Lower: { fg:'#a1a1aa', solid:'#d4d4d8', soft:'rgba(161,161,170,.10)', row:'transparent' },
};
const tierOf = (s)=> s>=21?'Hot':s>=12?'Warm':s>=6?'Some':'Lower';
const KIND = {
  high_intent_lead:{ label:'BOFU', dot:'#f43f5e', weight:10, big:true },
  opportunity:     { label:'Opportunity',       dot:'#f43f5e', weight:10, big:true },
  meeting_booked:  { label:'Meeting booked',     dot:'#6366f1', weight:10, big:true },
  tradeshow:       { label:'Tradeshow',          dot:'#f97316', weight:10, big:true },
  podcast_lead:    { label:'Podcast',            dot:'#7c3aed', weight:4 },
  reply:           { label:'Reply',              dot:'#10b981', weight:6 },
  low_intent_lead: { label:'TOFU lead (form)',   dot:'#94a3b8', weight:6 },
  linkedin_tofu:   { label:'TOFU lead (LinkedIn ad)', dot:'#0a66c2', weight:6 },
  click:           { label:'Click',              dot:'#0ea5e9', weight:1 },
};
const kindOf = (k)=> KIND[k] || { label:k||'Touch', dot:'#d4d4d8', weight:1 };
const MATCH = { domain:{label:'Domain',fg:'#059669'}, name:{label:'Name',fg:'#0284c7'} };

// ── server → component shapes ─────────────────────────────────────────────────
function segKey(r){
  const hay = ((r.framework||'') + ' ' + (r.segment||'')).toLowerCase();
  if (hay.includes('payer')) return 'payer';
  if (hay.includes('health system') || hay.includes('hospital') || hay.includes('rural health')) return 'health_system';
  if (hay.includes('special') || hay.includes('physician group') || hay.includes('pgs')) return 'specialty';
  return null;
}
function frameworkText(r){
  return [r.framework || r.segment, r.fit_tier].filter(Boolean).join(' · ') || 'Unclassified';
}
function mapAccount(r){
  return {
    id:r.account_id, name:r.name, segment:segKey(r), abm:!!r.abm,
    contacts:r.contacts||0, domain:r.domain||'', framework:frameworkText(r),
    score:r.score||0, series:(r.series&&r.series.length?r.series:[0,0,0,0,0,0,0,0]),
    trend:r.trend||'flat', deltaWeek:r.delta_week||0, actioned:!!r.activated, lastTouch:r.last_touch,
    recent:r.recent||null,   // {kind, at} — most significant meaningful touch (last 14d)
  };
}
const mapInbox = (e)=>({ company:e.company, account:e.account_name||null, match:e.match_tier||null,
  kind:e.kind, pts:e.points||0, ts:e.occurred_at });
function mapDetail(d){
  // join each event to the contact behind it (by contact_ext) so the timeline can
  // show WHO engaged on hover. Contacts carry email + title (no separate name field).
  const byId={};
  (d.contacts||[]).forEach(c=>{ if(c&&c.external_id!=null) byId[String(c.external_id)]={email:c.email||'',title:c.title||'',company:c.company||''}; });
  const events=(d.events||[]).map(e=>{ const c=byId[String(e.contact_ext)]||{};
    const att=(e.raw&&e.raw.attendee)||{};   // SFDC meeting attendee (name only)
    return { kind:e.kind, label:kindOf(e.kind).label, person:e.campaign||e.company||'',
      ts:e.occurred_at, pts:e.points||0, count:1,
      email:c.email||'', title:c.title||'', name:att.name||'' }; });
  const contacts=(d.contacts||[]).map(c=>c.email||c.company||c.external_id).filter(Boolean);
  return { contacts, events };
}

// Derive a display name from an email local part ("jane.smith@acme.com" -> "Jane Smith").
// Returns '' for role/shared addresses (info@, sales@) where there is no person to name.
function nameFromEmail(email){
  if(!email||email.indexOf('@')<0) return '';
  const local=email.split('@')[0];
  if(/^(info|sales|contact|hello|admin|support|team|office|no-?reply|noreply|marketing)$/i.test(local)) return '';
  const parts=local.split(/[._-]+/).filter(p=>/[a-z]/i.test(p)&&p.length>1);
  if(!parts.length) return '';
  return parts.map(p=>p.charAt(0).toUpperCase()+p.slice(1).toLowerCase()).join(' ');
}

// ── helpers ───────────────────────────────────────────────────────────────────
function relTime(iso){ if(!iso)return'—'; const d=Math.max(0,Date.now()-new Date(iso).getTime()),m=Math.round(d/60000); if(m<2)return'just now'; if(m<60)return`${m}m`; const h=Math.round(m/60); if(h<24)return`${h}h`; const dy=Math.round(h/24); return dy<30?`${dy}d`:new Date(iso).toLocaleDateString('en-US',{month:'short',day:'numeric'}); }
function daysSince(iso){ return iso?Math.max(0,Math.round((Date.now()-new Date(iso).getTime())/86400000)):0; }
// group drawer touches by kind + day so the timeline reads "Click ×8 · Jun 11 · +8"
// instead of one identical row per contact (the old console's clean pattern).
function groupEvents(events){
  const m=new Map();
  (events||[]).forEach(e=>{
    const day=e.ts?new Date(e.ts).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}):'—';
    const key=e.kind+'|'+day;
    const g=m.get(key)||{kind:e.kind,day,count:0,pts:0,ts:'',people:[]};
    g.count+=1; g.pts+=(e.pts||0); if((e.ts||'')>g.ts)g.ts=e.ts;
    const email=e.email||'',name=e.name||'';   // distinct people behind this touch (for the hover)
    if((email||name)&&!g.people.some(p=>p.email===email&&p.name===name)) g.people.push({email,name,title:e.title||''});
    m.set(key,g);
  });
  return [...m.values()].sort((a,b)=>(a.ts||'').localeCompare(b.ts||''));
}
const TX = {
  strong:{fontSize:14,fontWeight:500,color:'#18181b',lineHeight:1.4},
  body:{fontSize:13,fontWeight:400,color:'#52525b',lineHeight:1.4},
  meta:{fontSize:12,fontWeight:400,color:'#a1a1aa',lineHeight:1.3},
  label:{fontSize:11,fontWeight:600,color:'#a1a1aa',textTransform:'uppercase',letterSpacing:'.06em'},
};

function Sparkline({ series, color, w=92, h=30, fill=true, animate=true }){
  const max=Math.max(...series,1), min=Math.min(...series,0);
  const span=max-min||1;
  const pts=series.map((v,i)=>{
    const x=(i/(series.length-1||1))*(w-4)+2;
    const y=h-3-((v-min)/span)*(h-6);
    return [x,y];
  });
  const line=pts.map((p,i)=>`${i?'L':'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const area=`${line} L${(w-2).toFixed(1)},${h-1} L2,${h-1} Z`;
  const last=pts[pts.length-1];
  const gid='sg'+Math.random().toString(36).slice(2,8);
  return (
    <svg width={w} height={h} style={{display:'block',overflow:'visible'}}>
      <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor={color} stopOpacity="0.16"/>
        <stop offset="100%" stopColor={color} stopOpacity="0"/>
      </linearGradient></defs>
      {fill&&<path d={area} fill={`url(#${gid})`}/>}
      <path d={line} fill="none" stroke={color} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"
        style={animate?{strokeDasharray:300,strokeDashoffset:300,animation:'_draw .9s cubic-bezier(.16,1,.3,1) forwards'}:undefined}/>
      <circle cx={last[0]} cy={last[1]} r="2.4" fill={color}/>
      <circle cx={last[0]} cy={last[1]} r="4.5" fill={color} opacity="0.18"/>
    </svg>
  );
}

const TREND = {
  up:   { icon:'arrowUp', fg:'#047857', label:'Heating up' },
  flat: { icon:'arrowRight', fg:'#a1a1aa', label:'Steady' },
  down: { icon:'moon', fg:'#a1a1aa', label:'Cooling' },
};
function MomentumCell({ account:a }){
  const t=TREND[a.trend]||TREND.flat;
  const col=a.trend==='up'?'#10b981':a.trend==='down'?'#d4d4d8':'#a1a1aa';
  return (
    <div style={{display:'flex',alignItems:'center',gap:12}}>
      <Sparkline series={a.series} color={col}/>
      <div style={{display:'flex',flexDirection:'column',gap:1,minWidth:64}}>
        <span style={{display:'inline-flex',alignItems:'center',gap:3,fontSize:12,fontWeight:500,color:t.fg}}>
          <Icon name={t.icon} size={11}/>{t.label}
        </span>
        <span style={{fontSize:11,color:'#a1a1aa',fontVariantNumeric:'tabular-nums'}}>
          {a.trend==='up'?'+':''}{a.deltaWeek} pts this wk
        </span>
      </div>
    </div>
  );
}

function HeatMark({ score }){
  const hc=HEAT[tierOf(score)];
  return (
    <div style={{display:'flex',alignItems:'center',gap:9}}>
      <span style={{width:8,height:8,borderRadius:'50%',background:hc.solid,flexShrink:0}}/>
      <span style={{fontSize:17,fontWeight:600,fontVariantNumeric:'tabular-nums',color:hc.fg,lineHeight:1}}>{score}</span>
      <span style={{fontSize:12,color:'#a1a1aa'}}>{tierOf(score)}</span>
    </div>
  );
}

// ── ACCOUNTS ─────────────────────────────────────────────────────────────────
const COLS='minmax(0,1fr) 96px 208px 156px 92px';
function AccountRow({ a, onOpen, onActivate, showReason }){
  const [hov,setHov]=useState(false);
  const tier=tierOf(a.score), hc=HEAT[tier];
  const rk=showReason&&a.recent?kindOf(a.recent.kind):null;
  return (
    <div onClick={()=>onOpen(a)} onMouseEnter={()=>setHov(true)} onMouseLeave={()=>setHov(false)}
      style={{display:'grid',gridTemplateColumns:COLS,alignItems:'center',gap:20,padding:'14px 28px',
        borderBottom:'1px solid #f4f4f5',cursor:'pointer',
        background:hov?'rgba(244,244,245,.55)':hc.row,transition:'background .1s ease'}}>
      <div style={{minWidth:0}}>
        <div style={{display:'flex',alignItems:'center',gap:8}}>
          <span style={{...TX.strong,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{a.name}</span>
          {a.segment&&<SegmentBadge segment={a.segment}/>}
          {a.abm&&<span style={{flexShrink:0,borderRadius:6,background:'#fffbeb',padding:'1px 6px',fontSize:10.5,fontWeight:500,color:'#b45309',boxShadow:'inset 0 0 0 1px #fef3c7'}}>ABM</span>}
        </div>
        <div style={{...TX.meta,marginTop:3,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{a.contacts} contacts · {a.domain} · {a.framework}</div>
      </div>
      <HeatMark score={a.score}/>
      <MomentumCell account={a}/>
      {rk
        ? <span style={{display:'inline-flex',alignItems:'center',gap:7,minWidth:0}}>
            <span style={{width:7,height:7,borderRadius:'50%',background:rk.dot,flexShrink:0}}/>
            <span style={{...TX.body,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{rk.label}</span>
            <span style={{...TX.meta,whiteSpace:'nowrap'}}>· {relTime(a.recent.at)}</span>
          </span>
        : <span style={{...TX.meta,whiteSpace:'nowrap'}}>Last touch {relTime(a.lastTouch)}</span>}
      <div style={{display:'flex',justifyContent:'flex-end',alignItems:'center',gap:6}}>
        {a.actioned
          ? <span title="Already sent to Slack — click to re-send" onClick={e=>{e.stopPropagation();onActivate(a,true);}}
              style={{display:'inline-flex',alignItems:'center',gap:4,background:'#f0fdf4',border:'1px solid #bbf7d0',fontFamily:'var(--font-sans)',fontSize:12,fontWeight:500,color:'#15803d',cursor:'pointer',padding:'4px 10px',borderRadius:6}}>
              <Icon name="check" size={12}/>Activated</span>
          : (tier==='Hot'||tier==='Warm'||tier==='Some')&&<button onClick={e=>{e.stopPropagation();onActivate(a);}}
              title={tier==='Hot'?'Send to the AE':'Send to the SDR'}
              style={{background:'#fffbeb',border:'1px solid #fde68a',fontFamily:'var(--font-sans)',fontSize:12,fontWeight:500,color:'#b45309',cursor:'pointer',padding:'4px 10px',borderRadius:6}}>Activate</button>}
        <span style={{color:hov?'#a1a1aa':'#e4e4e7'}}><Icon name="arrowRight" size={14}/></span>
      </div>
    </div>
  );
}

function AccountsView({ accounts, onOpen, onActivate, segFilter }){
  const vis=accounts.filter(a=>segFilter==='all'||a.segment===segFilter).sort((a,b)=>b.score-a.score);
  return (
    <>
      <div style={{display:'grid',gridTemplateColumns:COLS,gap:20,padding:'8px 28px',borderBottom:'1px solid #f4f4f5',background:'#fafafa'}}>
        <div style={TX.label}>Account</div><div style={TX.label}>Heat</div>
        <div style={TX.label}>Momentum · last 8 weeks</div><div style={TX.label}>Status</div><div/>
      </div>
      {vis.length===0
        ? <div style={{padding:'40px 28px',textAlign:'center',...TX.meta}}>No accounts in this segment.</div>
        : vis.map(a=><AccountRow key={a.id} a={a} onOpen={onOpen} onActivate={onActivate}/>)}
    </>
  );
}

// Activity = the worklist: only accounts that actually moved recently (a meaningful
// touch, not a click — filtered server-side via `recent`), newest first, tier-jumps up
// top. The short list a rep works from, vs Accounts (everything, by lifetime heat).
function ActivityView({ accounts, onOpen, onActivate, segFilter }){
  const up=a=>tierOf(a.score)!==tierOf(a.score-a.deltaWeek)?1:0;
  const vis=accounts
    .filter(a=>(segFilter==='all'||a.segment===segFilter)&&a.recent)
    .sort((a,b)=> up(b)-up(a) || (b.recent.at||'').localeCompare(a.recent.at||'') || b.deltaWeek-a.deltaWeek);
  return (
    <>
      <div style={{display:'grid',gridTemplateColumns:COLS,gap:20,padding:'8px 28px',borderBottom:'1px solid #f4f4f5',background:'#fafafa'}}>
        <div style={TX.label}>Account</div><div style={TX.label}>Heat</div>
        <div style={TX.label}>Momentum · last 8 weeks</div><div style={TX.label}>What changed</div><div/>
      </div>
      {vis.length===0
        ? <div style={{padding:'40px 28px',textAlign:'center',...TX.meta}}>Nothing has moved here lately — check Accounts for the full list.</div>
        : vis.map(a=><AccountRow key={a.id} a={a} onOpen={onOpen} onActivate={onActivate} showReason/>)}
    </>
  );
}

// ── INBOX ─────────────────────────────────────────────────────────────────────
function groupInbox(events){
  const m=new Map();
  events.forEach(e=>{
    const key=e.company+'|'+(e.account||'')+'|'+(e.match||'');
    const g=m.get(key)||{company:e.company,account:e.account,match:e.match,kinds:{},pts:0,ts:'',top:null,topW:-1};
    g.kinds[e.kind]=(g.kinds[e.kind]||0)+1;
    g.pts+=e.pts;
    if((e.ts||'')>g.ts) g.ts=e.ts;
    const w=kindOf(e.kind).weight; if(w>g.topW){g.topW=w;g.top=e.kind;}
    m.set(key,g);
  });
  return [...m.values()].sort((a,b)=>(b.ts||'').localeCompare(a.ts||''));
}

function KindSummary({ kinds }){
  const order=Object.entries(kinds).sort((a,b)=>kindOf(b[0]).weight-kindOf(a[0]).weight);
  return (
    <div style={{display:'flex',alignItems:'center',gap:8,flexWrap:'wrap'}}>
      {order.map(([k,n])=>{ const m=kindOf(k); return (
        <span key={k} style={{display:'inline-flex',alignItems:'center',gap:5,fontSize:12.5,color:m.big?'#3f3f46':'#71717a',fontWeight:m.big?500:400}}>
          <span style={{width:6,height:6,borderRadius:'50%',background:m.dot,flexShrink:0}}/>
          {m.label}{n>1&&<span style={{color:'#a1a1aa',fontVariantNumeric:'tabular-nums'}}>×{n}</span>}
        </span>
      ); })}
    </div>
  );
}

const ICOLS='minmax(0,1fr) 220px 86px 48px 56px';
const BIG_KINDS=['high_intent_lead','opportunity','meeting_booked','tradeshow'];
function InboxGroupRow({ g, onResolve }){
  const [hov,setHov]=useState(false);
  const unresolved=!g.account;
  const m=g.match?MATCH[g.match]:null;
  const notable=g.topW>=3;
  const accent=notable?kindOf(g.top).dot:'transparent';
  const big=BIG_KINDS.includes(g.top);
  const bg=unresolved?'rgba(255,251,235,.45)':hov?'rgba(244,244,245,.5)':'#fff';
  return (
    <div onMouseEnter={()=>setHov(true)} onMouseLeave={()=>setHov(false)}
      style={{display:'grid',gridTemplateColumns:ICOLS,alignItems:'center',gap:20,padding:'12px 28px 12px 25px',
        borderBottom:'1px solid #f4f4f5',borderLeft:`3px solid ${accent}`,background:bg,transition:'background .1s'}}>
      <div style={{minWidth:0}}>
        <div style={{display:'flex',alignItems:'center',gap:8,minWidth:0}}>
          <span style={{fontSize:14,fontWeight:notable?500:400,color:notable?'#18181b':'#52525b',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{g.company}</span>
          {big&&<span style={{flexShrink:0,display:'inline-flex',alignItems:'center',gap:4,borderRadius:5,padding:'1px 6px',fontSize:10.5,fontWeight:600,color:'#be123c',background:'rgba(244,63,94,.08)'}}>{kindOf(g.top).label}</span>}
        </div>
        <div style={{marginTop:4}}><KindSummary kinds={g.kinds}/></div>
      </div>
      <div style={{minWidth:0}}>
        {unresolved
          ? <span style={{...TX.meta,color:'#b45309',fontStyle:'italic'}}>no account match</span>
          : <><div style={{...TX.body,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{g.account}</div>
              <div style={{fontSize:11,color:m?m.fg:'#a1a1aa',marginTop:1}}>matched by {m?m.label.toLowerCase():'—'}</div></>}
      </div>
      <div>
        {unresolved
          ? <button onClick={()=>onResolve(g)} style={{background:'rgba(217,119,6,.08)',border:'none',fontFamily:'var(--font-sans)',fontSize:12,fontWeight:500,color:'#b45309',cursor:'pointer',padding:'4px 10px',borderRadius:6}}>Resolve →</button>
          : <span style={{...TX.meta,color:'#a1a1aa'}}>auto</span>}
      </div>
      <div style={{textAlign:'right',fontSize:14,fontWeight:600,fontVariantNumeric:'tabular-nums',color:unresolved?'#b45309':'#3f3f46'}}>+{g.pts}</div>
      <div style={{...TX.meta,textAlign:'right'}}>{relTime(g.ts)}</div>
    </div>
  );
}

function InboxView({ events, onResolve }){
  const groups=useMemo(()=>groupInbox(events),[events]);
  const byPriority=(a,b)=>(b.topW-a.topW)||((b.ts||'').localeCompare(a.ts||''));
  const unresolved=groups.filter(g=>!g.account).sort(byPriority);
  const resolved=groups.filter(g=>g.account).sort(byPriority);
  const unrTouches=unresolved.reduce((s,g)=>s+Object.values(g.kinds).reduce((x,n)=>x+n,0),0);
  return (
    <>
      {unresolved.length>0&&(
        <div style={{display:'flex',alignItems:'center',gap:8,padding:'9px 28px',background:'rgba(255,251,235,.6)',borderBottom:'1px solid #fef3c7'}}>
          <span style={{width:7,height:7,borderRadius:'50%',background:'#f59e0b',flexShrink:0}}/>
          <span style={{fontSize:13,color:'#92400e'}}><strong style={{fontWeight:500}}>{unrTouches} touches across {unresolved.length} {unresolved.length===1?'company':'companies'}</strong> couldn't be matched — grouped here for review.</span>
        </div>
      )}
      <div style={{display:'grid',gridTemplateColumns:ICOLS,gap:20,padding:'8px 28px',borderBottom:'1px solid #f4f4f5',background:'#fafafa'}}>
        <div style={TX.label}>Company · activity</div><div style={TX.label}>Account</div>
        <div style={TX.label}>Match</div><div style={{...TX.label,textAlign:'right'}}>Pts</div>
        <div style={{...TX.label,textAlign:'right'}}>When</div>
      </div>
      {events.length===0&&<div style={{padding:'40px 28px',textAlign:'center',...TX.meta}}>No recent touches.</div>}
      {unresolved.map((g,i)=><InboxGroupRow key={'u'+i} g={g} onResolve={onResolve}/>)}
      {resolved.map((g,i)=><InboxGroupRow key={'r'+i} g={g} onResolve={onResolve}/>)}
    </>
  );
}

// ── DRAWER ────────────────────────────────────────────────────────────────────
// One timeline touch. On hover, any touch with a known person reveals who engaged
// (name derived from the email, plus the email and title). Only bare email clicks
// stay quiet. Account-level signals with no individual contact (e.g. Salesforce
// meetings) have nothing to show, so no hover appears there.
function TimelineRow({ g }){
  const m=kindOf(g.kind);
  const [hov,setHov]=useState(false);
  const showWho=g.kind!=='click' && g.people && g.people.length>0;
  return (
    <div style={{position:'relative',paddingLeft:20,paddingBottom:14}}
      onMouseEnter={()=>showWho&&setHov(true)} onMouseLeave={()=>setHov(false)}>
      <span style={{position:'absolute',left:-4,top:4,width:8,height:8,borderRadius:'50%',background:m.dot,boxShadow:'0 0 0 3px #fff'}}/>
      <div style={{...TX.body,cursor:showWho?'help':'default'}}>{m.label}{g.count>1&&<span style={{color:'#a1a1aa'}}> ×{g.count}</span>}</div>
      <div style={{...TX.meta,marginTop:2,display:'flex',gap:6,flexWrap:'wrap'}}>
        <span>{g.day}</span>
        <span style={{color:'#e4e4e7'}}>·</span>
        <span style={{fontWeight:500,color:m.dot}}>+{g.pts} pts</span>
      </div>
      {hov&&showWho&&(
        <div style={{position:'absolute',left:20,bottom:'calc(100% - 6px)',zIndex:60,background:'#18181b',color:'#fff',borderRadius:8,padding:'8px 11px',boxShadow:'0 10px 28px rgba(0,0,0,.22)',minWidth:200,maxWidth:300}}>
          <div style={{fontSize:10,fontWeight:700,letterSpacing:'.05em',textTransform:'uppercase',color:'#a1a1aa',marginBottom:6}}>Who engaged</div>
          {g.people.map((p,j)=>{ const nm=p.name||nameFromEmail(p.email); return (
            <div key={j} style={{marginBottom:j<g.people.length-1?7:0,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',maxWidth:278}}>
              {nm&&<div style={{fontSize:13,fontWeight:600,lineHeight:1.3}}>{nm}</div>}
              {p.email&&<div style={{fontSize:12,color:'#d4d4d8',lineHeight:1.4}}>{p.email}</div>}
              {p.title&&<div style={{fontSize:11,color:'#a1a1aa',lineHeight:1.4}}>{p.title}</div>}
              {!nm&&!p.email&&<div style={{fontSize:12,color:'#d4d4d8'}}>contact on file</div>}
            </div>
          ); })}
        </div>
      )}
    </div>
  );
}

function DetailDrawer({ account:a, detail, accounts, onClose, onActivate }){
  if(!a) return null;
  const tier=tierOf(a.score), hc=HEAT[tier];
  const d=detail||{contacts:[],events:[]};
  const breakdown={};
  d.events.forEach(e=>{ const pts=e.pts*(e.count||1); breakdown[e.kind]=(breakdown[e.kind]||0)+pts; });
  const total=Object.values(breakdown).reduce((s,v)=>s+v,0)||a.score;
  const col=a.trend==='up'?'#10b981':a.trend==='down'?'#d4d4d8':'#a1a1aa';
  const t=TREND[a.trend]||TREND.flat;
  return (
    <div style={{position:'fixed',inset:0,zIndex:40}}>
      <div className="fade" onClick={onClose} style={{position:'absolute',inset:0,background:'rgba(24,24,27,.15)'}}/>
      <aside style={{position:'absolute',right:0,top:0,height:'100%',width:'100%',maxWidth:440,display:'flex',flexDirection:'column',background:'#fff',boxShadow:'0 25px 50px -12px rgba(24,24,27,.18)',animation:'_slide .28s ease-out'}}>
        <div style={{padding:'22px 24px 18px',borderBottom:'1px solid #f4f4f5'}}>
          <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start'}}>
            <div style={{minWidth:0}}>
              <h2 style={{margin:0,fontSize:18,fontWeight:600,letterSpacing:'-.02em',color:'#18181b',lineHeight:1.3}}>{a.name}</h2>
              <div style={{marginTop:8,display:'flex',alignItems:'center',gap:8,flexWrap:'wrap'}}>
                {a.segment&&<SegmentBadge segment={a.segment}/>}
                {a.abm&&<span style={{borderRadius:6,background:'#fffbeb',padding:'1px 6px',fontSize:10.5,fontWeight:500,color:'#b45309',boxShadow:'inset 0 0 0 1px #fef3c7'}}>ABM</span>}
              </div>
            </div>
            <button onClick={onClose} style={{border:'none',background:'none',padding:6,cursor:'pointer',color:'#a1a1aa',borderRadius:8}}><Icon name="x" size={18}/></button>
          </div>
          <div style={{...TX.meta,marginTop:10}}>{a.framework} · {a.contacts} contacts engaging</div>
        </div>
        <div style={{flex:1,overflowY:'auto',padding:'20px 24px'}}>
          <div style={{borderRadius:12,border:'1px solid #f4f4f5',padding:'16px 18px',background:'linear-gradient(180deg,#fafafa,#fff)'}}>
            <div style={{display:'flex',alignItems:'flex-end',justifyContent:'space-between',marginBottom:12}}>
              <div>
                <div style={TX.label}>Engagement momentum</div>
                <div style={{display:'flex',alignItems:'baseline',gap:8,marginTop:6}}>
                  <span style={{fontSize:30,fontWeight:700,fontVariantNumeric:'tabular-nums',color:hc.fg,lineHeight:1}}>{a.score}</span>
                  <span style={{fontSize:12,color:'#a1a1aa'}}>pts · {tier}</span>
                </div>
              </div>
              <span style={{display:'inline-flex',alignItems:'center',gap:4,fontSize:12.5,fontWeight:500,color:t.fg,padding:'3px 9px',borderRadius:999,background:a.trend==='up'?'rgba(16,185,129,.1)':'rgba(161,161,170,.08)'}}>
                <Icon name={t.icon} size={12}/>{t.label}{a.trend==='up'?` · +${a.deltaWeek} this wk`:''}
              </span>
            </div>
            <Sparkline series={a.series} color={col} w={392} h={64}/>
            <div style={{display:'flex',justifyContent:'space-between',marginTop:6,...TX.meta}}><span>8 weeks ago</span><span>this week</span></div>
          </div>

          <div style={{...TX.label,marginTop:22,marginBottom:10}}>Score breakdown</div>
          <div style={{borderRadius:12,border:'1px solid #f4f4f5',overflow:'hidden'}}>
            {Object.entries(breakdown).sort((x,y)=>y[1]-x[1]).map(([k,pts],i,arr)=>{ const m=kindOf(k);
              return (
                <div key={k} style={{display:'flex',alignItems:'center',gap:12,padding:'9px 14px',borderBottom:i<arr.length-1?'1px solid #fafafa':'none'}}>
                  <span style={{width:7,height:7,borderRadius:'50%',background:m.dot,flexShrink:0}}/>
                  <span style={{...TX.body,flex:1}}>{m.label}</span>
                  <div style={{width:70,height:3,borderRadius:999,background:'#f4f4f5',overflow:'hidden'}}><div style={{height:'100%',width:`${(pts/total)*100}%`,background:m.dot,borderRadius:999,opacity:.7}}/></div>
                  <span style={{fontSize:13,fontWeight:600,fontVariantNumeric:'tabular-nums',color:'#3f3f46',width:30,textAlign:'right'}}>+{pts}</span>
                </div>
              ); })}
            {Object.keys(breakdown).length===0&&<div style={{padding:'12px 14px',...TX.meta}}>Loading touches…</div>}
            <div style={{display:'flex',justifyContent:'space-between',padding:'9px 14px',borderTop:'1px solid #f4f4f5',background:'#fafafa'}}>
              <span style={TX.label}>Total heat</span>
              <span style={{fontSize:15,fontWeight:700,fontVariantNumeric:'tabular-nums',color:hc.fg}}>{a.score}</span>
            </div>
          </div>

          {d.contacts.length>0&&<>
            <div style={{...TX.label,marginTop:22,marginBottom:10}}>Contacts engaging · {a.contacts}</div>
            <div style={{display:'flex',flexWrap:'wrap',gap:5,alignItems:'center'}}>
              {d.contacts.slice(0,12).map((c,i)=>(
                <span key={i} title={c} style={{width:28,height:28,borderRadius:'50%',background:'#fafafa',border:'1px solid #f4f4f5',display:'flex',alignItems:'center',justifyContent:'center',fontSize:11,fontWeight:600,color:'#52525b'}}>{(c[0]||'?').toUpperCase()}</span>
              ))}
              {d.contacts.length>12&&<span style={{...TX.meta,marginLeft:4}}>+{d.contacts.length-12} more</span>}
            </div>
          </>}

          <div style={{...TX.label,marginTop:22,marginBottom:10}}>Engagement timeline</div>
          <div style={{borderLeft:'1px solid #f4f4f5',marginLeft:4}}>
            {groupEvents(d.events).map((g,i)=><TimelineRow key={i} g={g}/>)}
            {d.events.length===0&&<div style={{paddingLeft:20,...TX.meta}}>No touches.</div>}
          </div>
        </div>
        <div style={{borderTop:'1px solid #f4f4f5',padding:'14px 24px',display:'flex',gap:8}}>
          {tier==='Hot'&&<button onClick={()=>onActivate(a)} style={{flex:1,display:'inline-flex',alignItems:'center',justifyContent:'center',gap:6,padding:'10px 16px',borderRadius:8,border:'none',fontFamily:'var(--font-sans)',fontSize:13,fontWeight:600,background:'#d97706',color:'#fff',cursor:'pointer'}}><Icon name="zap" size={15}/>Activate to SDR</button>}
          <Button variant="secondary" size="lg" iconLeft="doc" onClick={onClose}>Close</Button>
        </div>
      </aside>
    </div>
  );
}

// ── ACTIVATE MODAL (Slack preview; fetches its own recent touches) ────────────
function ActivateModal({ account:a, onClose, onConfirm }){
  const [ev,setEv]=useState([]);
  useEffect(()=>{ if(a) window.API.engagementAccount(a.id).then(d=>setEv(mapDetail(d).events)).catch(()=>{}); },[a&&a.id]);
  if(!a) return null;
  return (
    <div style={{position:'fixed',inset:0,zIndex:60,display:'flex',alignItems:'center',justifyContent:'center',padding:16}}>
      <div className="fade" onClick={onClose} style={{position:'absolute',inset:0,background:'rgba(24,24,27,.35)',backdropFilter:'blur(3px)'}}/>
      <div className="pop" style={{position:'relative',width:'100%',maxWidth:440,background:'#fff',borderRadius:16,border:'1px solid #e4e4e7',overflow:'hidden',boxShadow:'0 20px 40px rgba(24,24,27,.12)'}}>
        <div style={{padding:'20px 24px 16px',borderBottom:'1px solid #f4f4f5'}}>
          <h3 style={{margin:0,fontSize:16,fontWeight:600,color:'#18181b'}}>Activate {a.name}</h3>
          <p style={{margin:'4px 0 0',...TX.body}}>Posts a heat card to the engagement Slack channel.</p>
        </div>
        <div style={{margin:20,borderRadius:10,border:'1px solid #e1e1e1',overflow:'hidden'}}>
          <div style={{height:3,background:'#4f46e5'}}/>
          <div style={{padding:'12px 14px'}}>
            <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:10}}>
              <div style={{width:26,height:26,borderRadius:6,background:'#4f46e5',display:'flex',alignItems:'center',justifyContent:'center'}}><Icon name="sparkle" size={13} style={{color:'#fff'}}/></div>
              <div><div style={{fontSize:13,fontWeight:600,color:'#1d1c1d'}}>Magical ABM</div><div style={{fontSize:11,color:'#616061'}}>{new Date().toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'})}</div></div>
            </div>
            <div style={{fontSize:14,fontWeight:600,color:'#1d1c1d',marginBottom:4}}>{a.name} is {tierOf(a.score)} — {a.score} pts{a.trend==='up'?`, accelerating (+${a.deltaWeek} this week)`:''}</div>
            <div style={{fontSize:13,color:'#616061',marginBottom:8}}>{a.contacts} contacts engaging across {a.framework}. Recent:</div>
            {ev.slice(0,3).map((e,i)=>{ const m=kindOf(e.kind); return <div key={i} style={{display:'flex',gap:8,marginBottom:4,alignItems:'flex-start'}}><span style={{width:6,height:6,borderRadius:'50%',background:m.dot,marginTop:4,flexShrink:0}}/><span style={{fontSize:13,color:'#1d1c1d'}}>{e.label}{e.person?` — ${e.person}`:''}</span></div>; })}
          </div>
        </div>
        <div style={{display:'flex',justifyContent:'flex-end',gap:8,padding:'0 20px 20px'}}>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <button onClick={onConfirm} style={{display:'inline-flex',alignItems:'center',gap:6,padding:'8px 16px',borderRadius:8,border:'none',fontFamily:'var(--font-sans)',fontSize:13,fontWeight:600,background:'#d97706',color:'#fff',cursor:'pointer'}}><Icon name="zap" size={14}/>Post to Slack</button>
        </div>
      </div>
    </div>
  );
}

// ── MAIN ─────────────────────────────────────────────────────────────────────
function EngagementView({ pushToast }){
  const [accounts,setAccounts]=useState([]);
  const [inbox,setInbox]=useState([]);
  const [lastSync,setLastSync]=useState(null);
  const [loading,setLoading]=useState(true);
  const [tab,setTab]=useState('activity');
  const [open,setOpen]=useState(null);
  const [detail,setDetail]=useState(null);
  const [activating,setActivating]=useState(null);
  const [segFilter,setSegFilter]=useState('all');
  const [syncing,setSyncing]=useState(false);
  const [running,setRunning]=useState(false);   // server-side: a sync is in progress
  const [live,setLive]=useState(null);          // server-side: live routing on/off (null=loading)
  const [cutoff,setCutoff]=useState(null);      // server-side: send-cutoff date (YYYY-MM-DD) or ''
  // Auto-activate: when on, every Hot account is activated once (enriched + posted
  // to Slack), deduped via localStorage so it never re-posts. Mirrors auto-score.
  const [autoActivate,setAutoActivate]=useState(()=>localStorage.getItem('autoActivateEnabled')==='1');
  useEffect(()=>{ try{ localStorage.setItem('autoActivateEnabled', autoActivate?'1':'0'); }catch(_e){} },[autoActivate]);
  const autoRef=useRef(false);

  function load(){
    return Promise.all([window.API.engagement(),window.API.engagementInbox()]).then(([eng,inb])=>{
      setAccounts((eng.accounts||[]).map(mapAccount));
      setLastSync(eng.last_sync||null);
      setRunning(!!eng.running);
      setInbox((inb.events||[]).map(mapInbox));
      setLoading(false);
    });
  }
  useEffect(()=>{ load().catch(e=>{ setLoading(false); pushToast&&pushToast(`Couldn't load engagement: ${e.message}`,'danger'); }); },[]);

  // Deep-link: ?account=<id> (e.g. from a Slack "Open in console" link) opens that
  // account's drawer once the board has loaded. Runs once.
  const deepLinkRef=useRef(false);
  useEffect(()=>{
    if(loading || deepLinkRef.current) return;
    let aid; try{ aid=new URLSearchParams(window.location.search).get('account'); }catch(_e){ aid=null; }
    if(!aid) return;
    deepLinkRef.current=true;
    openAccount(accounts.find(x=>x.id===aid) || {id:aid, name:aid, score:0});
  },[loading,accounts]);  // eslint-disable-line

  // Poll the server's `running` flag so the UI reflects the ACTUAL background sync
  // (it runs all sources and can take a couple minutes), then refresh once it ends.
  const pollRef=useRef(false);
  function pollSyncStatus(){
    if(pollRef.current) return; pollRef.current=true;
    let tries=0;
    const tick=()=>{
      window.API.engagement().then(eng=>{
        setRunning(!!eng.running); setLastSync(eng.last_sync||null);
        if(eng.running && tries++<60){ setTimeout(tick,8000); }
        else { pollRef.current=false; load().then(()=>pushToast&&pushToast('Sync complete','success')); }
      }).catch(()=>{ pollRef.current=false; });
    };
    setTimeout(tick,6000);
  }
  // If a sync is already running when the page loads (someone else started it), track it.
  useEffect(()=>{ if(running) pollSyncStatus(); },[running]);  // eslint-disable-line

  function openAccount(a){ setOpen(a); setDetail(null);
    window.API.engagementAccount(a.id).then(d=>setDetail(mapDetail(d))).catch(()=>setDetail({contacts:[],events:[]})); }
  function sync(){ setSyncing(true); pushToast&&pushToast('Syncing all sources (email, Salesforce, podcast, LinkedIn ads)…','muted');
    window.API.syncEngagement().then(()=>{ setRunning(true); setSyncing(false); pollSyncStatus(); })
      .catch(e=>{ setSyncing(false); pushToast&&pushToast(`Sync failed: ${e.message}`,'danger'); }); }
  function handleActivate(a, force=false){ setActivating(null); setOpen(null);
    pushToast&&pushToast(`${force?'Re-sending':'Enriching'} ${a.name}${force?'':' + posting to Slack'}…`,'muted');
    window.API.activateEngagement(a.id, force).then(r=>{
      if(r&&r.already_activated){ pushToast&&pushToast(`${a.name} is already activated — not re-posted (use Activated → re-send)`,'muted'); return; }
      if(r&&r.suppressed){   // last activity predates the send cutoff — offer a deliberate override
        const lt=(r.last_touch||'').slice(0,10);
        if(window.confirm(`${a.name}'s last activity${lt?` (${lt})`:''} is before the send cutoff (${r.cutoff}).\n\nThis is the already-processed backlog we agreed not to send. Send it anyway?`))
          handleActivate(a, true);
        else pushToast&&pushToast(`${a.name} skipped — before the ${r.cutoff} send cutoff`,'muted');
        return;
      }
      const n=(r&&r.contacts||[]).filter(p=>p.email||p.phone).length;
      pushToast&&pushToast(`Activated ${a.name} — posted to Slack${n?` with ${n} contact${n===1?'':'s'}`:''}`,'success');
      load();   // refresh so the green "Activated" badge appears
    }).catch(e=>pushToast&&pushToast(`Activate failed: ${e.message}`,'danger')); }
  function resetActivations(){
    if(!window.confirm('Reset ALL activations? Every account can then be activated again (for testing).')) return;
    window.API.resetActivations().then(r=>{ pushToast&&pushToast(`Reset ${r.reset} activation${r.reset===1?'':'s'} — accounts can be re-activated`,'success'); load(); })
      .catch(e=>pushToast&&pushToast(`Reset failed: ${e.message}`,'danger')); }

  useEffect(()=>{ window.API.liveRouting().then(s=>setLive(!!s.enabled)).catch(()=>{}); },[]);
  useEffect(()=>{ window.API.sendCutoff().then(s=>setCutoff(s.cutoff||'')).catch(()=>{}); },[]);
  // Live-routing toggle. Flipping it sends NOTHING — it only changes where the NEXT
  // activation goes (real AE/SDR channels + @ping vs the private testing channel). The
  // June-25 send cutoff still holds back the already-processed backlog in EITHER mode.
  function toggleLive(){
    if(live===null) return;
    const next=!live;
    if(next && !window.confirm(
      'Go LIVE?\n\nNew activations will post to the real AE/SDR Slack channels and @-ping them '
      +'(only accounts with activity on/after the send cutoff). This does NOT send anything now.\n\nContinue?')) return;
    window.API.setLiveRouting(next).then(s=>{ setLive(!!s.enabled);
      pushToast&&pushToast(s.enabled
        ?'Live routing ON — activations post to the real AE/SDR channels and ping them'
        :'Live routing OFF — activations stay on the private testing channel (plain names)',
        s.enabled?'success':'muted'); })
      .catch(e=>pushToast&&pushToast(`Couldn't change live routing: ${e.message}`,'danger')); }
  // Send cutoff: only accounts with activity on/after this date are handed off; the older
  // already-processed backlog is suppressed. Changing it sends nothing.
  function editCutoff(){
    const cur=cutoff||'';
    const next=window.prompt('Send cutoff date (YYYY-MM-DD). Only accounts with activity on or after this date are sent; older ones are held back. Leave blank to clear.', cur);
    if(next===null) return;
    const v=next.trim();
    if(v && !/^\d{4}-\d{2}-\d{2}$/.test(v)){ pushToast&&pushToast('Date must be YYYY-MM-DD','danger'); return; }
    window.API.setSendCutoff(v).then(s=>{ setCutoff(s.cutoff||'');
      pushToast&&pushToast(s.cutoff?`Send cutoff set to ${s.cutoff} — only newer activity is handed off`:'Send cutoff cleared — all activity is sendable','success'); })
      .catch(e=>pushToast&&pushToast(`Couldn't set cutoff: ${e.message}`,'danger')); }
  // Auto-route accounts (once each) when the toggle is on:
  // Hot → AE; Warm + Some → SDR. All get the same enriched packet.
  // Sequential so we don't hammer Slack; deduped in localStorage + server-side.
  useEffect(()=>{
    if(!autoActivate || !accounts.length || autoRef.current) return;
    let done; try{ done=new Set(JSON.parse(localStorage.getItem('engagementActivated')||'[]')); }catch(_e){ done=new Set(); }
    const routable=new Set(['Hot','Warm','Some']);
    const todo=accounts.filter(a=>routable.has(tierOf(a.score)) && !done.has(a.id));
    if(!todo.length) return;
    autoRef.current=true;
    const hot=todo.filter(a=>tierOf(a.score)==='Hot').length;
    const sdr=todo.length-hot;
    pushToast&&pushToast(`Auto-routing: ${hot} Hot → AE, ${sdr} Warm/Some → SDR…`,'muted');
    (async()=>{
      let sent=0, held=0;
      for(const a of todo){
        try{
          const r=await window.API.activateEngagement(a.id);
          if(r&&r.suppressed){ held++; continue; }   // before cutoff — skip, don't mark done
          done.add(a.id); sent++;
          try{ localStorage.setItem('engagementActivated', JSON.stringify([...done])); }catch(_e){}
        }catch(_e){ /* leave for the next cycle */ }
      }
      autoRef.current=false;
      pushToast&&pushToast(`Auto-routing complete — ${sent} sent${held?`, ${held} held (before the ${cutoff||'cutoff'})`:''}`,'success');
    })();
  },[accounts,autoActivate]);

  const movers=useMemo(()=>{
    const changed=accounts.filter(a=>a.trend==='up'&&tierOf(a.score)!==tierOf(a.score-a.deltaWeek));
    const base=changed.length?changed:accounts.filter(a=>a.trend==='up');
    return [...base].sort((x,y)=>y.deltaWeek-x.deltaWeek).slice(0,4);
  },[accounts]);
  const hotCount=accounts.filter(a=>tierOf(a.score)==='Hot').length;
  const justWentHot=movers.filter(a=>tierOf(a.score)==='Hot'&&tierOf(a.score-a.deltaWeek)!=='Hot').length;
  const needs=[];
  if(justWentHot) needs.push({n:justWentHot,label:justWentHot===1?'account just went Hot':'accounts just went Hot',icon:'arrowUp',fg:'#047857',bg:'rgba(16,185,129,.1)',go:'activity'});
  if(hotCount) needs.push({n:hotCount,label:hotCount===1?'Hot account':'Hot accounts',icon:'zap',fg:'#b45309',bg:'#fffbeb',go:'accounts'});
  const stats={ accounts:accounts.length, touches:inbox.length,
                active:accounts.filter(a=>a.recent).length };

  function Tab({ id, label, count }){ const act=tab===id; return <button onClick={()=>setTab(id)} style={{position:'relative',display:'inline-flex',alignItems:'center',gap:6,padding:'10px 14px',border:'none',background:'none',cursor:'pointer',fontFamily:'var(--font-sans)',fontSize:13,fontWeight:act?500:400,color:act?'#18181b':'#a1a1aa',marginBottom:-1}}>{label}<span style={{fontSize:11,fontVariantNumeric:'tabular-nums',borderRadius:999,padding:'1px 6px',background:act?'#18181b':'#f4f4f5',color:act?'#fff':'#a1a1aa'}}>{count}</span>{act&&<span style={{position:'absolute',insetInline:0,bottom:-1,height:2,borderRadius:999,background:'#18181b'}}/>}</button>; }

  return (
    <div style={{color:'#18181b'}}>
      <style>{_CSS}</style>
      <main style={{maxWidth:1120,margin:'0 auto',padding:'26px 32px'}}>
        <div style={{display:'flex',alignItems:'flex-end',justifyContent:'space-between',gap:16,marginBottom:22}}>
          <div>
            <h1 style={{margin:0,fontSize:24,fontWeight:600,letterSpacing:'-.02em',color:'#18181b'}}>Engagement</h1>
            <p style={{margin:'5px 0 0',fontSize:14,color:'#71717a',maxWidth:640}}>Buyer intent across email, podcast, Salesforce &amp; LinkedIn ads — matched to your accounts, ranked by heat.</p>
          </div>
          <div style={{display:'flex',alignItems:'center',gap:12}}>
            {live!==null &&
              <label title={live
                ? 'LIVE: activations post to the real AE/SDR channels and @-ping them. Click to switch back to testing.'
                : 'Testing: activations stay on the private channel with plain names. Click to go live.'}
                style={{display:'inline-flex',alignItems:'center',gap:7,fontSize:13,fontWeight:600,color:live?'#b91c1c':'#3f3f46',cursor:'pointer',userSelect:'none'}}>
                <span onClick={toggleLive} style={{position:'relative',width:34,height:20,borderRadius:999,background:live?'#ef4444':'#e4e4e7',transition:'background .15s',flexShrink:0}}>
                  <span style={{position:'absolute',top:2,left:live?16:2,width:16,height:16,borderRadius:'50%',background:'#fff',boxShadow:'0 1px 2px rgba(0,0,0,.2)',transition:'left .15s'}}/>
                </span>
                {live?'LIVE — real channels':'Testing only'}
              </label>}
            {cutoff!==null &&
              <span onClick={editCutoff}
                title="Send cutoff: only accounts with activity on or after this date are handed off to the AE/SDR channels; older already-processed accounts are held back. Click to change."
                style={{display:'inline-flex',alignItems:'center',gap:5,fontSize:12.5,fontWeight:500,whiteSpace:'nowrap',color:cutoff?'#3f3f46':'#a1a1aa',cursor:'pointer',userSelect:'none',padding:'4px 10px',borderRadius:6,background:'#f4f4f5',border:'1px solid #e4e4e7'}}>
                {cutoff?`Sending from ${cutoff}`:'No send cutoff'}
              </span>}
            <label title="Auto-route: Hot → AE (enrich + Slack), Warm → SDR (Slack only)"
              style={{display:'inline-flex',alignItems:'center',gap:7,fontSize:13,color:'#3f3f46',cursor:'pointer',userSelect:'none'}}>
              <span onClick={()=>setAutoActivate(v=>!v)} style={{position:'relative',width:34,height:20,borderRadius:999,background:autoActivate?'#10b981':'#e4e4e7',transition:'background .15s',flexShrink:0}}>
                <span style={{position:'absolute',top:2,left:autoActivate?16:2,width:16,height:16,borderRadius:'50%',background:'#fff',boxShadow:'0 1px 2px rgba(0,0,0,.2)',transition:'left .15s'}}/>
              </span>
              Auto-route
            </label>
            {/* Live sync status: amber pulse while a background sync runs, else last-completed time */}
            {(running||syncing)
              ? <span title="A sync is running in the background (all sources)" style={{display:'inline-flex',alignItems:'center',gap:6,fontSize:12.5,fontWeight:600,color:'#b45309',background:'#fffbeb',border:'1px solid #fde68a',borderRadius:999,padding:'5px 11px'}}>
                  <span className="pulse" style={{width:7,height:7,borderRadius:'50%',background:'#f59e0b',display:'inline-block'}}/>Syncing all sources…
                </span>
              : <span title={lastSync&&lastSync.last_synced_at?`Last completed ${new Date(lastSync.last_synced_at).toLocaleString()}`:'No sync has run yet'} style={{display:'inline-flex',alignItems:'center',gap:6,fontSize:12.5,color:'#71717a'}}>
                  <Icon name="check" size={13} style={{color:lastSync&&lastSync.last_synced_at?'#10b981':'#d4d4d8'}}/>{lastSync&&lastSync.last_synced_at?`Last synced ${relTime(lastSync.last_synced_at)}`:'Never synced'}
                </span>}
            <button onClick={resetActivations} title="Clear all 'Activated' marks so accounts can be re-activated (for testing)"
              style={{display:'inline-flex',alignItems:'center',gap:6,borderRadius:8,background:'#fff',border:'1px solid #e4e4e7',padding:'8px 12px',fontFamily:'var(--font-sans)',fontSize:13,fontWeight:500,color:'#71717a',cursor:'pointer'}}>
              <Icon name="refresh" size={14}/>Reset activations
            </button>
            <button onClick={()=>{window.location.href='/api/engagement/export.csv';}}
              style={{display:'inline-flex',alignItems:'center',gap:6,borderRadius:8,background:'#fff',border:'1px solid #e4e4e7',padding:'8px 14px',fontFamily:'var(--font-sans)',fontSize:13,fontWeight:600,color:'#3f3f46',cursor:'pointer'}}>
              <Icon name="ext" size={15}/>Export CSV
            </button>
            <button onClick={sync} disabled={syncing||running} title="Pull every engagement source: Reply.io email, Salesforce leads/meetings, podcast, and LinkedIn TOFU ad reactions" style={{display:'inline-flex',alignItems:'center',gap:7,borderRadius:8,background:'#4f46e5',border:'none',padding:'8px 14px',fontFamily:'var(--font-sans)',fontSize:13,fontWeight:600,color:'#fff',cursor:'pointer',boxShadow:'0 1px 2px rgba(24,24,27,.05)',opacity:(syncing||running)?.6:1}}>
              <Icon name="refresh" size={15} className={(syncing||running)?'spin':''}/>{(syncing||running)?'Syncing…':'Sync all'}
            </button>
          </div>
        </div>

        {loading
          ? <div style={{padding:'80px 0',textAlign:'center',...TX.meta}}>Loading engagement…</div>
          : <>

        <div style={{display:'flex',alignItems:'center',marginBottom:20,padding:'14px 22px',background:'#fff',borderRadius:12,border:'1px solid #e4e4e7'}}>
          {needs.length?needs.map((it,i)=>(
            <React.Fragment key={i}>
              {i>0&&<span style={{width:1,height:28,background:'#f4f4f5',margin:'0 22px'}}/>}
              <button onClick={()=>setTab(it.go)} style={{display:'flex',alignItems:'center',gap:11,background:'none',border:'none',cursor:'pointer',padding:0}}>
                <span style={{width:30,height:30,borderRadius:8,background:it.bg,display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0}}><Icon name={it.icon} size={15} style={{color:it.fg}}/></span>
                <span style={{display:'flex',alignItems:'baseline',gap:6}}><b style={{fontSize:17,fontWeight:700,color:it.fg,fontVariantNumeric:'tabular-nums'}}>{it.n}</b><span style={{fontSize:13,color:'#3f3f46'}}>{it.label}</span></span>
              </button>
            </React.Fragment>
          )):(
            <span style={{display:'inline-flex',alignItems:'center',gap:8,fontSize:13,color:'#71717a'}}><Icon name="check" size={15} style={{color:'#10b981'}}/>You're all caught up — nothing needs action right now.</span>
          )}
          <span style={{marginLeft:'auto',fontSize:12,color:'#d4d4d8'}}>{stats.accounts} accounts · {stats.touches} touches</span>
        </div>

        <div style={{background:'#fff',borderRadius:12,border:'1px solid #e4e4e7',overflow:'hidden'}}>
          <div style={{display:'flex',alignItems:'stretch',borderBottom:'1px solid #f4f4f5',padding:'0 16px'}}>
            <Tab id="activity" label="Activity" count={stats.active}/>
            <Tab id="accounts" label="Accounts" count={stats.accounts}/>
            {(tab==='accounts'||tab==='activity')&&(
              <label style={{display:'inline-flex',alignItems:'center',gap:5,marginLeft:'auto',fontSize:12,color:'#a1a1aa'}}>Segment
                <select value={segFilter} onChange={e=>setSegFilter(e.target.value)} style={{appearance:'none',borderRadius:6,border:'1px solid #e4e4e7',background:'#fff',padding:'4px 24px 4px 8px',fontFamily:'var(--font-sans)',fontSize:12,fontWeight:500,color:'#3f3f46'}}>
                  <option value="all">All</option><option value="health_system">Health System</option><option value="specialty">Specialty</option><option value="payer">Payer</option>
                </select>
              </label>
            )}
          </div>
          {tab==='accounts'
            ? <AccountsView accounts={accounts} onOpen={openAccount} onActivate={setActivating} segFilter={segFilter}/>
            : <ActivityView accounts={accounts} onOpen={openAccount} onActivate={setActivating} segFilter={segFilter}/>}
        </div>
        </>}
      </main>

      {open&&<DetailDrawer account={open} detail={detail} accounts={accounts} onClose={()=>setOpen(null)} onActivate={setActivating}/>}
      {activating&&<ActivateModal account={activating} onClose={()=>setActivating(null)} onConfirm={()=>handleActivate(activating)}/>}
    </div>
  );
}
window.EngagementView = EngagementView;
