const API = import.meta.env.VITE_API_URL || 'http://localhost:8000/api'
export function token(){return localStorage.getItem('token')}
async function request(path, options={}){
  const headers = {...(options.headers||{})}
  if(!(options.body instanceof FormData)) headers['Content-Type']='application/json'
  if(token()) headers.Authorization=`Bearer ${token()}`
  const res=await fetch(`${API}${path}`,{...options,headers})
  if(!res.ok){let msg='Request failed'; try{const j=await res.json();msg=j.detail||msg}catch{};throw new Error(msg)}
  const ct=res.headers.get('content-type')||''
  return ct.includes('application/json')?res.json():res.text()
}
export const api={
 register:(data)=>request('/auth/register',{method:'POST',body:JSON.stringify(data)}),
 login:(data)=>request('/auth/login',{method:'POST',body:JSON.stringify(data)}),
 me:()=>request('/auth/me'),
 meetings:(q='')=>request(`/meetings${q?`?q=${encodeURIComponent(q)}`:''}`),
 meeting:(id)=>request(`/meetings/${id}`),
 upload:(form)=>request('/meetings',{method:'POST',body:form}),
 updateAction:(id,data)=>request(`/action-items/${id}`,{method:'PATCH',body:JSON.stringify(data)}),
 remove:(id)=>request(`/meetings/${id}`,{method:'DELETE'}),
 audio:(id)=>{const t=token(); return `${API}/meetings/${id}/audio${t?`?token=${encodeURIComponent(t)}`:''}`},
 exportUrl:(id)=>{const t=token(); return `${API}/meetings/${id}/export.txt${t?`?token=${encodeURIComponent(t)}`:''}`}
}
