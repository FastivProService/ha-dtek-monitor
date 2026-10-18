/* Shared presentation helpers. No credentials, account state or network calls. */
export function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
export function number(value, language = "en") {
  if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return "—";
  return new Intl.NumberFormat(language, {maximumFractionDigits: 3}).format(Number(value));
}
export function date(value, language = "en", time = false) {
  if (!value) return "—";
  const stamp = new Date(value);
  if (!Number.isFinite(stamp.getTime())) return "—";
  return new Intl.DateTimeFormat(language, {
    dateStyle: "medium", ...(time ? {timeStyle: "short"} : {}), timeZone: "Europe/Kyiv",
  }).format(stamp);
}
export const embeddedCSS = `:host([embedded]) ha-card{box-shadow:none;border:0;background:transparent;border-radius:0;padding:0}
  :host([embedded]) .card-title{display:none}
  button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:2px solid var(--primary-color,#0288d1);outline-offset:3px}
  button.primary{background:var(--primary-color,#0288d1);color:var(--text-primary-color,#fff);border-color:transparent;font-weight:600}
  button,summary{min-height:44px}button{touch-action:manipulation}[hidden]{display:none!important}`;
