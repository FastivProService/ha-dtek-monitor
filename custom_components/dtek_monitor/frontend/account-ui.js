/* Shared presentation helpers. No credentials, account state or network calls. */
export function zoneKey(zone) {
  return ({DAY_2_ZONE: "DAY", NIGHT_2_ZONE: "NIGHT"})[zone] || zone;
}
export function isUkrainian(language) {
  return ["uk", "ru"].includes(String(language || "en").toLowerCase().split(/[-_]/)[0]);
}
export function languageLocale(language) {
  return isUkrainian(language) ? "uk-UA" : (language || "en");
}
export function cabinetGroupNotice(attributes, language) {
  const extra=attributes?.unverified_cabinet_groups || [];
  if (!extra.length) return "";
  const matched=attributes.verified_cabinet_groups || [];
  const labels=groups=>groups.map(g=>String(g).replace(/^GPV/, "")).join(", ");
  if (isUkrainian(language)) return [
    matched.length ? `Для будинку підтверджено групи з кабінету: ${labels(matched)}.` : "",
    `Кабінет указує ${labels(extra)}, але дані будинку їх не підтверджують. Ці групи не використовуються в особистому графіку та сповіщеннях бота.`,
  ].filter(Boolean).join(" ");
  return [
    matched.length ? `Cabinet groups confirmed for this building: ${labels(matched)}.` : "",
    `The cabinet lists ${labels(extra)}, but the building data does not confirm them. These groups are excluded from the personal schedule and bot notifications.`,
  ].filter(Boolean).join(" ");
}
export function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
export function number(value, language = "en") {
  if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return "—";
  return new Intl.NumberFormat(languageLocale(language), {maximumFractionDigits: 3}).format(Number(value));
}
export function date(value, language = "en", time = false) {
  if (!value) return "—";
  const stamp = new Date(value);
  if (!Number.isFinite(stamp.getTime())) return "—";
  return new Intl.DateTimeFormat(languageLocale(language), {
    dateStyle: "medium", ...(time ? {timeStyle: "short"} : {}), timeZone: "Europe/Kyiv",
  }).format(stamp);
}
export const embeddedCSS = `:host([embedded]) ha-card{box-shadow:none;border:0;background:transparent;border-radius:0;padding:0}
  :host([embedded]) .card-title{display:none}
  button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:2px solid var(--primary-color,#0288d1);outline-offset:3px}
  button.primary{background:var(--primary-color,#0288d1);color:var(--text-primary-color,#fff);border-color:transparent;font-weight:600}
  button,summary{min-height:44px}button{touch-action:manipulation}[hidden]{display:none!important}`;
