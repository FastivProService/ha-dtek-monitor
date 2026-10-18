/* Small form double; actual HA selectors are also checked on a running HA. */
customElements.define('ha-form',class extends HTMLElement{
 constructor(){super();this.attachShadow({mode:'open'});}
 set schema(value){this._schema=value;this.render();}
 set data(value){this._data=value;for(const input of this.shadowRoot.querySelectorAll('input')){input.value=value[input.name]??'';input.checked=!!value[input.name];}}
 set computeLabel(value){this._label=value;this.render();}
 set disabled(value){for(const input of this.shadowRoot.querySelectorAll('input'))input.disabled=value;}
 reportValidity(){return true;}
 render(){
  this.shadowRoot.replaceChildren();
  for(const f of this._schema||[]){const label=document.createElement('label'),input=document.createElement('input');label.textContent=this._label?.(f)||f.name;input.name=f.name;input.type=f.selector?.boolean?'checkbox':'text';input.value=this._data?.[f.name]??f.default??'';input.checked=this._data?.[f.name]??f.default??false;input.oninput=()=>this.dispatchEvent(new CustomEvent('value-changed',{detail:{value:{...this._data,[f.name]:input.type==='checkbox'?input.checked:input.value}}}));label.append(input);this.shadowRoot.append(label);}
 }
});
window.apiCalls=[];window.optionAction='';window.optionPosts=0;
const oldWS=hass.callWS;
hass.callWS=async m=>{
 if(m.type==='dtek_monitor/account_options'){calls.push(m);optionAction=m.action;return {flow_id:'form-'+m.entity_id,entry_id:'connection-1'};}
 if(m.type==='config_entries/get_single')return {config_entry:{state:'loaded'}};
 return oldWS(m);
};
hass.loadBackendTranslation=async category=> (key,values={})=>{
 if(!key.startsWith(`component.dtek_monitor.${category}.`))return undefined;
 let value=window.optionTranslations;
 for(const part of key.replace('component.dtek_monitor.','').split('.'))value=value?.[part];
 return typeof value==='string'?value.replace(/\{(\w+)\}/g,(_,key)=>values[key]??''):undefined;
};
window.optionsStep=()=>({type:'form',step_id:'account_reminder_settings',flow_id:'form',data_schema:[{name:'enabled',selector:{boolean:{}},default:false},{name:'time',selector:{time:{}},default:'19:00:00'},{name:'include_estimate',selector:{boolean:{}},default:true}],errors:{}});
hass.callApi=async(method,path,data)=>{
 apiCalls.push({method,path,data:structuredClone(data)});
 if(method==='GET')return optionsStep();
 if(method==='DELETE')return {};
 optionPosts++;return {type:'create_entry'};
};

void 0;
