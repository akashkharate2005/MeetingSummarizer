import React,{useEffect,useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Upload, Search, LogOut, FileAudio, CheckCircle2, Clock3, AlertCircle, Download, Trash2, ArrowLeft, Users, FileText} from 'lucide-react'
import {api,token} from './lib/api'
import './styles.css'

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/>
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
    </svg>
  );
}

function Auth({onAuth}){
  const [register,setRegister]=useState(false);
  const [form,setForm]=useState({name:'',email:'',password:''});
  const [error,setError]=useState('');
  const [googleLoading,setGoogleLoading]=useState(false);

  async function submit(e){
    e.preventDefault();
    setError('');
    try {
      const r = register ? await api.register(form) : await api.login({email:form.email,password:form.password});
      localStorage.setItem('token',r.access_token);
      onAuth(r.user);
    } catch(e) {
      setError(e.message);
    }
  }

  async function handleGoogleLogin(){
    setError('');
    setGoogleLoading(true);
    try {
      const res = await api.googleAuthUrl();
      if (res && res.url) {
        window.location.href = res.url;
      }
    } catch(e) {
      setError(e.message || 'Google OAuth is not configured on the server. Please add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env.');
    } finally {
      setGoogleLoading(false);
    }
  }

  return (
    <div className="auth">
      <div className="auth-card">
        <div className="brand">Meet<span>Mind</span></div>
        <h1>{register?'Create account':'Welcome back'}</h1>
        <p className="muted">AI-powered meeting intelligence.</p>

        <button type="button" className="google-btn" onClick={handleGoogleLogin} disabled={googleLoading}>
          <GoogleIcon/>
          <span>{googleLoading ? 'Connecting to Google…' : 'Continue with Google'}</span>
        </button>

        <div className="auth-divider">
          <span>or continue with email</span>
        </div>

        <form onSubmit={submit}>
          {register && (
            <input
              placeholder="Full name"
              required
              value={form.name}
              onChange={e=>setForm({...form,name:e.target.value})}
            />
          )}
          <input
            type="email"
            placeholder="Email"
            required
            value={form.email}
            onChange={e=>setForm({...form,email:e.target.value})}
          />
          <input
            type="password"
            placeholder="Password"
            required
            value={form.password}
            onChange={e=>setForm({...form,password:e.target.value})}
          />
          {error && <div className="error">{error}</div>}
          <button className="primary">{register?'Create account':'Sign in'}</button>
        </form>

        <button className="link" onClick={()=>setRegister(!register)}>
          {register?'Already have an account? Sign in':'New here? Create an account'}
        </button>
      </div>
    </div>
  );
}

function App(){
  const [user,setUser]=useState(null);
  const [selected,setSelected]=useState(null);
  const [meetings,setMeetings]=useState([]);
  const [q,setQ]=useState('');
  const [uploading,setUploading]=useState(false);
  const [error,setError]=useState('');
  const MAX_UPLOAD_MB=500;
  const MAX_UPLOAD_BYTES=MAX_UPLOAD_MB*1024*1024;

  useEffect(()=>{
    const params = new URLSearchParams(window.location.search);
    const oauthToken = params.get('token');
    if (oauthToken) {
      localStorage.setItem('token', oauthToken);
      window.history.replaceState({}, document.title, window.location.pathname);
    }
    if (token()) {
      api.me().then(setUser).catch(()=>localStorage.removeItem('token'));
    }
  },[]);

  useEffect(()=>{if(user) load()},[user,q]);

  async function load(){try{setMeetings(await api.meetings(q))}catch(e){setError(e.message)}}
  if(!user)return <Auth onAuth={setUser}/>
  if(selected)return <Detail meetingId={selected} back={()=>{setSelected(null);load()}}/>
  async function upload(e){const file=e.target.files?.[0];if(!file)return;setError('');if(file.size===0){setError('The selected file is empty.');e.target.value='';return}if(file.size>MAX_UPLOAD_BYTES){setError(`File is too large. Maximum allowed size is ${MAX_UPLOAD_MB} MB.`);e.target.value='';return}setUploading(true);const fd=new FormData();fd.append('file',file);fd.append('title',file.name.replace(/\.[^.]+$/,''));try{const m=await api.upload(fd);setSelected(m.id);load()}catch(e){setError(e.message)}finally{setUploading(false);e.target.value=''}}
  return <div className="app"><header><div className="brand">Meet<span>Mind</span></div><div className="header-right"><span>{user.name}</span><button className="icon" onClick={()=>{localStorage.removeItem('token');setUser(null)}}><LogOut size={18}/></button></div></header><main><section className="hero"><div><p className="eyebrow">MEETING INTELLIGENCE</p><h1>Turn conversations into<br/><em>clear next steps.</em></h1><p className="hero-copy">Upload a meeting recording. MeetMind transcribes it, extracts decisions, and turns commitments into trackable action items.</p></div><label className="upload"><input type="file" accept="audio/*,.m4a,.flac" onChange={upload}/><Upload size={22}/><strong>{uploading?'Uploading…':'Upload meeting'}</strong><small>MP3, WAV, M4A, FLAC · up to 500 MB</small></label></section>{error&&<div className="error banner">{error}</div>}<div className="toolbar"><h2>Your meetings</h2><div className="search"><Search size={17}/><input placeholder="Search meetings" value={q} onChange={e=>setQ(e.target.value)}/></div></div><div className="grid">{meetings.length?meetings.map(m=><MeetingCard key={m.id} m={m} onClick={()=>setSelected(m.id)}/>):<div className="empty"><FileAudio size={40}/><h3>No meetings yet</h3><p>Upload your first recording to get a transcript and summary.</p></div>}</div></main></div>
}
function formatDuration(sec){
 if(!sec) return null;
 const m=Math.floor(sec/60), s=Math.floor(sec%60);
 if(m>=60){
   const h=Math.floor(m/60), rm=m%60;
   return `${h}h ${rm}m ${s}s`;
 }
 return `${m}m ${s}s`;
}

function MeetingCard({m,onClick}){
 const isDone = m.status==='completed'||m.status==='completed_with_warnings';
 const Icon=m.status==='completed'?CheckCircle2:m.status==='completed_with_warnings'?AlertCircle:m.status==='failed'?AlertCircle:Clock3;
 const durStr = formatDuration(m.duration_seconds);
 return <button className="card" onClick={onClick}>
   <div className="card-top">
     <span className={`status ${m.status}`}><Icon size={14}/>{m.status.replace(/_/g,' ')}</span>
     <span>{new Date(m.meeting_date).toLocaleDateString()}</span>
   </div>
   <h3>{m.title}</h3>
   <div className="tags">
     {durStr && <span className="duration-tag">{durStr}</span>}
     {(m.tags||[]).map(t=><span key={t}>{t}</span>)}
   </div>
   <div className="card-foot">
     {isDone?'Summary ready':m.status==='failed'?'Processing failed':'Processing in background'} <span>→</span>
   </div>
 </button>
}

function Detail({meetingId,back}){
 const [m,setM]=useState(null),[err,setErr]=useState('');
 useEffect(()=>{
   let id=setInterval(()=>{
     api.meeting(meetingId).then(x=>{
       setM(x);
       if(['completed','completed_with_warnings','failed'].includes(x.status)) clearInterval(id);
     }).catch(e=>setErr(e.message))
   }, 1500);
   return()=>clearInterval(id);
 },[meetingId]);

 if(!m)return <div className="loading">Loading meeting…</div>;

 const isDone = m.status==='completed'||m.status==='completed_with_warnings';
 const durStr = formatDuration(m.duration_seconds);

 return <div className="app">
   <header>
     <button className="back" onClick={back}><ArrowLeft size={18}/> All meetings</button>
     <div className="brand">Meet<span>Mind</span></div>
     <div/>
   </header>
   <main className="detail">
     <div className="detail-head">
       <div>
         <p className="eyebrow">MEETING</p>
         <h1>{m.title}</h1>
         <p className="muted">
           {new Date(m.meeting_date).toLocaleString()}
           {durStr && ` · ${durStr}`}
           {` · `}<b style={{textTransform:'capitalize'}}>{m.status.replace(/_/g,' ')}</b>
         </p>
       </div>
        <div className="actions">
          <a className="secondary" href={api.exportUrl(m.id)} download={`meeting_${m.id}_export.txt`}><Download size={17}/> Export</a>
          <button className="secondary" onClick={async()=>{if(confirm('Delete this meeting?')){await api.remove(m.id);back()}}}><Trash2 size={17}/></button>
        </div>
     </div>

     {m.status==='completed_with_warnings'&&m.error_message&&
       <div className="error banner" style={{background:'#fff9e6', borderColor:'#f0dfa8', color:'#7d5e18'}}>
         <b>Notice:</b> Some audio segments could not be transcribed cleanly ({m.error_message})
       </div>
     }

     {!isDone && m.status!=='failed' ? (
       <div className="processing">
         <div className="spinner"/>
         <h2>Working on your meeting…</h2>
         <p>We are transcribing the audio and generating the structured summary. This page updates automatically.</p>
       </div>
     ) : m.status==='failed' ? (
       <div className="error banner">Processing failed: {m.error_message||'Unknown error'}</div>
     ) : (
       <>
         <div className="summary-layout">
           <section className="panel summary">
             <h2>Executive summary</h2>
             <p>{m.summary?.summary_text || 'No summary available.'}</p>
             <h3>Key decisions</h3>
             <ul>
               {(m.summary?.decisions||[]).length > 0 ? (
                 m.summary.decisions.map((x,i)=><li key={i}>{x}</li>)
               ) : (
                 <li style={{color:'#8a8d86', listStyleType:'none'}}>No explicit decisions identified.</li>
               )}
             </ul>
           </section>
           <section className="panel">
             <h2>Action items</h2>
             {(m.summary?.action_items||[]).length > 0 ? (
               m.summary.action_items.map(a=><Action key={a.id} item={a}/>)
             ) : (
               <p style={{color:'#8a8d86'}}>No action items identified.</p>
             )}
           </section>
         </div>
         <TranscriptSection transcript={m.transcript} meetingId={m.id}/>
       </>
     )}
   </main>
 </div>
}

function TranscriptSection({transcript, meetingId}) {
  const segments = transcript?.speaker_segments || [];
  const hasSegments = Array.isArray(segments) && segments.length > 0;
  const [view, setView] = useState(hasSegments ? 'speakers' : 'text');

  const speakerColors = [
    { bg: '#e8f0e6', text: '#2d502f', border: '#cce0c7' },
    { bg: '#e8edf5', text: '#244368', border: '#ccdaf0' },
    { bg: '#faeee2', text: '#7d4715', border: '#f2d5b6' },
    { bg: '#f5e8f0', text: '#68244e', border: '#e8c0db' },
    { bg: '#edf5f4', text: '#215c57', border: '#c3e6e3' },
  ];

  const speakerMap = {};
  let colorIdx = 0;
  if (hasSegments) {
    segments.forEach(s => {
      const spk = s.speaker || 'Speaker 1';
      if (!speakerMap[spk]) {
        speakerMap[spk] = speakerColors[colorIdx % speakerColors.length];
        colorIdx++;
      }
    });
  }

  return (
    <section className="panel transcript">
      <div className="transcript-head">
        <h2>Recording & Transcript</h2>
        {hasSegments && (
          <div className="view-toggle">
            <button
              className={`toggle-btn ${view === 'speakers' ? 'active' : ''}`}
              onClick={() => setView('speakers')}
            >
              <Users size={14}/> Speaker View
            </button>
            <button
              className={`toggle-btn ${view === 'text' ? 'active' : ''}`}
              onClick={() => setView('text')}
            >
              <FileText size={14}/> Plain Text
            </button>
          </div>
        )}
      </div>

      <audio controls preload="metadata" src={api.audio(meetingId)} style={{width:'100%', marginBottom:'20px'}}/>

      {view === 'speakers' && hasSegments ? (
        <div className="speaker-segments">
          {segments.map((seg, idx) => {
            const spk = seg.speaker || 'Speaker 1';
            const colors = speakerMap[spk] || speakerColors[0];
            const initials = spk.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase() || 'S';
            return (
              <div key={idx} className="speaker-turn">
                <div className="speaker-avatar" style={{ backgroundColor: colors.bg, color: colors.text, borderColor: colors.border }}>
                  {initials}
                </div>
                <div className="speaker-body">
                  <div className="speaker-meta">
                    <span className="speaker-name" style={{ color: colors.text }}>{spk}</span>
                    {seg.timestamp && <span className="speaker-time">{seg.timestamp}</span>}
                  </div>
                  <div className="speaker-bubble">
                    {seg.text}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="transcript-text">{transcript?.text || 'No transcript available.'}</div>
      )}
    </section>
  );
}

function Action({item}){const [done,setDone]=useState(item.completed);async function toggle(){const next=!done;setDone(next);try{await api.updateAction(item.id,{completed:next,status:next?'completed':'open'})}catch{setDone(!next)}}return <div className={`action ${done?'done':''}`}><button onClick={toggle}><CheckCircle2 size={20}/></button><div><b>{item.description}</b><small>{item.owner||'Unassigned'} · {item.due_date||'No due date'}</small></div></div>}
createRoot(document.getElementById('root')).render(<App/>)
