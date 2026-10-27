# Occupancy panel design

The forecast and journal are paired observational tools. They share IBM Plex
Sans, a restrained green accent, neutral surfaces, 16 px section corners and a
1280 px maximum reading width. Their existing chart colors continue to identify
different people. Fonts and icons remain local, including the font license.

## Reading order

The forecast begins with current presence and the next two hours. The hourly
curve, expected changes and measured accuracy follow. Model coverage and manual
training controls are supporting information below them. Data and Setup retain
their existing functionality and inherit the same layout and control system.

The journal begins with collection health and history span. Resident coverage
stays beside the observations it qualifies. Cooling and exports have distinct
sections. Raw captured payloads remain available in disclosure controls.

## Honest comparisons

Probability error and published coverage remain visible. Percentage comparisons
against persistence are displayed after 30 matched forecasts and only when the
baseline Brier score exceeds 0.000001. This is a display safeguard against tiny
denominators and sparse early records, not a statistical significance threshold.
Raw scores and records are unchanged. Overlapping near-term predictions remain
labelled as non-independent observations.

Changing a person, horizon or time window cannot show another selection's
previous accuracy while the new request loads. Missing forecasts remain blank.

## Interaction and accessibility

- The main tabs support Arrow Left/Right and Home/End with one tab stop.
- All controls retain visible keyboard focus and touch targets.
- Charts retain their existing keyboard and pointer interaction.
- Phone layouts keep important metrics visible and adapt resident table rows.
- Light and dark themes use the same hierarchy. Reduced motion removes the
  short view-entry and hover transitions.
- Loading, empty, unknown, and recoverable error states use explicit text.

## Resource decisions

The existing Home Assistant panel is the primary design reference: its live
heading measured 24 px in IBM Plex Sans, with 12 px card padding and corners.
The revised spacing gives those established controls more room. Craftwork's
[dashboard catalog](https://craftwork.design/catalog/ui-kits/dashboard) and
[Blank 3 Pro](https://craftwork.design/product/blank-3-pro) were reviewed for
quiet dashboard grouping. The existing fonts, MDI paths and charts fit this
product, so no paid template, illustration, shader or remote asset is used.
The existing React components and plain journal JavaScript remain the runtime;
no framework migration or extra animation dependency is needed.
