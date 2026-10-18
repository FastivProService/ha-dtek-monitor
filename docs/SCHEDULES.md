# Building and apartment schedules

**English** · [Українська](SCHEDULES.uk.md) · [Connection and dashboard](PERSONAL_ACCOUNT.md)

A schedule is the operator's plan, not a measurement of electricity at home.

- [Schedule grid](#section-1)
- [My apartment group and the building schedules](#section-2)

<a id="section-1"></a>
## Schedule grid

The **Schedule** tab now opens a half-hour grid of all groups published by the
selected operator. Building groups have an outline and a **My building** label;
your confirmed personal group also has **My group**. Choosing a row shows its
exact intervals below the grid. This display does not change building groups,
personal selection, calendars or Telegram notifications.

Use **Grid / List** to choose the format. In the grid, choose today or one of the
next six days, then **Groups → My building** to filter rows or **All groups** to
see every group. In **List**, choose **My group** or **My building · all groups**.
Each calendar keeps its own navigation; returning to the grid retains the date,
filter and scroll position. These controls do not change your apartment's group.
On a phone, scroll the grid
horizontally; group labels stay visible. Dates and hours use Europe/Kyiv,
including repeated or skipped hours at daylight-saving changes.

The legend distinguishes planned outages, possible outages, no scheduled
outage, weekly forecasts and missing data. A white cell does not prove the house
has power. Missing, hidden or failed publication never produces a reassuring
empty schedule. Unpublished dates show unknown cells; a connection failure hides
the old grid. The notice above the grid describes the selected date and visible
groups. Only relevant legend entries appear; expand **Update details** for the
source and verification times. The grid reads HA's local snapshot without extra DTEK requests.

**Save PNG** exports the selected date and visible groups with the building
highlights, legend and update times. It contains the building address, not the
apartment number or account number. The saved image is a snapshot and does not
update. Download is unavailable while data is loading or all visible cells are
unknown. Day/filter selections survive ordinary HA updates.

A standalone `custom:dtek-schedule-card` also opens the grid by default; set
`view: list` to retain its previous default. Its `status_entity` and the planned
and possible calendar entities must be enabled and readable by the HA user.

<a id="section-2"></a>
## My apartment group and the building schedules

The cabinet connection does not establish which outage group supplies an apartment.
The verified cabinet responses currently provide no such assignment. A building
may have several groups; its **Primary schedule group** is a compatibility value,
not a claim about your apartment.

1. When HA starts monitoring an account, it matches its city, street and building
   against the operator’s public directory. A unique match connects the building
   automatically; an existing connection is reused, including for multiple
   apartments in the same building. This also applies to accounts selected later.
2. If the address needs confirmation, open **My DTEK → Settings → Building and
   outage group → Choose another building from the directory**. Confirm the city,
   street and building in the same dialog. Apartment numbers and cabinet credentials
   are never included in the public building connection.
3. A sole verified building group is used automatically. With several groups,
   choose your apartment group in the same settings. The building calendars and
   bot keep every group. Manual group choices never change automatically.

A directory outage does not block cabinet data or readings. HA reports that
matching is pending and retries every five minutes. **Do not link a building**
disables automatic linking for this account until you choose a building again;
it does not delete the shared building connection.

The account device exposes **My group**, **My schedule status**,
**My outage schedule** and **My possible outage schedule**. Add the personal
calendars to your apartment dashboard; keep the building calendars alongside them
when you want to compare all groups. The building calendars and Telegram bot keep
all address groups. Different accounts can select different groups in one building.

The status distinguishes a manual choice from automatic use of the building’s
sole group. Neither is an apartment assignment returned by the cabinet. If an
automatically linked building gains a second group, only the personal calendar
waits for a selection; the public building calendars and bot keep all groups.
If a manually selected group disappears from the verified building groups, group
labels are hidden, the building is removed or schedule verification fails, the
personal calendars become unavailable. A saved manual choice remains visible for
review; another group is never substituted for it automatically. The schedule status
explains why. Choosing **Do not link a building** removes only the personal link and prevents automatic relinking.

## Schedules without a personal cabinet

Just [add your building address](../README.md#setup). After restarting HA,
reload its page and add a built-in calendar card or the
[DTEK grid card](REFERENCE.md#schedule-card), using that building's availability
sensor and calendars. No phone login is required. Set `view: list` for a calendar
list; the default is the grid.
