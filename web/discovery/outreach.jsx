const { useState, useEffect, useMemo, useRef } = React;

// ── Outreach dashboard ────────────────────────────────────────────────────────
// Cross-channel outreach performance for the operator: SmartLead email stats
// (sent / open / click / reply / bounce + interested) and HeyReach LinkedIn
// stats (connects sent / accepted, message replies, trend), overall and per
// campaign. Reads /api/outreach/stats (server-cached ~10 min; Refresh forces
// a live pull). Rates arrive server-computed from raw counts; a dash means
// "nothing sent yet", never a fake 0%.
//
// Structure: shared primitives (chips, cells, Funnel, MetaLine, StatTable,
// ChannelCard gate) + one thin config-driven card per channel. Adding a
// channel = a config + a card function; no copied grid or state blocks.

const _OTX = {
  strong:{fontSize:14,fontWeight:500,color:'#18181b',lineHeight:1.4},
  body:{fontSize:13,fontWeight:400,color:'#52525b',lineHeight:1.4},
  meta:{fontSize:12,fontWeight:400,color:'#a1a1aa',lineHeight:1.3},
  label:{fontSize:11,fontWeight:600,color:'#a1a1aa',textTransform:'uppercase',letterSpacing:'.06em'},
};

const _fmtN = (n)=> (n==null?'–':Number(n).toLocaleString('en-US'));
const _fmtPct = (p)=> (p==null?'–':`${p.toFixed(1)}%`);

// ── chips ─────────────────────────────────────────────────────────────────────

function OChip({ fg, bg, ring, children }){
  return <span style={{display:'inline-flex',alignItems:'center',gap:4,borderRadius:6,padding:'1px 7px',fontSize:11,fontWeight:500,color:fg,background:bg,boxShadow:`inset 0 0 0 1px ${ring}`,whiteSpace:'nowrap'}}>{children}</span>;
}

const _CHIP_TONES = {
  good:  {fg:'#047857',bg:'#ecfdf5',ring:'#a7f3d0'},
  idle:  {fg:'#71717a',bg:'#fafafa',ring:'#e4e4e7'},
  warn:  {fg:'#b45309',bg:'#fffbeb',ring:'#fde68a'},
  bad:   {fg:'#be123c',bg:'#fff1f2',ring:'#fecdd3'},
  info:  {fg:'#0369a1',bg:'#f0f9ff',ring:'#bae6fd'},
};
function ToneChip({ tone, children }){ return <OChip {..._CHIP_TONES[tone]}>{children}</OChip>; }

const _STATUS_TONE = {
  ACTIVE:['good','Active'], IN_PROGRESS:['good','Running'], START:['good','Running'],
  DRAFTED:['idle','Draft'], DRAFT:['idle','Draft'], PAUSED:['warn','Paused'],
  STOPPED:['bad','Stopped'], CANCELED:['bad','Canceled'],
  COMPLETED:['info','Completed'], FINISHED:['info','Finished'],
};
function OStatusChip({ status }){
  const [tone,label]=_STATUS_TONE[String(status||'').toUpperCase()]||['idle',status||'?'];
  return <ToneChip tone={tone}>{label}</ToneChip>;
}

// ── cells (the only building blocks StatTable columns use) ───────────────────

function NameCell({ text }){
  return <div style={{..._OTX.body,color:'#3f3f46',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}} title={text}>{text}</div>;
}
function NumCell({ v, tone }){
  return <div style={{..._OTX.body,fontVariantNumeric:'tabular-nums',color:tone||'#52525b'}}>{_fmtN(v)}</div>;
}
// A rate cell: the number in ink + a thin 4px meter (text never wears the color).
function RateCell({ pct }){
  return (
    <div style={{minWidth:0}}>
      <div style={{..._OTX.body,fontVariantNumeric:'tabular-nums',color:'#3f3f46'}}>{_fmtPct(pct)}</div>
      <div style={{height:4,borderRadius:4,background:'#f4f4f5',marginTop:3,overflow:'hidden'}}>
        {pct!=null && <div style={{height:'100%',width:`${Math.min(100,pct)}%`,borderRadius:4,background:'#4f46e5'}}/>}
      </div>
    </div>
  );
}

// ── shared blocks ─────────────────────────────────────────────────────────────

// The channel funnel: each stage a thin bar sized against the FIRST stage,
// count directly labeled, conversion at the right. A stage's conversion
// denominator defaults to the previous stage; pass `vs` (a stage index) when
// the metric is not a subset of its neighbor (email replies come from sends,
// not from clicks — QA, 2026-07-14).
function Funnel({ stages }){
  const base=Math.max(stages.length?stages[0].count:0,1);
  return (
    <div style={{padding:'14px 20px 12px',display:'flex',flexDirection:'column',gap:9}}>
      {stages.map((s,i)=>{
        const vsIdx=s.vs!=null?s.vs:i-1;
        const denom=i?stages[vsIdx].count:null;
        const conv=i?(denom>0?(100*s.count/denom):null):null;
        const vsLabel=i?(s.vs!=null?`of ${stages[vsIdx].label.toLowerCase()}`:'of prev'):null;
        return (
          <div key={s.label} style={{display:'grid',gridTemplateColumns:'92px minmax(0,1fr) 150px',alignItems:'center',gap:14}}>
            <div style={{..._OTX.body,color:'#3f3f46'}}>{s.label}</div>
            <div style={{height:6,borderRadius:4,background:'#f4f4f5',overflow:'hidden'}}>
              <div style={{height:'100%',width:`${Math.min(100,100*s.count/base)}%`,borderRadius:4,background:'#4f46e5',transition:'width .3s ease'}}/>
            </div>
            <div style={{..._OTX.body,fontVariantNumeric:'tabular-nums',textAlign:'right',whiteSpace:'nowrap'}}>
              <span style={{fontWeight:600,color:'#18181b'}}>{_fmtN(s.count)}</span>
              {i>0 && <span style={{..._OTX.meta,marginLeft:7}}>{conv==null?'–':`${conv.toFixed(1)}%`} {vsLabel}</span>}
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

function ZeroNote({ children }){
  return <div style={{padding:'26px 20px',textAlign:'center'}}><div style={_OTX.body}>{children}</div></div>;
}

// Column-config table: cols = [{label, width, render(row)}]. Both channels
// (and any future one) share this — the grid never gets copied again.
function StatTable({ cols, rows }){
  if(!rows.length) return null;
  const grid=cols.map(c=>c.width).join(' ');
  return (
    <div>
      <div style={{display:'grid',gridTemplateColumns:grid,gap:14,padding:'8px 20px',borderTop:'1px solid #f4f4f5',borderBottom:'1px solid #f4f4f5',background:'#fafafa'}}>
        {cols.map(c=><div key={c.label} style={_OTX.label}>{c.label}</div>)}
      </div>
      {rows.map(r=>(
        <div key={r.id} style={{display:'grid',gridTemplateColumns:grid,gap:14,alignItems:'center',padding:'10px 20px',borderBottom:'1px solid #f8f8f8'}}>
          {cols.map(c=><React.Fragment key={c.label}>{c.render(r)}</React.Fragment>)}
        </div>
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

// ── the channel card gate ─────────────────────────────────────────────────────
// Owns the shell + the three states every channel has (unconfigured / fetch
// error / live). Channels never duplicate this logic — they pass copy.
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

function ChannelCard({ title, tagline, state, unconfiguredNote, errorNote, subtitle, headerRight, children }){
  if(!state) return null;
  if(!state.configured) return (
    <ChannelShell title={title} subtitle={tagline} chip={<ToneChip tone="warn">Not connected</ToneChip>}>
      <SetupNote>{unconfiguredNote}</SetupNote>
    </ChannelShell>
  );
  if(state.error) return (
    <ChannelShell title={title} subtitle={tagline} chip={<ToneChip tone="bad">Fetch failed</ToneChip>}>
      <SetupNote>{errorNote(state.error)}</SetupNote>
    </ChannelShell>
  );
  return <ChannelShell title={title} subtitle={subtitle} chip={headerRight}>{children}</ChannelShell>;
}

function HeaderRight({ activity, heroLabel, heroPct }){
  return (
    <div style={{display:'flex',alignItems:'center',gap:16}}>
      <ToneChip tone={activity.on?'good':'idle'}>{activity.on?activity.onLabel:activity.offLabel}</ToneChip>
      <HeroRate label={heroLabel} pct={heroPct}/>
    </div>
  );
}

// ── the two channels ──────────────────────────────────────────────────────────

function EmailCard({ email }){
  const o=(email&&email.overall)||{};
  const rows=(email&&email.campaigns)||[];
  const anySends=(o.sent||0)>0;
  const bounceTone=o.bounce_rate==null?null:(o.bounce_rate>5?'#e11d48':o.bounce_rate>2?'#b45309':null);
  const cols=[
    {label:'Campaign',   width:'minmax(0,1.6fr)', render:r=><NameCell text={r.name}/>},
    {label:'Status',     width:'84px', render:r=><div><OStatusChip status={r.status}/></div>},
    {label:'Leads',      width:'64px', render:r=><NumCell v={r.leads}/>},
    {label:'Sent',       width:'72px', render:r=><NumCell v={r.sent}/>},
    {label:'Open',       width:'88px', render:r=><RateCell pct={r.open_rate}/>},
    {label:'Click',      width:'88px', render:r=><RateCell pct={r.click_rate}/>},
    {label:'Reply',      width:'88px', render:r=><RateCell pct={r.reply_rate}/>},
    {label:'Interested', width:'76px', render:r=><NumCell v={r.interested} tone={r.interested>0?'#047857':undefined}/>},
  ];
  return (
    <ChannelCard title="Email · SmartLead" tagline="Cold email sequences" state={email}
      unconfiguredNote={<>SmartLead is not connected yet. Add <b>SMARTLEAD_API_KEY</b> to the API service environment (the key lives in SmartLead → Settings → API) and this card fills itself in.</>}
      errorNote={(e)=><>SmartLead responded with an error: {e}. Usually a revoked or mistyped API key.</>}
      subtitle={`${rows.length} campaign${rows.length===1?'':'s'} · ${_fmtN(o.leads)} leads loaded`}
      headerRight={<HeaderRight activity={{on:anySends,onLabel:'Sending',offLabel:'No sends yet'}} heroLabel="reply rate" heroPct={o.reply_rate}/>}>
      <Funnel stages={[
        {label:'Sent', count:o.sent||0},
        {label:'Opened', count:o.opens||0},
        {label:'Clicked', count:o.clicks||0},
        {label:'Replied', count:o.replies||0, vs:0},
      ]}/>
      <MetaLine parts={[
        {value:_fmtN(o.interested), label:'interested', tone:(o.interested||0)>0?'#047857':undefined},
        {value:_fmtPct(o.bounce_rate), label:`bounce rate (${_fmtN(o.bounces)})`, tone:bounceTone||undefined},
        {value:_fmtN(o.unsubscribes), label:'unsubscribed'},
        {value:_fmtPct(o.open_rate), label:'open rate'},
        {value:_fmtPct(o.click_rate), label:'click rate'},
        (email&&email.campaigns_errored)>0?{value:_fmtN(email.campaigns_errored), label:'campaigns failed to load (excluded from totals)', tone:'#be123c'}:null,
      ]}/>
      {!anySends && <ZeroNote>Campaigns are loaded but nothing has sent yet; rates appear with the first send.</ZeroNote>}
      <StatTable cols={cols} rows={rows}/>
    </ChannelCard>
  );
}

function LinkedInCard({ linkedin }){
  const o=(linkedin&&linkedin.overall)||{};
  const rows=(linkedin&&linkedin.campaigns)||[];
  const anyActivity=(o.connections_sent||0)>0||(o.messages_sent||0)>0;
  const cols=[
    {label:'Campaign',   width:'minmax(0,1.6fr)', render:r=><NameCell text={r.name}/>},
    {label:'Status',     width:'84px', render:r=><div><OStatusChip status={r.status}/></div>},
    {label:'Connects',   width:'96px', render:r=><NumCell v={r.connections_sent}/>},
    {label:'Accept',     width:'92px', render:r=><RateCell pct={r.accept_rate}/>},
    {label:'Messages',   width:'96px', render:r=><NumCell v={r.messages_sent}/>},
    {label:'Reply',      width:'92px', render:r=><RateCell pct={r.message_reply_rate}/>},
    {label:'Interested', width:'76px', render:r=><NumCell v={r.interested} tone={r.interested>0?'#047857':undefined}/>},
  ];
  return (
    <ChannelCard title="LinkedIn · HeyReach" tagline="Connect + message sequences" state={linkedin}
      unconfiguredNote={<>HeyReach is not connected yet. Set <b>HEYREACH_API_KEY</b> on the API service and the LinkedIn stats fill in.</>}
      errorNote={(e)=><>HeyReach responded with an error: {e}. If it mentions the key, generate a <b>workspace-level</b> API key in HeyReach and update <b>HEYREACH_API_KEY</b> in the service environment.</>}
      subtitle={`${rows.length} campaign${rows.length===1?'':'s'} · ${_fmtN(o.leads_contacted)} leads contacted`}
      headerRight={<HeaderRight activity={{on:anyActivity,onLabel:'Active',offLabel:'No activity yet'}} heroLabel="accept rate" heroPct={o.accept_rate}/>}>
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
        (linkedin&&linkedin.campaigns_errored)>0?{value:_fmtN(linkedin.campaigns_errored), label:'campaigns failed to load (excluded from totals)', tone:'#be123c'}:null,
      ]}/>
      <TrendChart trend={linkedin&&linkedin.trend}/>
      {!anyActivity && <ZeroNote>No outreach has gone out yet; accept and reply rates appear with the first connection requests.</ZeroNote>}
      <StatTable cols={cols} rows={rows}/>
    </ChannelCard>
  );
}

// ── the page ──────────────────────────────────────────────────────────────────

function _relTime(iso){
  const m=Math.round((Date.now()-new Date(iso).getTime())/60000);
  if(m<2) return 'just now';
  if(m<60) return `${m}m ago`;
  const h=Math.round(m/60);
  return h<24?`${h}h ago`:`${Math.round(h/24)}d ago`;
}

function OutreachPage(){
  const [data,setData]=useState(null);
  const [loading,setLoading]=useState(true);
  const [refreshing,setRefreshing]=useState(false);
  const [err,setErr]=useState(null);
  const [tick,setTick]=useState(0);   // re-render pulse so "updated Xm ago" ticks

  function load(refresh){
    (refresh?setRefreshing:setLoading)(true);
    return window.API.outreachStats(refresh)
      .then(d=>{ setData(d); setErr(null); })
      .catch(e=>setErr(String(e.message||e)))
      .finally(()=>{ setLoading(false); setRefreshing(false); });
  }
  // Initial load + the self-refresh the header promises (every 10 min, aligned
  // with the server cache TTL) + a 1-min clock pulse for the relative time.
  useEffect(()=>{
    load(false);
    const poll=setInterval(()=>load(false),600000);
    const clock=setInterval(()=>setTick(t=>t+1),60000);
    return ()=>{ clearInterval(poll); clearInterval(clock); };
  },[]);

  const updated=useMemo(()=>data&&data.fetched_at?_relTime(data.fetched_at):null,[data,tick]);

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
