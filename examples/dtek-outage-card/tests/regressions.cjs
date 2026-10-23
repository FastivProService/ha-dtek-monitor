const assert = require('node:assert/strict');

module.exports = async function regressions(page) {
  await page.reload();
  await page.locator('.interval').first().waitFor();
  const focus = await page.evaluate(async () => {
    const card = document.querySelector('dtek-outage-card');
    const select = card.shadowRoot.querySelector('select');
    const option = select.options[1];
    select.focus();
    card.hass = { ...window.demoHass, states: { ...window.demoHass.states, 'sensor.other': { state: '123' } } };
    const unrelated = card.shadowRoot.activeElement === select && select.options[1] === option;
    await card._load();
    return { unrelated, refreshed: card.shadowRoot.activeElement === select && select.options[1] === option };
  });
  assert.deepEqual(focus, { unrelated: true, refreshed: true });

  await page.locator('select').selectOption('GPV1.1');
  await page.evaluate(() => {
    const card = document.querySelector('dtek-outage-card');
    card.setConfig({ entity: 'binary_sensor.other_power', status_entity: 'sensor.other_status' });
  });
  await page.waitForFunction(() => !document.querySelector('dtek-outage-card')._busy);
  assert.equal(await page.locator('select').inputValue(), 'GPV2.2');

  // Exercise old success AND old failure after switching either user or connection.
  for (const change of ['user', 'connection']) {
    for (const rejectOld of [false, true]) {
      const result = await page.evaluate(async ({ change, rejectOld }) => {
        const card = document.querySelector('dtek-outage-card');
        let resolveOld, rejectPrevious;
        const oldResult = new Promise((resolve, reject) => { resolveOld = resolve; rejectPrevious = reject; });
        const old = { states: window.demoHass.states, user: { id: 'old' }, connection: {}, callWS: () => oldResult };
        card.hass = old;
        const freshData = structuredClone(window.demoData);
        freshData.marker = 'new-session';
        let newCalls = 0;
        card.hass = {
          ...old,
          user: { id: change === 'user' ? 'new' : 'old' },
          connection: change === 'connection' ? {} : old.connection,
          callWS: async () => { newCalls++; return freshData; },
        };
        await new Promise((resolve) => setTimeout(resolve, 0));
        if (rejectOld) rejectPrevious(new Error('old failure'));
        else resolveOld({ ...window.demoData, marker: 'old-session' });
        await new Promise((resolve) => setTimeout(resolve, 0));
        return { marker: card._data?.marker, busy: card._busy, error: card._error, newCalls };
      }, { change, rejectOld });
      assert.deepEqual(result, { marker: 'new-session', busy: false, error: '', newCalls: 1 });
    }
  }

  const refreshContext = await page.evaluate(async () => {
    const card = document.querySelector('dtek-outage-card');
    let finish;
    const waiting = new Promise((resolve) => { finish = resolve; });
    let oldGridCalls = 0;
    const old = { ...window.demoHass, user: { id: 'refresh-old' }, connection: {}, callWS: (request) => {
      if (request.type.endsWith('schedule_refresh')) return waiting;
      oldGridCalls++; return Promise.resolve(window.demoData);
    } };
    card.hass = old;
    await new Promise((resolve) => setTimeout(resolve, 0));
    const initial = oldGridCalls;
    const pending = card._load(true);
    card.hass = { ...old, user: { id: 'refresh-new' }, callWS: async () => window.demoData };
    finish({});
    await pending;
    await new Promise((resolve) => setTimeout(resolve, 0));
    return oldGridCalls === initial;
  });
  assert.equal(refreshContext, true);

  await page.evaluate(async () => {
    const start = Date.parse('2026-10-24T21:00:00Z');
    const slots = Array.from({ length: 50 }, (_, i) => {
      const time = new Date(start + i * 1800000);
      const label = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Kyiv', hour: '2-digit', minute: '2-digit' }).format(time);
      return { label, offset: i < 8 ? '+0300' : '+0200', start: time.toISOString(), end: new Date(time.getTime() + 1800000).toISOString() };
    });
    const data = { status: 'ready', days: [{ date: '2026-10-25', slots, rows: [{ id: 'GPV2.2', label: 'Черга 2.2', home: true, cells: slots.map((_, i) => i === 7 ? 'outage' : 'clear') }] }] };
    const card = document.querySelector('dtek-outage-card');
    card.hass = { ...window.demoHass, user: { id: 'dst' }, connection: {}, callWS: async () => data };
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  assert.equal(await page.locator('.interval b').innerText(), '03:30 UTC+0300–03:00 UTC+0200');
  assert.equal(await page.locator('.cell').count(), 50);
  console.log('PASS: stable controls/focus, address reset, user/connection races, refresh cancellation, DST boundary offsets.');
};
