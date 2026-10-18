import { accountCardEditor } from "./account-card-editor.js?v=1.8.0b9";
/* DTEK account history. No external assets or credentials in the browser. */
import { accountNumber } from "./account-number.js?v=1.8.0b9";
const words = {
  en: {
    title: "DTEK account history", year: "Year", refresh: "Refresh history", loading: "Loading…",
    consumption: "Monthly consumption", readings: "Meter readings", period: "Month", value: "Value",
    zone: "Tariff scale", energy: "Energy type", source: "Source", status: "Status", date: "Date (Kyiv)",
    active: "Active", inactive: "Inactive", blocked: "Blocked", unknown: "Unknown",
    missing: "No data", open: "Open month", future: "Future month", closed: "Closed month",
    fetched: "Data fetched", empty: "No readings returned for this year.", previous: "Previous", next: "Next",
    note: "Only closed months are plotted. Missing periods are gaps. Negative values are retained as reported by DTEK.",
    scope: "History belongs to the account. DTEK does not identify the meter for each old record; readings are not subtracted to calculate consumption.",
    failure: "Could not load history. Try again later.", auth: "Reauthenticate the personal account in Settings → Devices & services.",
    limited: "Please wait before trying again (at least 60 seconds after a request; longer if DTEK limits requests).",
    denied: "This account is unavailable or you do not have permission to view it.",
    stale: "Refresh failed. The previously fetched result remains displayed with its original date.",
    total: "Total", page: "Page", general: "Single zone", day: "Day", night: "Night",
  },
  uk: {
    title: "Історія особового рахунку ДТЕК", year: "Рік", refresh: "Оновити історію", loading: "Завантаження…",
    consumption: "Місячне споживання", readings: "Покази лічильника", period: "Місяць", value: "Значення",
    zone: "Тарифна шкала", energy: "Тип енергії", source: "Джерело", status: "Статус", date: "Дата (Київ)",
    active: "Активний", inactive: "Неактивний", blocked: "Заблокований", unknown: "Невідомо",
    missing: "Немає даних", open: "Незакритий місяць", future: "Майбутній місяць", closed: "Закритий місяць",
    fetched: "Дані отримано", empty: "За цей рік показів не отримано.", previous: "Назад", next: "Далі",
    note: "Графік містить лише закриті місяці. Пропуски означають відсутність даних. Від'ємні значення збережено так, як їх надає ДТЕК.",
    scope: "Історія належить рахунку. ДТЕК не вказує лічильник для кожного старого запису; різниця показів не використовується для обчислення споживання.",
    failure: "Не вдалося завантажити історію. Спробуйте пізніше.", auth: "Виконайте повторний вхід у кабінет: Налаштування → Пристрої та служби.",
    limited: "Зачекайте перед повторною спробою (щонайменше 60 секунд після запиту; довше за обмежень ДТЕК).",
    denied: "Рахунок недоступний або у вас немає дозволу на перегляд.",
    stale: "Оновлення не вдалося. Показано попередній результат із початковою датою отримання.",
    total: "Разом", page: "Сторінка", general: "Одна зона", day: "День", night: "Ніч",
  },
};
const css = `
  :host { display:block; } ha-card { padding:20px; overflow:hidden; }
  h2 { font-size:1.3em; margin:0 0 16px; } h3 { font-size:1.08em; margin:20px 0 12px; }
  .controls { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  label { display:flex; align-items:center; gap:8px; }
  select, button { font:inherit; color:var(--primary-text-color); background:var(--card-background-color); border:1px solid var(--divider-color,#aaa); border-radius:8px; padding:8px 12px; min-height:40px; }
  button { cursor:pointer; } button:disabled { opacity:.5; cursor:default; }
  p { line-height:1.5; margin:12px 0; } .muted { color:var(--secondary-text-color); font-size:.9em; }
  .error { color:var(--error-color,#b71c1c); } .scroll { overflow-x:auto; }
  table { border-collapse:collapse; width:100%; font-size:.9em; }
  th,td { padding:9px 8px; border-bottom:1px solid var(--divider-color,#ddd); text-align:start; vertical-align:top; }
  th { font-weight:600; white-space:nowrap; } .numeric { font-variant-numeric:tabular-nums; white-space:nowrap; }
  .inactive,.blocked { color:var(--secondary-text-color); }
  .pill { padding:3px 6px; border:1px solid var(--divider-color,#aaa); border-radius:6px; white-space:nowrap; }
  .pagination { display:flex; gap:12px; align-items:center; justify-content:flex-end; margin-top:12px; }
  svg { display:block; width:100%; min-width:300px; height:auto; } svg text { font:16px sans-serif; fill:var(--secondary-text-color); }
  .bar { fill:var(--primary-color,#0288d1); } .negative { fill:var(--warning-color,#c67a00); }
  .axis { stroke:var(--divider-color,#aaa); stroke-width:1; }
  @media(max-width:500px) { ha-card {padding:14px;} h2 {font-size:1.15em;} }
`;
function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function svgElement(tag, attrs = {}, text) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  if (text !== undefined) el.textContent = text;
  return el;
}
class DtekAccountHistoryCard extends HTMLElement {
  constructor() { super(); this.attachShadow({mode:"open"}); this._seq=0; this._page=0; }
  setConfig(config) {
    if (!config.entity || typeof config.entity !== "string") throw new Error("Specify an account sensor in entity.");
    const year = config.year ?? Number(new Intl.DateTimeFormat("en", {year:"numeric", timeZone:"Europe/Kyiv"}).format(new Date()));
    if (!Number.isInteger(year) || year < 2000 || year > Number(new Intl.DateTimeFormat("en", {year:"numeric", timeZone:"Europe/Kyiv"}).format(new Date()))) throw new Error("Invalid history year.");
    const changed = !this._config || config.entity !== this._config.entity || config.year !== this._config.year;
    this._config = {...config};
    if (changed) { this._year=year; this._data=null; this._error=null; this._seq++; this._busy=false; this._page=0; }
    this._render();
    if (changed && this.isConnected && this._hass) this._load(false);
  }
  set hass(hass) {
    const initial = !this._hass;
    const lang = hass.language?.startsWith("uk") ? "uk" : "en";
    const languageChanged = lang !== this._lang;
    this._hass=hass; this._lang=lang;
    if (languageChanged) this._render();
    if (initial && this.isConnected && this._config) this._load(false);
  }
  connectedCallback() { if (this._hass && this._config) this._load(false); }
  disconnectedCallback() { this._seq++; this._busy=false; }
  getCardSize() { return 12; }
  static getConfigElement() { return accountCardEditor(); }
  static getStubConfig() { return {entity:"sensor.example_account_data_updated"}; }
  get t() { return words[this._lang || "en"]; }
  format(value) { return value === null || value === undefined ? "—" : new Intl.NumberFormat(this._lang || "en", {maximumFractionDigits:3}).format(Number(value)); }
  zone(value) { return ({GENERAL:this.t.general,DAY:this.t.day,NIGHT:this.t.night})[value] || value; }
  async _load(force) {
    if (!this._hass || !this._config || (this._busy && this._loadingYear===this._year)) return;
    const sequence=++this._seq, year=this._year, entity=this._config.entity;
    this._busy=true; this._loadingYear=year; this._error=null; this._render();
    try {
      const data=await this._hass.callWS({type:"dtek_monitor/account_history",entity_id:entity,year,force});
      if (sequence!==this._seq || !this.isConnected) return;
      if (data.year!==year || !Array.isArray(data.months) || !Array.isArray(data.readings)) throw new Error("Invalid history response");
      this._data=data; this._page=0;
    } catch (err) {
      if (sequence!==this._seq || !this.isConnected) return;
      this._error = err.code === "rate_limited" ? "limited" : err.code === "auth_required" ? "auth" : ["unauthorized","not_available"].includes(err.code) ? "denied" : "failure";
      if (this._error === "denied" || this._error === "auth") this._data=null;
    } finally {
      if (sequence===this._seq && this.isConnected) { this._busy=false; this._render(); }
    }
  }
  _render() {
    if (!this._config) return;
    const t=this.t, root=this.shadowRoot;
    root.replaceChildren(element("style",css));
    const card=element("ha-card"); root.append(card);
    card.append(element("h2",this._config.title || t.title));
    card.append(accountNumber(this._hass, this._config.entity));
    const controls=element("div",undefined,"controls"), label=element("label",t.year), select=element("select");
    select.setAttribute("aria-label",t.year);
    const current=Number(new Intl.DateTimeFormat("en",{year:"numeric",timeZone:"Europe/Kyiv"}).format(new Date()));
    for(let year=current;year>=2000;year--) { const opt=element("option",String(year));opt.value=String(year);select.append(opt); }
    select.value=String(this._year);
    select.addEventListener("change",()=>{ this._year=Number(select.value);this._data=null;this._page=0;this._load(false); });
    label.append(select);controls.append(label);
    const refresh=element("button",this._busy?t.loading:t.refresh);refresh.disabled=this._busy;refresh.addEventListener("click",()=>this._load(true));controls.append(refresh);card.append(controls);
    if(this._error) { const error=element("p",t[this._error],"error");error.setAttribute("role","alert");card.append(error);if(this._data)card.append(element("p",t.stale,"muted")); }
    if(!this._data || this._data.year!==this._year) return;
    const data=this._data;
    const fetched = new Intl.DateTimeFormat(this._lang || "en",{dateStyle:"medium",timeStyle:"short",timeZone:"Europe/Kyiv"}).format(new Date(data.fetched_at));
    card.append(element("p",`${t.fetched}: ${fetched} (Europe/Kyiv)`,"muted"));
    card.append(element("h3",`${t.consumption} · ${data.year}`));
    card.append(this._chart(data.months));card.append(element("p",t.note,"muted"));
    const details=element("details"),summary=element("summary",`${t.consumption} · kWh`);details.append(summary);
    const monthTable=this._table([t.period,t.total,t.zone,t.status]);
    for(const month of data.months) {
      const row=element("tr");row.append(element("td",month.period),element("td",this.format(month.value),"numeric"));
      const zones=Object.entries(month.zones).map(([key,value])=>`${this.zone(key)}: ${this.format(value)}`).join("; ");
      row.append(element("td",zones||"—"),element("td",t[month.status]||month.status));monthTable.body.append(row);
    }
    details.append(monthTable.wrap);card.append(details);
    card.append(element("h3",`${t.readings} · ${data.readings.length}`));card.append(element("p",t.scope,"muted"));
    if(!data.readings.length) {card.append(element("p",t.empty));return;}
    const table=this._table([t.date,t.value,t.status,t.zone,t.energy,t.source]);
    const pageSize=10, pages=Math.ceil(data.readings.length/pageSize);this._page=Math.min(this._page,pages-1);
    for(const record of data.readings.slice(this._page*pageSize,(this._page+1)*pageSize)) {
      const row=element("tr",undefined,record.status);
      const local=record.date_time.slice(0,16).replace("T"," ");
      row.append(element("td",local,"numeric"),element("td",this.format(record.value),"numeric"));
      const status=element("td");status.append(element("span",t[record.status]||t.unknown,"pill"));row.append(status,element("td",this.zone(record.zone)),element("td",record.energy),element("td",record.source||"—"));table.body.append(row);
    }
    card.append(table.wrap);
    const pagination=element("div",undefined,"pagination"), prev=element("button",t.previous), next=element("button",t.next);
    prev.disabled=this._page===0;next.disabled=this._page>=pages-1;
    prev.addEventListener("click",()=>{this._page--;this._render();});next.addEventListener("click",()=>{this._page++;this._render();});
    pagination.append(prev,element("span",`${t.page} ${this._page+1} / ${pages}`),next);card.append(pagination);
  }
  _table(headers) {
    const wrap=element("div",undefined,"scroll"),table=element("table"),head=element("thead"),tr=element("tr"),body=element("tbody");
    for(const title of headers)tr.append(element("th",title));head.append(tr);table.append(head,body);wrap.append(table);return {wrap,body};
  }
  _chart(months) {
    const wrap=element("div",undefined,"scroll"),svg=svgElement("svg",{viewBox:"0 0 600 240",role:"img","aria-label":`${this.t.consumption}, kWh`});
    const values=months.filter(m=>m.status==="closed" && m.value!==null).map(m=>Number(m.value));
    const min=Math.min(0,...values),max=Math.max(0,...values),span=max-min||1,y=v=>195-(v-min)/span*160,zero=y(0);
    svg.append(svgElement("line",{x1:45,y1:zero,x2:590,y2:zero,class:"axis"}));
    for(const value of new Set([min,max,0])) svg.append(svgElement("text",{x:40,y:y(value)+4,"text-anchor":"end"},this.format(value)));
    svg.append(svgElement("text",{x:45,y:18},"kWh"));
    months.forEach((month,i)=>{
      const x=53+i*44;
      svg.append(svgElement("text",{x:x+13,y:225,"text-anchor":"middle"},month.period.slice(5)));
      if(month.status!=="closed" || month.value===null){ svg.append(svgElement("text",{x:x+13,y:zero-8,"text-anchor":"middle"},"—"));return; }
      const value=Number(month.value),top=Math.min(y(value),zero),height=Math.max(Math.abs(y(value)-zero),1);
      const bar=svgElement("rect",{x,y:top,width:26,height,class:value<0?"bar negative":"bar","data-period":month.period,tabindex:0});
      const description=`${month.period}: ${this.format(month.value)} kWh`;bar.setAttribute("aria-label",description);bar.append(svgElement("title",{},description));svg.append(bar);
    });
    wrap.append(svg);return wrap;
  }
}
if(!customElements.get("dtek-account-history-card")) customElements.define("dtek-account-history-card",DtekAccountHistoryCard);
window.customCards=window.customCards||[];
window.customCards.push({type:"dtek-account-history-card",name:"DTEK account history",description:"Dated meter readings and monthly consumption for a personal account."});
