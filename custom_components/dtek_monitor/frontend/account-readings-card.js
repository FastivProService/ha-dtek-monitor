import "./account-readback.js?v=1.8.0b21";
import { embeddedCSS, date, number } from "./account-ui.js?v=1.8.0b21";
import "./account-check-card.js?v=1.8.0b21";
import { accountCardEditor } from "./account-card-editor.js?v=1.8.0b21";
/* Explicit readings submission. HA state updates never replace an edited form. */
import { accountNumber } from "./account-number.js?v=1.8.0b21";
import { DtekEnergyEditor, energyMessage } from "./account-energy-editor.js?v=1.8.0b21";
const labels = {
  en: {
    energyLocked: "Wait for the sensor settings operation to finish.", sourceInvalid: "Sensor settings changed. Your values are retained. Insert a fresh suggestion or verify and edit the readings manually before review.",
    title: "Send DTEK meter readings", load: "Load current meter", prepare: "Review readings", confirm: "Confirm and send to DTEK",
    back: "Back to editing", loading: "Working…", serial: "Meter", previous: "Previous", value: "New reading", increase: "Difference",
    calibrate: "Use entered readings for local calibration", sourceReview: "Source: Home Assistant energy sensor. The captured proposal is checked again before sending; it expires after three minutes.", general: "Single zone", day: "Day", night: "Night", date: "Date (Kyiv)",
    instructions: "Enter whole-number kWh from the meter or insert a reviewed energy-source proposal. All tariff zones are required. Reading submission is available to HA administrators.",
    review: "Check the account, meter and every value. Confirmation is valid for five minutes; DTEK data is checked again before sending.",
    checked: "I reviewed these readings and their source and want to send them to DTEK.",
    cabinet: "Open DTEK cabinet", accepted: "DTEK explicitly confirmed receipt for every tariff zone. This does not mean the billing calculation is final.",
    rejected: "DTEK rejected the submitted readings. Check the account in the cabinet before preparing new values.",
    unknown: "The result is not confirmed. Do not send these readings again until you check the DTEK cabinet. There is no automatic retry.",
    partial: "Only some tariff zones may have been accepted. Check all zones in the DTEK cabinet before continuing.",
    acknowledged: "The previous result has been checked. A new submission requires a new review and confirmation.",
    not_sent: "The request was stopped before submission.", last: "Last attempt", recover: "Check submission status",
    ackCheck: "I checked every tariff zone in the DTEK cabinet and understand that unlocking does not resend anything.",
    ack: "Unlock new entry after checking", unavailable: "Could not verify account data. Try loading the meter again later.",
    unauthorized: "This form requires an active HA administrator with read and control access to this account.",
    not_available: "This account is no longer available. Check the integration and selected account.",
    auth_required: "Reauthenticate the personal account in Settings → Devices & services.",
    rate_limited: "DTEK has limited requests. Please try again later.",
    unsupported_meter: "This meter configuration cannot be submitted here. Initially one connected consumption meter with one zone or Day/Night is supported. Use the DTEK cabinet.",
    pending_or_blocked: "There are current/pending or blocked readings. Check the DTEK cabinet before entering more readings.",
    invalid_readings: "Enter whole non-negative numbers for all zones, within the meter's digit limit and no lower than previous readings. Use the cabinet for meter rollover or replacement.",
    context_changed: "The meter data changed. Load current data and review your readings again.",
    confirmation_expired: "This confirmation expired or was already used. Check submission status before preparing another one.",
    already_sent: "These values were already accepted today. Check the DTEK cabinet.",
    check_cabinet: "A previous result is unresolved. Check submission status and the DTEK cabinet.",
    journal_unavailable: "The local submission record could not be saved or read. Check submission status and the cabinet before any new attempt.",
  },
  uk: {
    energyLocked: "Дочекайтеся завершення налаштування датчика.", sourceInvalid: "Налаштування датчика змінилися. Введені значення збережено. Підставте свіжу підказку або перевірте й відредагуйте покази вручну перед переглядом.",
    title: "Передати покази ДТЕК", load: "Завантажити дані лічильника", prepare: "Перевірити покази", confirm: "Підтвердити та передати в ДТЕК",
    back: "Повернутися до редагування", loading: "Зачекайте…", serial: "Лічильник", previous: "Попередні", value: "Нові покази", increase: "Різниця",
    calibrate: "Використати введені покази для локальної звірки", sourceReview: "Джерело: датчик енергії Home Assistant. Збережений відлік перевіряється перед передачею та діє три хвилини.", general: "Одна зона", day: "День", night: "Ніч", date: "Дата (Київ)",
    instructions: "Введіть накопичувальні покази лічильника в цілих кВт·год або підставте перевірену підказку джерела для всіх тарифних зон. Передача доступна адміністраторам HA.",
    review: "Перевірте рахунок, лічильник і кожне значення. Підтвердження діє п’ять хвилин; перед відправленням дані ДТЕК перевіряються повторно.",
    checked: "Я перевірив ці покази та їхнє джерело і хочу передати їх у ДТЕК.",
    cabinet: "Відкрити кабінет ДТЕК", accepted: "ДТЕК явно підтвердив отримання для кожної тарифної зони. Це ще не означає остаточний розрахунок споживання.",
    rejected: "ДТЕК відхилив передані покази. Перевірте рахунок у кабінеті перед введенням нових значень.",
    unknown: "Результат не підтверджено. Не надсилайте ці покази повторно, доки не перевірите кабінет ДТЕК. Автоматичних повторів немає.",
    partial: "Деякі тарифні зони могли бути прийняті. Перевірте всі зони в кабінеті ДТЕК перед продовженням.",
    acknowledged: "Попередній результат перевірено. Для нової передачі потрібні новий перегляд і підтвердження.",
    not_sent: "Запит зупинено до передачі показів.", last: "Остання спроба", recover: "Перевірити стан передачі",
    ackCheck: "Я перевірив усі тарифні зони в кабінеті ДТЕК і розумію, що розблокування нічого не надсилає повторно.",
    ack: "Дозволити нове введення після перевірки", unavailable: "Не вдалося перевірити дані рахунку. Спробуйте завантажити їх пізніше.",
    unauthorized: "Потрібен активний адміністратор HA з правами читання та керування цим рахунком.",
    not_available: "Рахунок більше недоступний. Перевірте інтеграцію та вибраний рахунок.",
    auth_required: "Повторно авторизуйте кабінет у Налаштування → Пристрої та служби.",
    rate_limited: "ДТЕК обмежив запити. Спробуйте пізніше.",
    unsupported_meter: "Ця конфігурація не підтримує передачу тут. Наразі підтримується один підключений лічильник споживання: одна зона або День/Ніч. Скористайтеся кабінетом ДТЕК.",
    pending_or_blocked: "Є поточні/очікувані або заблоковані покази. Перевірте кабінет ДТЕК перед новою передачею.",
    invalid_readings: "Введіть цілі невід’ємні числа для всіх зон у межах розрядності лічильника, не менші за попередні. Для обнулення чи заміни лічильника скористайтеся кабінетом.",
    context_changed: "Дані лічильника змінилися. Завантажте актуальні дані та перевірте покази ще раз.",
    confirmation_expired: "Підтвердження прострочене або вже використане. Перевірте стан передачі перед новою спробою.",
    already_sent: "Ці значення вже прийняті сьогодні. Перевірте кабінет ДТЕК.",
    check_cabinet: "Результат попередньої спроби невідомий. Перевірте стан передачі та кабінет ДТЕК.",
    journal_unavailable: "Не вдалося зберегти або прочитати локальний запис передачі. Перевірте стан передачі та кабінет перед новою спробою.",
  },
};
function node(tag, text) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  return el;
}

class DtekAccountReadingsCard extends HTMLElement {
  static get workspaceVersion() { return 2; }
  constructor() {
    super(); this.attachShadow({mode: "open"}); this._seq = 0; this._values = {};
  }
  setConfig(config) {
    if (!config.entity || typeof config.entity !== "string") throw new Error("Set an account entity");
    if (this._config?.entity === config.entity) { this._config={...config}; this.toggleAttribute("embedded",!!config.embedded); this.render(); return; }
    this._seq++; this._config = {...config}; this.toggleAttribute("embedded",!!config.embedded); this._context = null; this._draft = null;
    this._opening = null; this._readback = null; this._energyEditor = null; this._sourceInvalid=false;this._energyLocked=false;this._sourceToken = null; this._result = null; this._values = {}; this._error = null; this._uncertain = false; this._busy = false;
    this.render();
  }
  set hass(hass) {
    const languageChanged = this._hass?.language !== hass.language;
    const first = !this._hass;
    const changedUser = this._hass && (this._hass.user?.id !== hass.user?.id || this._hass.connection !== hass.connection);
    this._hass = hass;
    if (changedUser) { const config=this._config; this._config=null; this.setConfig(config); }
    this._energyEditor?.configure(hass, this._config.entity, this._context, !!this._config.embedded,{externalSettings:!!this._config.energy_settings_external});
    // hass is replaced on every state update; do not rebuild inputs or confirmation.
    if (first || languageChanged || !this.shadowRoot.firstChild) this.render();
  }
  connectedCallback() { this.render(); }
  disconnectedCallback() {
    this._seq++; this._draft = null; this._busy = false;
    if (this._sending) this._uncertain = true;
    this._sending = false;
  }
  getCardSize() { return 5; }
  get hasDraft() { return !!this._draft || Object.values(this._values).some(v => v !== "") || !!this._energyEditor?.hasDraft; }
  set energyLocked(value){if(this._energyLocked!==value){this._energyLocked=value;this.render();}}
  invalidateEnergy(){
    this._energyEditor?.invalidate();
    if(this._sourceToken || this._draft?.source){this._draft=null;this._sourceInvalid=true;}
    this.render();
  }
  get busy() { return this._busy || !!this._energyEditor?._busy; }
  get readOnly() { return !!this._config?.read_only || this._hass?.user?.is_admin === false; }
  async open() {
    if (this._opening) return this._opening;
    if (this.readOnly || this._context || this._busy || this._uncertain) return;
    const entity = this._config.entity;
    const opening = (async () => {
      await this.act("context");
      if (this.isConnected && this._config.entity === entity && this._context && this._energyEditor) await this._energyEditor.act("get");
    })();
    this._opening = opening;
    try { await opening; } finally { if (this._opening === opening) this._opening = null; }
  }
  static getConfigElement() { return accountCardEditor(); }
  static getStubConfig() { return {entity: "sensor.example_account_data_updated"}; }
  get w() { return labels[this._hass?.language?.startsWith("uk") ? "uk" : "en"]; }
  async request(action, extra = {}) {
    return this._hass.callWS({type: "dtek_monitor/account_submission", entity_id: this._config.entity, action, ...extra});
  }
  async act(action) {
    if (this._busy || !this._hass || this.readOnly) return;
    if((action==="prepare"||action==="confirm")&&(this._energyLocked||this._sourceInvalid))return;
    const seq = ++this._seq;
    const draft = this._draft;
    const context = this._context;
    this._busy = true; this._error = null; this._sending = action === "confirm";
    this.render();this.dispatchEvent(new CustomEvent("submission-operation"));
    try {
      let result;
      if (action === "context") {
        result = await this.request(action);
        if (seq !== this._seq) return;
        if (context?.serial !== result.serial) {this._values = {}; this._sourceToken = null; this._energyEditor = null;}
        this._context = result; this._draft = null; this._result = null; this._uncertain = false;
      } else if (action === "prepare") {
        result = await this.request(action, {revision: context.revision, values: {...this._values}, ...(this._sourceToken ? {source_token: this._sourceToken} : {})});
        if (seq !== this._seq) return;
        this._draft = result; this._result = null;
      } else if (action === "confirm") {
        result = await this.request(action, {confirmation: draft.confirmation});
        if (seq !== this._seq) return;
        this._draft = null; this._result = result; this._uncertain = true; this._values = {}; this._sourceToken = null;
        this._readback?.reset();
      } else {
        result = await this.request(action, {attempt: context.last_attempt.id});
        if (seq !== this._seq) return;
        this._context = null; this._result = result; this._values = {};
      }
    } catch (error) {
      if (seq !== this._seq) return;
      this._error = error?.code || "unavailable";
      if (action === "confirm") { this._draft = null; this._uncertain = true; }
      if (["unauthorized", "not_available", "auth_required"].includes(this._error)) {
        this._context = null; this._draft = null; this._values = {}; this._result = null;
      }
    } finally {
      if (seq === this._seq) { this._busy = false; this._sending = false; this.render();this.dispatchEvent(new CustomEvent("submission-operation")); }
    }
  }
  button(text, action) {
    const b = node("button", text); b.type = "button"; b.disabled = this._busy || this._energyLocked;
    b.addEventListener("click", action); return b;
  }
  gate(card, label, button) {
    const wrap = node("label"); wrap.className = "check";
    const check = node("input"); check.type = "checkbox"; check.disabled = this._busy;
    check.addEventListener("change", () => {button.disabled = !check.checked || this._busy || this._energyLocked;});
    wrap.append(check, node("span", label)); card.append(wrap); button.disabled = true; card.append(button);
  }
  table(card, rows) {
    const wrap = node("div"); wrap.className = "scroll";
    const table = node("table"), head = node("tr"); table.className="review-table";
    for (const text of ["", this.w.previous, this.w.value, this.w.increase]) head.append(node("th", text));
    table.append(head);
    for (const r of rows) {
      const tr = node("tr");
      for (const [i, text] of [this.w[r.zone.toLowerCase()] || r.zone, number(r.previous,this._hass?.language), number(r.value,this._hass?.language), number(r.increase,this._hass?.language)].entries()) { const cell=node("td", String(text)); cell.dataset.label=["",this.w.previous,this.w.value,this.w.increase][i]; tr.append(cell); }
      table.append(tr);
    }
    wrap.append(table); card.append(wrap);
  }
  render() {
    if (!this._config) return;
    const w = this.w, card = node("ha-card");
    const style = node("style", `:host{display:block}ha-card{padding:20px;overflow:hidden}h2{font-size:1.2em;margin:0 0 12px}p{line-height:1.5;overflow-wrap:anywhere}button,input{font:inherit;color:var(--primary-text-color);background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#aaa);border-radius:6px;padding:10px}button{cursor:pointer;margin:8px 8px 0 0;white-space:normal}button:disabled{opacity:.5;cursor:default}.fields label{display:block;margin:16px 0}.fields input{display:block;box-sizing:border-box;width:100%;margin-top:8px}.check{display:flex;gap:10px;align-items:flex-start;margin:16px 0}.check input{margin-top:4px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:10px 6px;border-bottom:1px solid var(--divider-color,#ddd);white-space:nowrap}.alert{color:var(--error-color,#b22)}a{color:var(--primary-color)}small{display:block;color:var(--secondary-text-color);margin-top:6px}`);
    card.append(node("h2", this._config.title || w.title));
    if (!this._config.embedded) card.append(accountNumber(this._hass, this._config.entity));
    if (this.readOnly) { const p=node("p", this._hass?.language?.startsWith("uk") ? "Ви можете переглядати покази та історію. Передавати покази й налаштовувати датчик може адміністратор Home Assistant." : "You can view readings and history. Sending readings and configuring the source requires a Home Assistant administrator."); p.setAttribute("role","status"); card.append(p); style.textContent+=embeddedCSS; this.shadowRoot.replaceChildren(style,card); return; }
    card.append(node("p", this._config.embedded ? (this._hass?.language?.startsWith("uk") ? "Вкажіть накопичувальні покази з лічильника — не споживання за місяць." : "Enter the cumulative meter reading, not the monthly consumption.") : w.instructions));
    const a = node("a", w.cabinet); a.href = "https://my.dtek-oem.com.ua/"; a.target = "_blank"; a.rel = "noopener noreferrer"; card.append(a);
    if (this._error) { const error = node("p", w[this._error] || energyMessage(this._hass?.language, this._error) || w.unavailable); error.className = "alert"; error.setAttribute("role", "alert"); card.append(error); }
    if(this._energyLocked || this._sourceInvalid){const note=node("p",this._energyLocked?w.energyLocked:w.sourceInvalid);note.className="source-notice";note.setAttribute("role","status");card.append(note);}
    const ctx = this._context;
    if (ctx) {
      if (!this._config.embedded || this._draft) card.append(node("p", ctx.address));
      if (ctx.serial) card.append(node("p", `${w.serial}: ${ctx.serial} · ${date(ctx.date, this._hass?.language)}`));
    }
    const result = this._result || ctx?.last_attempt;
    if (this._uncertain && !this._result) card.append(node("p", w.unknown));
    if (result) {
      const p = node("p", w[result.status] || w.unknown); p.setAttribute("role", "status"); card.append(p);
      if (result.at) card.append(node("small", `${w.last}: ${date(result.at, this._hass?.language, true)}`));
      if (result.readings) this.table(card, result.readings);
    }
    if (this._draft) {
      card.append(node("p", this._config.embedded ? (this._hass?.language?.startsWith("uk") ? "Перевірте адресу, лічильник і покази перед передачею." : "Check the address, meter and readings before sending.") : w.review)); this.table(card, this._draft.readings);
      if (this._draft.source) {
        card.append(node("p", this._config.embedded ? (this._hass?.language?.startsWith("uk") ? "Покази розраховано за домашнім датчиком. Перед передачею джерело буде перевірено ще раз." : "These readings use your home sensor. Its data is checked again before sending.") : w.sourceReview), node("p", energyMessage(this._hass?.language, this._draft.source.mode)));
        for (const [zone, row] of Object.entries(this._draft.source.readings)) card.append(node("small", `${w[zone.toLowerCase()]}: ${row.source.value} kWh · ${row.source.at}`));
      }
      card.append(this.button(w.back, () => {this._draft = null; this.render();}));
      const send = this.button(w.confirm, () => this.act("confirm")); send.className="primary"; this.gate(card, w.checked, send);
    } else if (ctx?.blocked && !this._uncertain) {
      if (["unknown", "partial"].includes(ctx.last_attempt?.status)) {
        this.gate(card, w.ackCheck, this.button(w.ack, () => this.act("acknowledge")));
      } else card.append(node("p", w[ctx.reason] || w.unsupported_meter));
    } else if (ctx && !this._uncertain) {
      const form = node("form"); form.className = "fields";
      for (const z of ctx.zones) {
        const label = node("label", `${w[z.zone.toLowerCase()] || z.zone} · ${w.value} (${this._hass?.language?.startsWith("uk") ? "кВт·год" : "kWh"})`);
        label.append(node("small", `${w.previous}: ${number(z.previous, this._hass?.language)} · ${date(z.date, this._hass?.language)}`));
        const input = node("input"); input.type = "text"; input.inputMode = "numeric"; input.pattern = "[0-9]+";
        input.maxLength = ctx.digits; input.required = true; input.disabled = this._busy;
        input.value = this._values[z.zone] || ""; input.setAttribute("aria-label", `${w[z.zone.toLowerCase()] || z.zone} (kWh)`);
        input.addEventListener("input", () => {this._values[z.zone] = input.value; this._sourceToken = null;this._sourceInvalid=false;const button=form.querySelector("button[type=submit]");if(button)button.disabled=this._busy||this._energyLocked;const notice=card.querySelector(".source-notice");if(notice&&!this._energyLocked)notice.remove();});
        label.append(input); form.append(label);
      }
      const prepare = this.button(w.prepare, () => {}); prepare.className="primary"; prepare.type = "submit";prepare.disabled=this._busy||this._energyLocked||this._sourceInvalid; form.append(prepare);
      form.addEventListener("submit", e => {e.preventDefault(); this.act("prepare");}); card.append(form);
      if (!this._config.embedded) card.append(this.button(w.calibrate, () => this._energyEditor?.calibrate({...this._values})));
    }
    if (!this._draft) { const refresh=this.button(this._uncertain ? w.recover : w.load, () => this.act("context")); if (this._config.embedded && ctx && !this._uncertain) { const more=node("details"), summary=node("summary", this._hass?.language?.startsWith("uk")?"Оновити дані лічильника":"Refresh meter data"); more.append(summary,refresh); card.append(more); } else card.append(refresh); }
    if (this._busy) card.append(node("p", w.loading));
    if (!this._draft && !this._uncertain) {
      if (!this._energyEditor) {
        this._energyEditor = new (customElements.get("dtek-energy-editor") || DtekEnergyEditor)();
        this._energyEditor.addEventListener("energy-proposal", event => {
          if (this._busy || this._draft || this._uncertain || this._context?.blocked) return;
          this._sourceInvalid=false;this._values = {...event.detail.values}; this._sourceToken = event.detail.token; this.render();
        });
      }
      this._energyEditor.configure(this._hass, this._config.entity, ctx, !!this._config.embedded,{externalSettings:!!this._config.energy_settings_external});
      const form=card.querySelector("form");
      if (this._config.embedded && form) card.insertBefore(this._energyEditor,form); else card.append(this._energyEditor);
    }
    if (result || this._uncertain) {
      if (!this._readback) this._readback=document.createElement("dtek-account-readback");
      this._readback.configure(this._hass, this._config.entity); card.append(this._readback);
    }
    style.textContent+=embeddedCSS+`:host{container-type:inline-size}.fields button.primary{width:100%;margin-right:0}
      @container(max-width:500px){.review-table tr:first-child{display:none}.review-table tr{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:16px 0}.review-table td{white-space:normal;overflow-wrap:anywhere;padding:8px 0}.review-table td:first-child{grid-column:1/-1;font-weight:600}.review-table td::before{content:attr(data-label);display:block;font-size:.8em;color:var(--secondary-text-color);margin-bottom:4px}}`;

    this.shadowRoot.replaceChildren(style, card);
  }
}
if (!customElements.get("dtek-account-readings-card")) customElements.define("dtek-account-readings-card", DtekAccountReadingsCard);
window.customCards = window.customCards || [];
if (!window.customCards.some(card => card.type === 'dtek-account-readings-card')) window.customCards.push({type:'dtek-account-readings-card', name: "DTEK meter readings", description: "Review and explicitly confirm personal meter readings (HA administrators)."});
