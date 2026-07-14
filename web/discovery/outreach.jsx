const { useState, useEffect, useMemo, useRef } = React;

// ── Outreach dashboard ────────────────────────────────────────────────────────
// Cross-channel outreach performance for the operator: SmartLead email stats
// (sent / open / click / reply / bounce + interested) and HeyReach LinkedIn
// stats (connects sent / accepted, message replies, trend), overall and per
// campaign. Reads /api/outreach/stats (server-cached ~10 min; Refresh forces
// a live pull). Rates are computed server-side from raw counts — a dash means
// "nothing sent yet", never a fake 0%. Mirrors the campaigns console's visual
// language (same primitives, same palette, no new design system).

const _OTX = {
  strong:{fontSize:14,fontWeight:500,color:'#18181b',lineHeight:1.4},
  body:{fontSize:13,fontWeight:400,color:'#52525b',lineHeight:1.4},
  meta:{fontSize:12,fontWeight:400,color:'#a1a1aa',lineHeight:1.3},
  label:{fontSize:11,fontWeight:600,color:'#a1a1aa',textTransform:'uppercase',letterSpacing:'.06em'},
};

function OChip({ fg, bg, ring, children, title }){
  return <span title={title} style={{display:'inline-flex',alignItems:'center',gap:4,borderRadius:6,padding:'1px 7px',fontSize:11,fontWeight:500,color:fg,background:bg,boxShadow:`inset 0 0 0 1px ${ring}`,whiteSpace:'nowrap'}}>{children}</span>;
}

const _OSTATUS = {
  ACTIVE:      {fg:'#047857',bg:'#ecfdf5',ring:'#a7f3d0',label:'Active'},
  IN_PROGRESS: {fg:'#047857',bg:'#ecfdf5',ring:'#a7f3d0',label:'Running'},
  START:       {fg:'#047857',bg:'#ecfdf5',ring:'#a7f3d0',label:'Running'},
  DRAFTED:     {fg:'#71717a',bg:'#fafafa',ring:'#e4e4e7',label:'Draft'},
  DRAFT:       {fg:'#71717a',bg:'#fafafa',ring:'#e4e4e7',label:'Draft'},
  PAUSED:      {fg:'#b45309',bg:'#fffbeb',ring:'#fde68a',label:'Paused'},
  STOPPED:     {fg:'#be123c',bg:'#fff1f2',ring:'#fecdd3',label:'Stopped'},
  COMPLETED:   {fg:'#0369a1',bg:'#f0f9ff',ring:'#bae6fd',label:'Completed'},
  FINISHED:    {fg:'#0369a1',bg:'#f0f9ff',ring:'#bae6fd',label:'Finished'},
};
function OStatusChip({ status }){
  const s=_OSTATUS[String(status||'').toUpperCase()]||{fg:'#71717a',bg:'#fafafa',ring:'#e4e4e7',label:status||'?'};
  return <OChip fg={s.fg} bg={s.bg} ring={s.ring}>{s.label}</OChip>;
}

const _fmtN = (n)=> (n==null?'–':Number(n).toLocaleString('en-US'));
const _fmtPct = (p)=> (p==null?'–':`${p.toFixed(1)}%`);

// A rate cell: the number in ink + a thin 4px meter underneath (marks stay
// thin; text never wears the series color).
function RateCell({ pct, warn }){
  const color = warn==null ? '#4f46e5' : (warn==='amber' ? '#d97706' : warn==='rose' ? '#e11d48' : '#4f46e5');
  return (
    <div style={{minWidth:0}}>
      <div style={{..._OTX.body,fontVariantNumeric:'tabular-nums',color:'#3f3f46'}}>{_fmtPct(pct)}</div>
      <div style={{height:4,borderRadius:4,background:'#f4f4f5',marginTop:3,overflow:'hidden'}}>
        {pct!=null && <div style={{height:'100%',width:`${Math.min(100,pct)}%`,borderRadius:4,background:color}}/>}
      </div>
    </div>
  );
}

// The channel funnel: each stage a thin bar sized against the FIRST stage,
// count + rate directly labeled, stage-to-stage conversion at the right.
// Single hue (one series shrinking through its own stages), recessive track.
function Funnel({ stages }){
  const base=Math.max(stages.length?stages[0].count:0,1);
  return (
    <div style={{padding:'14px 20px 12px',display:'flex',flexDirection:'column',gap:9}}>
      {stages.map((s,i)=>{
        const prev=i?stages[i-1].count:null;
        const conv=i?(prev>0?(100*s.count/prev):null):null;
        return (
          <div key={s.label} style={{display:'grid',gridTemplateColumns:'92px minmax(0,1fr) 150px',alignItems:'center',gap:14}}>
            <div style={{..._OTX.body,color:'#3f3f46'}}>{s.label}</div>
            <div style={{height:6,borderRadius:4,background:'#f4f4f5',overflow:'hidden'}}>
              <div style={{height:'100%',width:`${Math.min(100,100*s.count/base)}%`,borderRadius:4,background:'#4f46e5',transition:'width .3s ease'}}/>
            </div>
            <div style={{..._OTX.body,fontVariantNumeric:'tabular-nums',textAlign:'right',whiteSpace:'nowrap'}}>
              <span style={{fontWeight:600,color:'#18181b'}}>{_fmtN(s.count)}</span>
              {i>0 && <span style={{..._OTX.meta,marginLeft:7}}>{conv==null?'–':`${conv.toFixed(1)}%`} of prev</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// The card header's single hero rate — the one number the operator reads first.
function HeroRate({ label, pct }){
  return (
    <div style={{textAlign:'right'}}>
      <div style={{fontSize:21,fontWeight:600,color:'#18181b',fontVariantNumeric:'tabular-nums',lineHeight:1.1}}>{_fmtPct(pct)}</div>
      <div style={{..._OTX.meta,marginTop:1}}>{label}</div>
    </div>
  );
}

// Secondary counts, one quiet line — never a row of boxes.
function MetaLine({ parts }){
  return (
    <div style={{padding:'0 20px 14px',display:'flex',gap:6,flexWrap:'wrap',alignItems:'baseline'}}>
      {parts.filter(Boolean).map((p,i)=>(
        <span key={i} style={{..._OTX.meta,color:p.tone||'#a1a1aa'}}>
          {i>0&&<span style={{margin:'0 5px',color:'#e4e4e7'}}>·</span>}
          <span style={{fontVariantNumeric:'tabular-nums',fontWeight:500,color:p.tone||'#71717a'}}>{p.value}</span> {p.label}
        </span>
      ))}
    </div>
  );
}

// ── LinkedIn connects trend (single series -> no legend; crosshair tooltip) ──
function TrendChart({ trend }){
  const [hov,setHov]=useState(null);
  const wrapRef=useRef(null);
  const series=(trend||[]).map(d=>({date:d.date,v:d.connectionsSent||0}));
  const hasData=series.some(d=>d.v>0);
  if(!hasData) return null;
  const W=560,H=96,PX=6,PY=8;
  const max=Math.max(...series.map(d=>d.v),1);
  const x=(i)=>PX+(series.length<2?0:(i*(W-2*PX)/(series.length-1)));
  const y=(v)=>H-PY-(v/max)*(H-2*PY);
  const path=series.map((d,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(d.v).toFixed(1)}`).join(' ');
  const area=`${path} L${x(series.length-1).toFixed(1)},${H-PY} L${x(0).toFixed(1)},${H-PY} Z`;
  return (
    <div style={{padding:'14px 20px 6px',borderTop:'1px solid #f4f4f5'}}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline'}}>
        <div style={_OTX.label}>Connection requests per day</div>
        <div style={{..._OTX.meta,fontVariantNumeric:'tabular-nums'}}>peak {max}</div>
      </div>
      <div ref={wrapRef} style={{position:'relative',marginTop:6}}
        onMouseLeave={()=>setHov(null)}
        onMouseMove={(e)=>{ const r=wrapRef.current.getBoundingClientRect();
          const fx=(e.clientX-r.left)/r.width*W;
          const i=Math.max(0,Math.min(series.length-1,Math.round((fx-PX)/((W-2*PX)/Math.max(1,series.length-1)))));
          setHov(i); }}>
        <svg viewBox={`0 0 ${W} ${H}`} style={{width:'100%',height:'auto',display:'block'}}>
          {[0.25,0.5,0.75].map(f=><line key={f} x1={PX} x2={W-PX} y1={PY+f*(H-2*PY)} y2={PY+f*(H-2*PY)} stroke="#f4f4f5" strokeWidth="1"/>)}
          <path d={area} fill="#4f46e5" opacity="0.07"/>
          <path d={path} fill="none" stroke="#4f46e5" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"/>
          {hov!=null && <>
            <line x1={x(hov)} x2={x(hov)} y1={PY} y2={H-PY} stroke="#d4d4d8" strokeWidth="1"/>
            <circle cx={x(hov)} cy={y(series[hov].v)} r="4" fill="#4f46e5" stroke="#fff" strokeWidth="2"/>
          </>}
        </svg>
        {hov!=null && (
          <div style={{position:'absolute',left:`${(x(hov)/W)*100}%`,top:-4,transform:`translate(${hov>series.length/2?'-105%':'8px'},0)`,background:'#18181b',color:'#fff',borderRadius:6,padding:'4px 8px',fontSize:11,whiteSpace:'nowrap',pointerEvents:'none'}}>
            <span style={{opacity:.65}}>{series[hov].date}</span>{' '}
            <span style={{fontWeight:600,fontVariantNumeric:'tabular-nums'}}>{series[hov].v} sent</span>
          </div>
        )}
      </div>
      <div style={{display:'flex',justifyContent:'space-between',marginTop:2}}>
        <span style={_OTX.meta}>{series[0].date}</span>
        <span style={_OTX.meta}>{series[series.length-1].date}</span>
      </div>
    </div>
  );
}

// ── channel cards ─────────────────────────────────────────────────────────────
function ChannelShell({ title, subtitle, chip, children }){
  return (
    <div style={{background:'#fff',borderRadius:14,boxShadow:'0 0 0 1px #f0f0f1,0 1px 2px rgba(0,0,0,.03)',overflow:'hidden'}}>
      <div style={{display:'flex',alignItems:'center',gap:10,padding:'16px 20px',borderBottom:'1px solid #f4f4f5'}}>
        <div style={{minWidth:0}}>
          <div style={{..._OTX.strong,fontSize:15}}>{title}</div>
          <div style={{..._OTX.meta,marginTop:2}}>{subtitle}</div>
        </div>
        <div style={{marginLeft:'auto'}}>{chip}</div>
      </div>
      {children}
    </div>
  );
}

function SetupNote({ children }){
  return (
    <div style={{margin:'16px 20px',padding:'12px 14px',background:'#fffbeb',border:'1px solid #fde68a',borderRadius:10,fontSize:12.5,color:'#92400e',lineHeight:1.5}}>
      {children}
    </div>
  );
}

function ZeroNote({ children }){
  return <div style={{padding:'26px 20px',textAlign:'center'}}><div style={_OTX.body}>{children}</div></div>;
}

const _ECOLS='minmax(0,1.6fr) 84px 64px 72px 88px 88px 88px 76px';
function EmailCard({ email }){
  if(!email) return null;
  if(!email.configured) return (
    <ChannelShell title="Email · SmartLead" subtitle="Cold email sequences" chip={<OChip fg='#b45309' bg='#fffbeb' ring='#fde68a'>Not connected</OChip>}>
      <SetupNote>SmartLead is not connected yet. Add <b>SMARTLEAD_API_KEY</b> to the API service environment (the key lives in SmartLead → Settings → API) and this card fills itself in.</SetupNote>
    </ChannelShell>
  );
  if(email.error) return (
    <ChannelShell title="Email · SmartLead" subtitle="Cold email sequences" chip={<OChip fg='#be123c' bg='#fff1f2' ring='#fecdd3'>Fetch failed</OChip>}>
      <SetupNote>SmartLead responded with an error: {email.error}. Usually a revoked or mistyped API key.</SetupNote>
    </ChannelShell>
  );
  const o=email.overall||{};
  const anySends=(o.sent||0)>0;
  const bounceTone=o.bounce_rate==null?null:(o.bounce_rate>5?'#e11d48':o.bounce_rate>2?'#b45309':null);
  const rows=email.campaigns||[];
  return (
    <ChannelShell title="Email · SmartLead" subtitle={`${rows.length} campaign${rows.length===1?'':'s'} · ${_fmtN(o.leads)} leads loaded`}
      chip={<div style={{display:'flex',alignItems:'center',gap:16}}>
        {anySends?<OChip fg='#047857' bg='#ecfdf5' ring='#a7f3d0'>Sending</OChip>:<OChip fg='#71717a' bg='#fafafa' ring='#e4e4e7'>No sends yet</OChip>}
        <HeroRate label="reply rate" pct={o.reply_rate}/>
      </div>}>
      <Funnel stages={[
        {label:'Sent', count:o.sent||0},
        {label:'Opened', count:o.opens||0},
        {label:'Clicked', count:o.clicks||0},
        {label:'Replied', count:o.replies||0},
      ]}/>
      <MetaLine parts={[
        {value:_fmtN(o.interested), label:'interested', tone:(o.interested||0)>0?'#047857':undefined},
        {value:_fmtPct(o.bounce_rate), label:`bounce rate (${_fmtN(o.bounces)})`, tone:bounceTone||undefined},
        {value:_fmtN(o.unsubscribes), label:'unsubscribed'},
        {value:_fmtPct(o.open_rate), label:'open rate'},
        {value:_fmtPct(o.click_rate), label:'click rate'},
      ]}/>
      {!anySends && <ZeroNote>Campaigns are loaded but nothing has sent yet; rates appear with the first send.</ZeroNote>}
      {rows.length>0 && (
        <div>
          <div style={{display:'grid',gridTemplateColumns:_ECOLS,gap:14,padding:'8px 20px',borderTop:'1px solid #f4f4f5',borderBottom:'1px solid #f4f4f5',background:'#fafafa'}}>
            <div style={_OTX.label}>Campaign</div><div style={_OTX.label}>Status</div>
            <div style={_OTX.label}>Leads</div><div style={_OTX.label}>Sent</div>
            <div style={_OTX.label}>Open</div><div style={_OTX.label}>Click</div>
            <div style={_OTX.label}>Reply</div><div style={_OTX.label}>Interested</div>
          </div>
          {rows.map(r=>(
            <div key={r.id} style={{display:'grid',gridTemplateColumns:_ECOLS,gap:14,alignItems:'center',padding:'10px 20px',borderBottom:'1px solid #f8f8f8'}}>
              <div style={{..._OTX.body,color:'#3f3f46',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}} title={r.name}>{r.name}</div>
              <div><OStatusChip status={r.status}/></div>
              <div style={{..._OTX.body,fontVariantNumeric:'tabular-nums'}}>{_fmtN(r.leads)}</div>
              <div style={{..._OTX.body,fontVariantNumeric:'tabular-nums'}}>{_fmtN(r.sent)}</div>
              <RateCell pct={r.open_rate}/>
              <RateCell pct={r.click_rate}/>
              <RateCell pct={r.reply_rate}/>
              <div style={{..._OTX.body,fontVariantNumeric:'tabular-nums',color:r.interested>0?'#047857':'#52525b'}}>{_fmtN(r.interested)}</div>
            </div>
          ))}
        </div>
      )}
    </ChannelShell>
  );
}

const _LCOLS='minmax(0,1.6fr) 84px 96px 92px 96px 92px 76px';
function LinkedInCard({ linkedin }){
  if(!linkedin) return null;
  if(!linkedin.configured) return (
    <ChannelShell title="LinkedIn · HeyReach" subtitle="Connect + message sequences" chip={<OChip fg='#b45309' bg='#fffbeb' ring='#fde68a'>Not connected</OChip>}>
      <SetupNote>HeyReach is not connected yet. Set <b>HEYREACH_API_KEY</b> on the API service and the LinkedIn stats fill in.</SetupNote>
    </ChannelShell>
  );
  if(linkedin.error) return (
    <ChannelShell title="LinkedIn · HeyReach" subtitle="Connect + message sequences" chip={<OChip fg='#be123c' bg='#fff1f2' ring='#fecdd3'>Fetch failed</OChip>}>
      <SetupNote>HeyReach responded with an error: {linkedin.error}. If it says the key is invalid, the key was rotated; update <b>HEYREACH_API_KEY</b> in the service environment.</SetupNote>
    </ChannelShell>
  );
  const o=linkedin.overall||{};
  const anyActivity=(o.connections_sent||0)>0||(o.messages_sent||0)>0;
  const rows=linkedin.campaigns||[];
  return (
    <ChannelShell title="LinkedIn · HeyReach" subtitle={`${rows.length} campaign${rows.length===1?'':'s'} · ${_fmtN(o.leads_contacted)} leads contacted`}
      chip={<div style={{display:'flex',alignItems:'center',gap:16}}>
        {anyActivity?<OChip fg='#047857' bg='#ecfdf5' ring='#a7f3d0'>Active</OChip>:<OChip fg='#71717a' bg='#fafafa' ring='#e4e4e7'>No activity yet</OChip>}
        <HeroRate label="accept rate" pct={o.accept_rate}/>
      </div>}>
      <Funnel stages={[
        {label:'Connects', count:o.connections_sent||0},
        {label:'Accepted', count:o.connections_accepted||0},
        {label:'Replied', count:o.message_replies||0},
      ]}/>
      <MetaLine parts={[
        {value:_fmtN(o.messages_sent), label:'messages sent'},
        {value:_fmtPct(o.message_reply_rate), label:'message reply rate'},
        {value:_fmtN(o.interested), label:'interested (auto-tagged)', tone:(o.interested||0)>0?'#047857':undefined},
        {value:_fmtN(o.profile_views), label:'profile views'},
        (o.inmails_sent||0)>0?{value:_fmtN(o.inmails_sent), label:`InMails (${_fmtPct(o.inmail_reply_rate)} reply)`}:null,
      ]}/>
      <TrendChart trend={linkedin.trend}/>
      {!anyActivity && <ZeroNote>Senders are connected but no outreach has gone out yet; accept and reply rates appear with the first connection requests.</ZeroNote>}
      {rows.length>0 && (
        <div>
          <div style={{display:'grid',gridTemplateColumns:_LCOLS,gap:14,padding:'8px 20px',borderTop:'1px solid #f4f4f5',borderBottom:'1px solid #f4f4f5',background:'#fafafa'}}>
            <div style={_OTX.label}>Campaign</div><div style={_OTX.label}>Status</div>
            <div style={_OTX.label}>Connects</div><div style={_OTX.label}>Accept</div>
            <div style={_OTX.label}>Messages</div><div style={_OTX.label}>Reply</div>
            <div style={_OTX.label}>Interested</div>
          </div>
          {rows.map(r=>(
            <div key={r.id} style={{display:'grid',gridTemplateColumns:_LCOLS,gap:14,alignItems:'center',padding:'10px 20px',borderBottom:'1px solid #f8f8f8'}}>
              <div style={{..._OTX.body,color:'#3f3f46',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}} title={r.name}>{r.name}</div>
              <div><OStatusChip status={r.status}/></div>
              <div style={{..._OTX.body,fontVariantNumeric:'tabular-nums'}}>{_fmtN(r.connections_sent)}</div>
              <RateCell pct={r.accept_rate}/>
              <div style={{..._OTX.body,fontVariantNumeric:'tabular-nums'}}>{_fmtN(r.messages_sent)}</div>
              <RateCell pct={r.message_reply_rate}/>
              <div style={{..._OTX.body,fontVariantNumeric:'tabular-nums',color:r.interested>0?'#047857':'#52525b'}}>{_fmtN(r.interested)}</div>
            </div>
          ))}
        </div>
      )}
    </ChannelShell>
  );
}

// ── the page ──────────────────────────────────────────────────────────────────
function OutreachPage(){
  const [data,setData]=useState(null);
  const [loading,setLoading]=useState(true);
  const [refreshing,setRefreshing]=useState(false);
  const [err,setErr]=useState(null);

  function load(refresh){
    (refresh?setRefreshing:setLoading)(true);
    return window.API.outreachStats(refresh)
      .then(d=>{ setData(d); setErr(null); })
      .catch(e=>setErr(String(e.message||e)))
      .finally(()=>{ setLoading(false); setRefreshing(false); });
  }
  useEffect(()=>{ load(false); },[]);

  const updated=useMemo(()=>{
    if(!data||!data.fetched_at) return null;
    const m=Math.round((Date.now()-new Date(data.fetched_at).getTime())/60000);
    if(m<2) return 'just now';
    if(m<60) return `${m}m ago`;
    const h=Math.round(m/60);
    return h<24?`${h}h ago`:`${Math.round(h/24)}d ago`;
  },[data]);

  return (
    <div style={{maxWidth:1120,margin:'0 auto',padding:'20px 24px 60px',display:'flex',flexDirection:'column',gap:16}}>
      <div style={{display:'flex',alignItems:'center',gap:10}}>
        <div>
          <div style={{fontSize:17,fontWeight:600,color:'#18181b'}}>Outreach performance</div>
          <div style={{..._OTX.meta,marginTop:2}}>Live campaign stats from SmartLead (email) and HeyReach (LinkedIn). Refreshes on its own every 10 minutes.</div>
        </div>
        <div style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:10}}>
          {updated && <span style={_OTX.meta}>updated {updated}{data&&data.cached?' · cached':''}</span>}
          <button onClick={()=>load(true)} disabled={refreshing}
            style={{background:'#fff',border:'1px solid #e4e4e7',borderRadius:7,padding:'6px 14px',fontSize:12.5,fontWeight:500,color:refreshing?'#a1a1aa':'#3f3f46',cursor:refreshing?'default':'pointer'}}>
            {refreshing?'Refreshing…':'Refresh'}
          </button>
        </div>
      </div>
      {err && <div style={{padding:'12px 14px',background:'#fff1f2',border:'1px solid #fecdd3',borderRadius:10,fontSize:12.5,color:'#be123c'}}>Could not load outreach stats: {err}</div>}
      {loading
        ? <div style={{padding:'60px 0',textAlign:'center',..._OTX.body}}>Loading outreach stats…</div>
        : data && <>
            <EmailCard email={data.email}/>
            <LinkedInCard linkedin={data.linkedin}/>
          </>}
    </div>
  );
}

window.OutreachPage = OutreachPage;
