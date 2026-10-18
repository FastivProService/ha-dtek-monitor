/* Fictional accounts for local UI tests and the interactive preview only. */
window.calls = [];
window.accounts = [1, 2].map(n => ({
  entity_id: `sensor.account_${n}`, masked: `••••000${n}`,
  address: `Одеса · Прикладна, ${n === 1 ? 12 : 24}, кв. ${n}`, can_submit: true,
  entities: [`sensor.account_${n}`, `sensor.reading_${n}`],
  roles: {"account_updated:all": `sensor.account_${n}`, "account_reading:GENERAL": `sensor.reading_${n}`,
    "account_consumption:all": `sensor.consumption_${n}`, "account_schedule_groups:all": `sensor.group_${n}`,
    "account_schedule_status:all": `sensor.schedule_${n}`, "account_outage_schedule:all": `calendar.personal_${n}`},
  building_schedule: {schedule_status: `sensor.building_${n}`, outage_schedule: `calendar.building_${n}`},
}));
const states = {};
for (const n of [1, 2]) {
  states[`sensor.account_${n}`] = {state: "2026-10-18T12:00:00+03:00", attributes: {}};
  states[`sensor.reading_${n}`] = {state: String(12400 * n), attributes: {reading_date: "2026-10-17", zone: "GENERAL"}};
  states[`sensor.consumption_${n}`] = {state: "85", attributes: {period: "2026-09"}};
  states[`sensor.group_${n}`] = {state: "GPV3.1", attributes: {selected_groups: ["GPV3.1"], building_groups: ["GPV3.1", "GPV5.1"]}};
  states[`sensor.schedule_${n}`] = {state: "schedule_missing", attributes: {selected_groups: ["GPV3.1"]}};
  states[`sensor.building_${n}`] = {state: "missing", attributes: {}};
}
states["sensor.grid"] = {state: "2100.5", attributes: {friendly_name: "Лічильник на вводі", device_class: "energy", state_class: "total_increasing", unit_of_measurement: "kWh"}};
window.hass = {
  language: "uk", user: {id: "preview", is_admin: true}, states,
  callWS: async msg => {
    calls.push(structuredClone(msg));
    const n = msg.entity_id?.endsWith("2") ? 2 : 1;
    if (msg.type === "dtek_monitor/accounts") return structuredClone(accounts);
    if (msg.type === "dtek_monitor/account_identity") return {masked: `••••000${n}`, ...(msg.reveal ? {number: `100000000${n}`} : {})};
    if (msg.type === "dtek_monitor/account_profile") return {
      address: accounts[n - 1].address, available: true, metadata_available: true,
      model: "Лічильник прикладу", serial: msg.reveal ? "DEMO-001" : "••••0001",
      eics: [msg.reveal ? "56X0000000000001" : "••••0001"], zone_scheme: "Одна зона",
      contract_power_kw: "5", next_verification_year: 2030, fetched_at: "2026-10-18T12:00:00+03:00", seals: [],
    };
    if (msg.type === "dtek_monitor/account_submission") {
      if (msg.action === "context") return {account: `••••000${n}`, address: accounts[n - 1].address, serial: `DEMO-${n}`, date: "2026-10-18", digits: 6,
        revision: `revision-${n}`, blocked: false, zones: [{zone: "GENERAL", previous: String(12400 * n), date: "2026-10-17"}]};
      if (msg.action === "prepare") return {confirmation: `draft-${n}`, readings: [{zone: "GENERAL", previous: String(12400 * n), value: msg.values.GENERAL, increase: String(Number(msg.values.GENERAL) - 12400 * n)}]};
      if (msg.action === "confirm") return {status: "accepted", at: "2026-10-18T12:05:00+03:00"};
    }
    if (msg.type === "dtek_monitor/account_energy") {
      if (msg.action === "get") return {binding: {serial: `DEMO-${n}`, mode: "offset", freshness: "reported", revision: "binding-1", history: [], max_age: 900, max_power_kw: "15", zones: {GENERAL: {entity_id: "sensor.grid", anchor_meter: "12400", anchor_source: "2000"}}},
        ready: true, proposal: "proposal-1", readings: {GENERAL: {value: "12500.5", send_value: "12500", source: {value: "2100.5", at: "2026-10-18T12:00:00+03:00"}}}};
    }
    if (msg.type === "dtek_monitor/account_history") return {year: msg.year, fetched_at: "2026-10-18T12:00:00+03:00", months: Array.from({length: 12}, (_, i) => ({period: `${msg.year}-${String(i + 1).padStart(2, "0")}`, status: i < 9 ? "closed" : "open", value: i < 9 ? String([185, 142, 96, 25, 0, 0, 4, 0, 85][i]) : null, zones: {}})),
      readings: Array.from({length: 4}, (_, i) => ({date_time: `2026-0${9 - i}-30T00:00:00+03:00`, value: String(12400 - i * 85), status: "active", zone: "GENERAL", energy: "active", source: "Особистий кабінет"}))};
    if (msg.type === "dtek_monitor/account_readback") return {status: "no_attempt", latest: [{value: "PRIVATE-DUPLICATE"}], matches: [], fetched_at: "2026-10-18T12:00:00+03:00"};
    if (msg.type === "dtek_monitor/account_checks") return {status: "never", history: []};
    throw Object.assign(new Error("Unsupported preview operation"), {code: "unavailable"});
  },
};
