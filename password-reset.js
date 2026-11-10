let csrfToken='';
const form=document.querySelector('#password-reset-form');
const message=document.querySelector('#password-reset-message');
function tell(text,error=false){message.textContent=text;message.classList.add('visible');message.classList.toggle('error',error)}
async function csrf(){const response=await fetch('/api/v1/auth/csrf',{credentials:'same-origin'});const data=await response.json();csrfToken=data.csrf_token}
async function api(path,options={}){const headers={...(options.headers||{})};if(options.method&&options.method!=='GET')headers['X-CSRF-Token']=csrfToken;if(options.body){headers['Content-Type']='application/json';options.body=JSON.stringify(options.body)}const response=await fetch(path,{...options,headers,credentials:'same-origin'});const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||'Не удалось выполнить запрос.');return data}
form.addEventListener('submit',async event=>{event.preventDefault();const data=Object.fromEntries(new FormData(form));if(data.new_password!==data.confirm_password){tell('Новые пароли не совпадают.',true);return}delete data.confirm_password;const button=document.querySelector('#password-reset-submit');button.disabled=true;try{await api('/api/v1/auth/password',{method:'POST',body:data});tell('Пароль обновлён. Открываю личный кабинет…');setTimeout(()=>location.href='/cabinet',600)}catch(error){tell(error.message,true)}finally{button.disabled=false}});
document.querySelector('#password-reset-logout').addEventListener('click',async event=>{event.preventDefault();try{await api('/api/v1/auth/logout',{method:'POST',body:{}});location.href='/login'}catch(error){tell(error.message,true)}});
csrf().catch(error=>tell(error.message,true));
