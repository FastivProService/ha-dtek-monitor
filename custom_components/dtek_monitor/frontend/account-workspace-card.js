import "./account-profile-card.js?v=1.8.0b11";
import "./account-readings-card.js?v=1.8.0b11";
import "./account-history-card.js?v=1.8.0b11";
import "./schedule-card.js?v=1.8.0b11";
import { accountCardEditor } from "./account-card-editor.js?v=1.8.0b11";
import { accountNumber } from "./account-number.js?v=1.8.0b11";
import { el, date, number, embeddedCSS } from "./account-ui.js?v=1.8.0b11";

const words = {
  en: {
    reload: "The dashboard still has an older version of the DTEK cards. Reload the page to open the new interface.", reloadAction: "Reload page", title: "My DTEK", overview: "Overview", schedule: "Schedule", readings: "Readings", consumption: "Consumption", settings: "Settings",
    account: "Personal account", choose: "Choose an account", loading: "Loading accounts…", error: "Could not load your accounts. Try again.",
    empty: "Connect a DTEK personal account to see your readings and consumption here.", missing: "This account is unavailable. Check the connection or choose another account.",
    connect: "Connect DTEK", manage: "Manage connections and notifications", retry: "Refresh account list",
    send: "Enter meter readings", history: "Reading history", latest: "Latest DTEK meter reading", usage: "Consumption reported by DTEK",
    readingNote: "Cumulative meter reading", noReading: "DTEK has not provided a reading yet.", unit: "kWh", GENERAL: "Single zone", DAY: "Day", NIGHT: "Night",
    group: "My outage group", groupEmpty: "Not selected", scheduleLink: "Open schedule", details: "Account and meter details", detailsShort: "Details",
    dated: "Account updated", unavailable: "Account data is unavailable. Retained values may be out of date.",
    sensor: "Home energy sensor", sensorNote: "Use your own cumulative energy sensor to suggest readings. You can also enter readings manually.",
    sensorSettings: "Set up or calibrate a sensor", notifications: "Groups, accounts and notifications", notificationsNote: "Choose the apartment's group, connect another account or change reminders in the integration settings.",
    diagnostics: "Check journal", diagnosticsNote: "Local checks and technical details", viewOnly: "You have viewing access. An administrator can submit readings and configure the source.",
    mine: "My group", building: "All building groups", noSchedule: "Link a building and choose your group in the integration settings. Building schedules remain available independently of the personal account.",
    setup: "Configure DTEK", draft: "Discard the unfinished entry?", draftNote: "Your readings or sensor settings have not been saved. They will not be copied to the other account.",
    stay: "Keep editing", discard: "Discard and switch", wait: "Wait for the current operation to finish before switching accounts.",
    state_current: "Today's schedule is published.", state_empty: "No planned outages today — the schedule is verified.", state_missing: "Today's schedule is not fully published yet.",
    state_unpublished: "DTEK has paused schedule publication.", state_partial: "Only part of today's schedule is available.", state_forecast: "Only a forecast is available.",
    state_error: "DTEK could not be reached. Updates will resume automatically.", state_unknown: "The schedule status is not known yet.",
    state_no_address: "Connect the building's schedule.", state_not_selected: "Choose your group from the building's groups.", state_group_unavailable: "DTEK no longer confirms your selected group. Review the selection.",
    state_labels_hidden: "DTEK has hidden group labels.", state_address_unavailable: "The building connection is unavailable.", state_unavailable: "Schedule data is unavailable.",
  },
  uk: {
    reload: "На панелі залишилася попередня версія карток ДТЕК. Оновіть сторінку, щоб відкрити новий інтерфейс.", reloadAction: "Оновити сторінку", title: "Мій ДТЕК", overview: "Огляд", schedule: "Графік", readings: "Покази", consumption: "Споживання", settings: "Налаштування",
    account: "Особовий рахунок", choose: "Оберіть рахунок", loading: "Завантаження рахунків…", error: "Не вдалося завантажити рахунки. Спробуйте ще раз.",
    empty: "Підключіть особистий кабінет ДТЕК, щоб бачити тут покази та споживання.", missing: "Цей рахунок недоступний. Перевірте підключення або оберіть інший рахунок.",
    connect: "Підключити ДТЕК", manage: "Керувати підключеннями та сповіщеннями", retry: "Оновити список рахунків",
    send: "Ввести покази лічильника", history: "Історія показів", latest: "Останні покази в ДТЕК", usage: "Споживання за даними ДТЕК",
    readingNote: "Накопичувальні покази лічильника", noReading: "ДТЕК ще не надав показів.", unit: "кВт·год", GENERAL: "Одна зона", DAY: "День", NIGHT: "Ніч",
    group: "Моя група відключень", groupEmpty: "Не обрана", scheduleLink: "Відкрити графік", details: "Реквізити та лічильник", detailsShort: "Реквізити",
    dated: "Рахунок оновлено", unavailable: "Дані рахунку недоступні. Збережені значення можуть бути застарілими.",
    sensor: "Домашній лічильник енергії", sensorNote: "Накопичувальний датчик енергії може підказувати покази. Їх також можна вводити вручну.",
    sensorSettings: "Підключити або відкалібрувати датчик", notifications: "Група, рахунки та сповіщення", notificationsNote: "Оберіть групу квартири, підключіть інший рахунок або змініть нагадування в налаштуваннях інтеграції.",
    diagnostics: "Журнал перевірок", diagnosticsNote: "Локальні перевірки й технічні відомості", viewOnly: "Вам доступний перегляд. Передавати покази та налаштовувати джерело може адміністратор.",
    mine: "Моя група", building: "Усі групи будинку", noSchedule: "Прив’яжіть будинок та оберіть свою групу в налаштуваннях інтеграції. Графіки будинку залишаються доступними незалежно від особистого кабінету.",
    setup: "Налаштувати ДТЕК", draft: "Скасувати незавершене введення?", draftNote: "Покази або налаштування датчика ще не збережено. Вони не будуть перенесені до іншого рахунку.",
    stay: "Продовжити введення", discard: "Скасувати та змінити рахунок", wait: "Дочекайтеся завершення поточної операції перед зміною рахунку.",
    state_current: "Графік на сьогодні опубліковано.", state_empty: "Сьогодні планових відключень немає — графік перевірено.", state_missing: "Графік на сьогодні ще не повністю опубліковано.",
    state_unpublished: "ДТЕК призупинив публікацію графіків.", state_partial: "Доступна лише частина графіка на сьогодні.", state_forecast: "Доступний лише прогноз.",
    state_error: "Немає зв’язку з ДТЕК. Оновлення відновляться автоматично.", state_unknown: "Стан графіка поки невідомий.",
    state_no_address: "Підключіть графік будинку.", state_not_selected: "Оберіть свою групу серед груп будинку.", state_group_unavailable: "ДТЕК більше не підтверджує обрану групу. Перевірте вибір.",
    state_labels_hidden: "ДТЕК приховав назви груп.", state_address_unavailable: "Підключення будинку недоступне.", state_unavailable: "Дані графіка недоступні.",
  },
};
const sections = ["overview", "schedule", "readings", "consumption", "settings"];
const css = `
  :host{display:block;container-type:inline-size;color:var(--primary-text-color,#192634)}
  *{box-sizing:border-box}ha-card{display:block;overflow:hidden;border-radius:var(--ha-card-border-radius,18px);background:var(--ha-card-background,var(--card-background-color,#fff))}
  .header{padding:24px 24px 20px;background:linear-gradient(120deg,color-mix(in srgb,var(--primary-color,#0288d1) 12%,transparent),transparent)}
  .brand{display:flex;align-items:center;justify-content:space-between;gap:12px}.brand h2{white-space:nowrap;font-size:1.15em;margin:0;font-weight:600}
  .address{font-size:1.45em;font-weight:650;line-height:1.35;margin:16px 0 10px;overflow-wrap:anywhere}
  .account-picker{display:block;margin-top:14px;font-size:.9em}.account-picker select{display:block;width:100%;margin-top:6px}
  nav{display:flex;border-block:1px solid var(--divider-color,#ddd);padding:0 16px;overflow-x:auto;scrollbar-width:thin;gap:4px}
  nav button{flex:1;white-space:nowrap;border:0;border-radius:0;border-bottom:3px solid transparent;padding:14px 10px;color:var(--secondary-text-color,#596979)}
  nav button[aria-selected=true]{color:var(--primary-color,#0288d1);border-bottom-color:var(--primary-color,#0288d1);font-weight:650}
  .panel{padding:24px}.tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
  .tile{padding:18px;border:1px solid var(--divider-color,#ddd);border-radius:14px}.tile h3{margin:0 0 12px;font-size:.95em;font-weight:500;color:var(--secondary-text-color,#596979)}
  .value{font-size:1.8em;line-height:1.2;font-weight:650;font-variant-numeric:tabular-nums;margin:8px 0}.unit{font-size:.5em;font-weight:400;margin-left:6px}
  .muted,small{color:var(--secondary-text-color,#596979);font-size:.88em}small{display:block;margin-top:5px}p,small{line-height:1.5;overflow-wrap:anywhere}
  .group{margin:20px 0;padding:18px;border-radius:14px;background:var(--secondary-background-color,#f4f7fa)}.group h3{margin:0;font-size:1em}.group strong{display:inline-block;margin-left:10px;font-size:1.15em}.group h3{display:inline}.group p{margin:8px 0 0}
  .actions{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 20px}.primary{flex-grow:1}
  button,select{font:inherit;color:inherit;background:transparent;border:1px solid var(--divider-color,#aab5bf);border-radius:10px;padding:10px 14px;min-height:44px;max-width:100%}
  select{background:var(--card-background-color,#fff)}button{cursor:pointer}button:disabled{opacity:.55;cursor:default}a{color:var(--primary-color,#0288d1);line-height:1.5}
  details{margin:20px 0}summary{display:flex;align-items:center;cursor:pointer;gap:10px;font-weight:600}summary::before{content:'›';font-size:1.4em}details[open]>summary::before{transform:rotate(90deg)}
  .setting{padding-bottom:20px;margin-bottom:20px;border-bottom:1px solid var(--divider-color,#ddd)}.setting h3{margin:0 0 8px;font-size:1.05em}
  .switcher{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}.switcher button[aria-pressed=true]{background:var(--secondary-background-color,#f4f7fa);border-color:var(--primary-color,#0288d1)}
  .notice{margin:14px 0;color:var(--secondary-text-color,#596979)}.notice:empty{display:none}.empty{padding:24px}
  dialog{max-width:min(440px,calc(100vw - 32px));border:1px solid var(--divider-color,#ddd);border-radius:16px;padding:24px;color:inherit;background:var(--card-background-color,#fff)}dialog::backdrop{background:#0008}dialog h3{margin-top:0}
  @container(max-width:520px){.header{padding:18px 16px}.panel{padding:18px 16px}.address{font-size:1.25em}.tiles{gap:10px}.tile{padding:12px}.tile h3{font-size:.85em}.value{font-size:1.45em}.unit{display:block;margin:4px 0 0;font-size:.6em}nav{padding:0 6px;flex-wrap:wrap}nav button{flex:1 1 auto;font-size:.85em;padding:10px 9px}.actions button{width:100%}}
  @media(prefers-reduced-motion:no-preference){button{transition:background-color .12s}}
`;

class DtekAccountWorkspaceCard extends HTMLElement {
  constructor() { super(); this.attachShadow({mode: "open"}); this._seq = 0; this._panels = new Map(); this._tab = "overview"; }
  static getConfigElement() { return accountCardEditor(); }
  static getStubConfig() { return {}; }
  getCardSize() { return 9; }
  getGridOptions() { return {columns: 12, rows: "auto", min_columns: 6}; }
  get t() { return words[this._hass?.language?.startsWith("uk") ? "uk" : "en"]; }
  setConfig(config) {
    if (config.entity !== undefined && typeof config.entity !== "string") throw new Error("entity must be a string");
    const changed = this._config?.entity !== config.entity;
    this._config = {...config};
    if (changed) { this._entity = config.entity; this._resetPanels(); }
    this._ensureShell(); this._updateHeader();
    if (this._accounts) this._applyAccounts();
  }
  set hass(hass) {
    const changed = this._hass && (this._hass.user?.id !== hass.user?.id || this._hass.connection !== hass.connection);
    this._hass = hass;
    if (changed) {
      this._seq++; this._loading = false; this._accounts = null; this._entity = this._config?.entity;
      this._resetPanels(); this._identity?.remove(); this._identity = null;
      this._select?.replaceChildren();
    }
    this._ensureShell(); this._updateHeader();
    // Keep mounted forms and inputs; update their hass reference only.
    for (const panel of this._panels.values()) for (const card of panel.querySelectorAll("[data-child-card]")) card.hass = hass;
    if (!this._accounts && !this._loading && this.isConnected) this._loadAccounts();
    this._updateOverview();
  }
  connectedCallback() { this._ensureShell(); if (this._hass && !this._accounts) this._loadAccounts(); }
  disconnectedCallback() { this._seq++; this._loading = false; this._accounts = null; this._resetPanels(); this._identity?.remove(); this._identity = null; }
  _button(text, fn, cls) { const b = el("button", text, cls); b.type = "button"; b.onclick = fn; const key=Object.keys(this.t).find(key=>this.t[key]===text); if(key)b.dataset.i18n=key; return b; }
  _link(text, href = "/config/integrations/integration/dtek_monitor") { const a = el("a", text); a.href = href; const key=Object.keys(this.t).find(key=>this.t[key]===text); if(key)a.dataset.i18n=key; return a; }
  _ensureShell() {
    if (this._shell || !this._config) return;
    this._shell = el("ha-card");
    const header = el("header", undefined, "header"), brand = el("div", undefined, "brand");
    this._title = el("h2"); this._detailsButton = this._button("", () => { this._activate("settings"); this._profileDetails.open = true; this._profileDetails.dispatchEvent(new Event("toggle")); });
    brand.append(this._title, this._detailsButton); header.append(brand);
    this._picker = el("label", undefined, "account-picker"); this._pickerLabel = el("span"); this._select = el("select");
    this._select.onchange = () => this._choose(this._select.value); this._picker.append(this._pickerLabel, this._select); header.append(this._picker);
    this._address = el("div", undefined, "address"); this._identityBox = el("div"); header.append(this._address, this._identityBox);
    this._nav = el("nav"); this._nav.setAttribute("role", "tablist"); this._tabs = new Map();
    for (const section of sections) {
      const b = this._button("", () => this._activate(section)); b.setAttribute("role", "tab"); b.id = `tab-${section}`; b.setAttribute("aria-controls", `panel-${section}`);
      b.onkeydown = e => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
        e.preventDefault(); const i=sections.indexOf(section), next=e.key==="Home"?0:e.key==="End"?sections.length-1:(i+(e.key==="ArrowRight"?1:-1)+sections.length)%sections.length;
        this._activate(sections[next]); this._tabs.get(sections[next]).focus();
      };
      this._tabs.set(section, b); this._nav.append(b);
    }
    this._notice = el("p", undefined, "notice"); this._notice.setAttribute("role", "status");
    this._content = el("div"); this._empty = el("div", undefined, "empty");
    this._dialog = el("dialog"); this._dialog.setAttribute("aria-labelledby", "switch-title");
    this._shell.append(header, this._nav, this._notice, this._empty, this._content, this._dialog);
    this.shadowRoot.append(el("style", css + embeddedCSS), this._shell);
  }
  _resetPanels() {
    this._panels.clear(); this._content?.replaceChildren(); this._readings = null; this._overview = null; this._account = null; this._tab = "overview";
    if (this._dialog?.open) this._dialog.close();
  }
  async _loadAccounts() {
    if (!this._hass || this._loading) return;
    const seq=++this._seq; this._loading=true; this._listError=false; this._showEmpty(this.t.loading);
    try {
      const accounts=await this._hass.callWS({type: "dtek_monitor/accounts"});
      if (seq!==this._seq || !this.isConnected) return;
      if (!Array.isArray(accounts)) throw new Error("Invalid account list");
      this._accounts=accounts; this._applyAccounts();
    } catch (_) {
      if (seq!==this._seq || !this.isConnected) return;
      this._listError=true; this._showEmpty(this.t.error);
    } finally { if (seq===this._seq) this._loading=false; }
  }
  _applyAccounts() {
    if (!this._hass) return;
    if (["dtek-account-readings-card", "dtek-account-history-card", "dtek-account-profile-card", "dtek-energy-editor"].some(tag => customElements.get(tag)?.workspaceVersion !== 1)) {
      this._resetPanels(); this._identity?.remove(); this._identity=null; this._updateHeader(); this._showEmpty(this.t.reload);
      this._empty.replaceChildren(el("p",this.t.reload),this._button(this.t.reloadAction,()=>window.location.reload()));
      return;
    }
    if (!this._entity && this._accounts.length===1) this._entity=this._accounts[0].entity_id;
    const account=this._accounts.find(a => a.entity_id===this._entity || a.entities?.includes(this._entity));
    this._select.replaceChildren(...[{entity_id: "", masked: this.t.choose}, ...this._accounts].map(a => {
      const option=el("option", a.address ? `${a.address} · ${a.masked}` : a.masked); option.value=a.entity_id; return option;
    }));
    this._select.value=account?.entity_id || "";
    if (!account) {
      this._resetPanels(); this._identity?.remove(); this._identity=null;
      this._showEmpty(this._entity ? this.t.missing : this._accounts.length ? this.t.choose : this.t.empty); this._updateHeader(); return;
    }
    const changed=this._account?.entity_id!==account.entity_id;
    if (changed) this._resetPanels();
    this._entity=account.entity_id; this._account=account;
    if (this._readings) this._readings._config.read_only=!account.can_submit;
    this._empty.hidden=true; this._content.hidden=false; this._nav.hidden=false; this._updateHeader(); this._activate(this._tab);
  }
  _showEmpty(message) {
    if (!this._empty) return;
    this._empty.hidden=false; this._content.hidden=true; this._nav.hidden=true;
    this._empty.replaceChildren(el("p", message));
    if (this._listError || this._accounts) this._empty.append(this._button(this.t.retry, () => this._loadAccounts()));
    this._empty.append(el("p")); this._empty.append(this._link(this._accounts?.length ? this.t.setup : this.t.connect, this._accounts?.length ? undefined : "/config/integrations/dashboard?add_integration=dtek_monitor"));
  }
  _updateHeader() {
    if (!this._shell) return;
    const t=this.t;
    for (const node of this.shadowRoot.querySelectorAll("[data-i18n]")) node.textContent=t[node.dataset.i18n];
    this._title.textContent=this._config.title || t.title; this._detailsButton.textContent=t.detailsShort; this._detailsButton.setAttribute("aria-label",t.details); this._detailsButton.hidden=!this._account;
    this._pickerLabel.textContent=t.account; this._select.setAttribute("aria-label", t.account);
    this._picker.hidden=this._config.show_account_selector===false || !this._accounts?.length || this._accounts.length===1;
    this._address.textContent=this._account?.address || "";
    this._nav.setAttribute("aria-label", t.title);
    for (const [key, b] of this._tabs) b.textContent=t[key];
    if (this._account && this._identityEntity!==this._entity || this._account && !this._identity) {
      this._identity=accountNumber(this._hass, this._entity); this._identityEntity=this._entity; this._identityBox.replaceChildren(this._identity);
    }
  }
  _choose(entity) {
    this._select.value=this._entity || "";
    if (!entity || entity===this._entity) return;
    if (this._readings?.busy) { this._notice.textContent=this.t.wait; return; }
    const switchAccount=() => { this._entity=entity; this._notice.textContent=""; this._applyAccounts(); };
    if (!this._readings?.hasDraft) { switchAccount(); return; }
    const title=el("h3", this.t.draft); title.id="switch-title";
    const actions=el("div", undefined, "actions");
    actions.append(this._button(this.t.stay, () => this._dialog.close()), this._button(this.t.discard, () => { this._dialog.close(); switchAccount(); }));
    this._dialog.replaceChildren(title, el("p", this.t.draftNote), actions); this._dialog.showModal();
  }
  _child(tag, config, parent) {
    const card=document.createElement(tag); card.dataset.childCard=""; card.setConfig({entity:this._entity, embedded:true, ...config}); card.hass=this._hass; parent.append(card); return card;
  }
  _activate(tab) {
    if (!this._account) return;
    if (!this._panels.has(tab)) {
      const panel=el("section", undefined, "panel"); panel.id=`panel-${tab}`; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", `tab-${tab}`);
      this._panels.set(tab, panel); this._content.append(panel); this[`_create_${tab}`](panel);
    }
    this._tab=tab;
    for (const [key, panel] of this._panels) panel.hidden=key!==tab;
    for (const [key, button] of this._tabs) { button.setAttribute("aria-selected", String(key===tab)); button.tabIndex=key===tab?0:-1; }
    if (tab==="readings") this._readings?.open();
  }
  _create_overview(panel) {
    this._overview=el("div");
    const actions=el("div", undefined, "actions");
    if (this._account.can_submit) actions.append(this._button(this.t.send, () => this._activate("readings"), "primary"));
    actions.append(this._button(this.t.scheduleLink, () => this._activate("schedule"))); panel.append(actions,this._overview);
    if (!this._account.can_submit) panel.append(el("p", this.t.viewOnly, "muted"));
    this._updateOverview();
  }
  _period(value) {
    if (!/^\d{4}-\d{2}$/.test(value || "")) return value || "—";
    const stamp=new Date(`${value}-01T12:00:00+03:00`);
    return Number.isFinite(stamp.getTime()) ? new Intl.DateTimeFormat(this._hass.language || "en",{month:"long",year:"numeric",timeZone:"Europe/Kyiv"}).format(stamp) : "—";
  }
  _state(role) { return this._hass.states?.[this._account?.roles?.[role]]; }
  _updateOverview() {
    if (!this._overview || !this._account) return;
    const t=this.t, lang=this._hass.language, tiles=el("div", undefined, "tiles"), reading=el("article", undefined, "tile"), consumption=el("article", undefined, "tile");
    reading.append(el("h3", t.latest));
    const roles=Object.entries(this._account.roles || {}).filter(([key]) => key.startsWith("account_reading:"));
    if (!roles.length) reading.append(el("p", t.noReading));
    for (const [key, id] of roles) {
      const state=this._hass.states?.[id], zone=key.split(":")[1], value=el("p", number(state?.state, lang), "value"); value.append(el("span", t.unit, "unit"));
      reading.append(el("small", t[zone] || zone), value, el("small", date(state?.attributes?.reading_date, lang)));
    }
    reading.append(el("small", t.readingNote));
    const usage=this._state("account_consumption:all"), usageValue=el("p", number(usage?.state, lang), "value"); usageValue.append(el("span", t.unit, "unit"));
    consumption.append(el("h3", t.usage), usageValue, el("small", this._period(usage?.attributes?.period))); tiles.append(reading, consumption);
    const group=el("article", undefined, "group"), state=this._state("account_schedule_status:all"), selected=this._state("account_schedule_groups:all")?.attributes?.selected_groups || [];
    let status=state?.state || "unknown";
    if (["selected","auto_selected"].includes(status)) status=state?.attributes?.schedule_status || "current";
    if (status.startsWith("schedule_")) status=status.slice(9);
    group.append(el("h3", t.group), el("strong", selected.length ? selected.map(g=>g.replace(/^GPV/, "")).join(", ") : t.groupEmpty), el("p", t[`state_${status}`] || t.state_unknown));
    const updated=this._state("account_updated:all") || this._hass.states?.[this._entity];
    const stamp=el("p", updated && !["unknown","unavailable"].includes(updated.state) ? `${t.dated}: ${date(updated.state,lang,true)}` : t.unavailable, "muted");
    this._overview.replaceChildren(tiles, group, stamp);
  }
  _create_readings(panel) {
    this._readings=this._child("dtek-account-readings-card", {read_only: !this._account.can_submit}, panel);
    const details=el("details"), summary=el("summary", this.t.history); summary.dataset.i18n="history"; details.append(summary); panel.append(details);
    details.addEventListener("toggle", () => { if (details.open && !details.querySelector("[data-child-card]")) this._child("dtek-account-history-card", {view:"readings"}, details); });
  }
  _create_consumption(panel) { this._child("dtek-account-history-card", {view:"consumption"}, panel); }
  _create_schedule(panel) {
    const buttons=el("div", undefined, "switcher"), slots=el("div"), mounted=new Map();
    const modes={mine:{status_entity:this._account.roles?.["account_schedule_status:all"], entities:[this._account.roles?.["account_outage_schedule:all"],this._account.roles?.["account_possible_schedule:all"]].filter(Boolean)},
      building:{status_entity:this._account.building_schedule?.schedule_status, entities:[this._account.building_schedule?.outage_schedule,this._account.building_schedule?.possible_schedule].filter(Boolean)}};
    const show=mode => {
      for (const b of buttons.children) b.setAttribute("aria-pressed", String(b.dataset.mode===mode));
      if (!mounted.has(mode)) {
        const box=el("div"), config=modes[mode]; mounted.set(mode,box); slots.append(box);
        if (config.status_entity && config.entities.length) this._child("dtek-schedule-card", {...config, title:this.t[mode]}, box);
        else box.append(el("p", this.t.noSchedule), this._link(this.t.setup));
      }
      for (const [key, box] of mounted) box.hidden=key!==mode;
    };
    for (const mode of ["mine","building"]) { const b=this._button(this.t[mode], () => show(mode)); b.dataset.mode=mode; buttons.append(b); }
    panel.append(buttons,slots); show("mine");
  }
  _create_settings(panel) {
    const profile=el("details"); profile.append(el("summary", this.t.details)); this._profileDetails=profile;
    profile.addEventListener("toggle", () => { if (profile.open && !profile.querySelector("[data-child-card]")) this._child("dtek-account-profile-card", {}, profile); }); panel.append(profile);
    if (this._account.can_submit) {
      const sensor=el("section", undefined, "setting"); sensor.append(el("h3", this.t.sensor), el("p", this.t.sensorNote), this._button(this.t.sensorSettings, async () => {
        this._activate("readings"); await this._readings.open(); const editor=this._readings._energyEditor;
        if (editor) {editor._settingsOpen=true;editor.render();editor.scrollIntoView({block:"nearest"});}
      })); panel.append(sensor);
    }
    const settings=el("section", undefined, "setting"); settings.append(el("h3", this.t.notifications), el("p", this.t.notificationsNote)); if(this._hass.user?.is_admin)settings.append(this._link(this.t.manage)); panel.append(settings);
    const diagnostics=el("details"); diagnostics.append(el("summary", this.t.diagnostics), el("p", this.t.diagnosticsNote, "muted"));
    diagnostics.addEventListener("toggle", () => { if (diagnostics.open && !diagnostics.querySelector("[data-child-card]")) this._child("dtek-account-check-card", {}, diagnostics); }); panel.append(diagnostics);
    panel.append(this._button(this.t.retry, () => this._loadAccounts()));
  }
}
if (!customElements.get("dtek-account-workspace-card")) customElements.define("dtek-account-workspace-card", DtekAccountWorkspaceCard);
window.customCards=window.customCards || [];
if (!window.customCards.some(c=>c.type==="dtek-account-workspace-card")) window.customCards.push({type:"dtek-account-workspace-card", name:"My DTEK", description:"One account workspace: overview, schedule, readings, consumption and settings."});
