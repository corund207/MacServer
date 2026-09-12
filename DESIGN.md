---
name: MacServer Operations
description: A restrained evidence-first instrument board for private administration and local appliance status.
colors:
  ground: "#0a1015"
  surface: "#101820"
  surface-raised: "#16222c"
  divider: "#263744"
  divider-strong: "#3a4e5d"
  ink: "#ecf3f7"
  muted: "#9eb0bb"
  verified-mint: "#75dec4"
  secondary-blue: "#79aef8"
  attention-amber: "#f4c474"
  failure-red: "#ff8d87"
  selection-ink: "#07100e"
typography:
  display:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "clamp(32px, 4.1vw, 66px)"
    fontWeight: 680
    lineHeight: 1.02
    letterSpacing: "-0.04em"
  metric:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "clamp(24px, 2.3vw, 38px)"
    fontWeight: 620
    lineHeight: 1.1
    letterSpacing: "-0.035em"
  title:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "17px"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-0.015em"
  body:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "12px"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "0.1em"
rounded:
  badge: "5px"
  navigation: "7px"
  control: "8px"
  mark: "11px"
  panel: "14px"
  round: "50%"
spacing:
  micro: "4px"
  compact: "8px"
  small: "12px"
  medium: "16px"
  large: "20px"
  section: "24px"
  region: "28px"
  frame: "36px"
  major: "48px"
components:
  identity-mark:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.verified-mint}"
    rounded: "{rounded.mark}"
    size: "38px"
  freshness-cell:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink}"
    padding: "14px"
  plot-surface:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "12px"
    height: "230px"
  service-cell:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    padding: "15px 16px"
  status-healthy:
    textColor: "{colors.verified-mint}"
    typography: "{typography.label}"
  status-attention:
    textColor: "{colors.attention-amber}"
    typography: "{typography.label}"
  status-failed:
    textColor: "{colors.failure-red}"
    typography: "{typography.label}"
---

# Design System: MacServer Operations

## Overview

**Creative North Star: "The Evidence Instrument Board"**

MacServer uses a restrained blue-black operations world in which evidence is the interface. The local dashboard is the clearest expression: one continuous, line-divided instrument board that states what is known, how fresh it is, and what needs attention before exposing detail. The private admin inherits the same quiet system vocabulary while adding navigation, forms, and a user-selectable light theme for task work.

The visual language is sober, dense, and native to the operating system rather than branded through illustration. Status colors carry defined meanings; typography and rules do most of the organizing. Unavailable, unverified, and stale are first-class states, never cosmetically converted into reassurance.

**Key Characteristics:**

- Blue-black operational surfaces with fine, low-contrast dividers.
- System UI typography, compact uppercase labels, and tabular measurements.
- Mint for verified health, amber for attention, red for failure, and blue for secondary data.
- Flat, continuous regions with depth reserved for the live plot.
- Explicit provenance, freshness, and unavailable language at the point of use.
- No decorative imagery or motion.

## Colors

The dark palette is cool and quiet so that evidence states remain legible without turning the screen into a field of badges.

### Primary

- **Verified Mint:** The scarce affirmative accent for verified healthy state, the identity mark, and the primary CPU series. It never means merely present, connected, or assumed healthy.

### Secondary

- **Secondary Data Blue:** The memory series, informational timeline markers, and the native focus outline. It supports comparison without competing with the current verdict.

### Tertiary

- **Attention Amber:** Stale evidence, degraded state, and items requiring review.
- **Failure Red:** Verified failure and critical events. Red is not used for absence of evidence unless a display transport failure is itself verified.

### Neutral

- **Blue-Black Ground:** The continuous dashboard canvas and the base for the evidence strip.
- **Instrument Surface:** Plot and service-cell fill against the ground.
- **Raised Instrument Surface:** A reserved darker-blue raised layer; use only when the implementation needs a distinct surface tier.
- **Primary Ink:** High-contrast headings, measurements, and service names.
- **Muted Evidence Text:** Sources, timestamps, explanatory copy, and secondary labels.
- **Divider / Strong Divider:** Fine section structure and stronger scrollbar or boundary affordances.
- **Selection Ink:** The dark foreground paired with mint text selection.

The admin surface keeps these semantic roles but uses its own existing dark and light aliases. Do not copy dashboard literals into the admin theme map without checking both themes.

**The Evidence Color Rule.** Mint means verified healthy, amber means attention, red means verified failure, and blue means secondary or informational data; never use these colors as decoration.

**The Text-Plus-Color Rule.** Every status color is paired with a visible word such as NOMINAL, ATTENTION, ACTION REQUIRED, UNVERIFIED, healthy, degraded, failed, or unavailable.

## Typography

**Display Font:** System UI sans serif
**Body Font:** System UI sans serif
**Label/Mono Font:** System UI sans serif with tabular numerals where measurements or time must align

**Character:** Native typography keeps this appliance interface immediate and dependable. Hierarchy comes from disciplined size, weight, tracking, and casing rather than a display typeface.

### Hierarchy

- **Display:** A tightly tracked, balanced verdict headline. Reserve it for the page-level operational conclusion.
- **Metric:** Large, medium-weight tabular values that stay on one line and scan as a ruled strip.
- **Title:** Compact section headings for trend, attention, and service regions.
- **Body:** The default reading voice for explanations and operational context; verdict detail is capped at roughly 70 characters.
- **Label:** Small uppercase headings and states with generous tracking. Keep sentence-case explanatory text separate.

**The Measurement Rule.** Times, freshness, percentages, and table values use tabular numerals; labels never carry the burden of aligning changing data.

**The Native Voice Rule.** Use the platform system stack only. Do not introduce webfonts, ornamental display faces, or faux-terminal monospace styling.

## Layout

The local dashboard is a single centered frame capped at 1720px, with 36px horizontal padding at wide widths. Horizontal rules divide identity, verdict, vitals, operating detail, services, and trust-boundary footer into one continuous instrument board. The primary operating grid gives the trend roughly 1.65 times the width of the attention rail; the verdict uses a similar 1.6-to-0.7 relationship between conclusion and freshness evidence.

At 1050px, the verdict and operating grid become single-column, five vitals become a three-column grid, and services become two columns. At 620px, the frame uses 16px horizontal padding, vitals use two columns with the fifth spanning both, freshness becomes a vertical list, services become one column, and the footer stacks. Content order and semantics stay identical; there is no horizontal scrolling requirement.

The admin surface uses a 236px navigation rail and a content frame capped at 1600px, collapsing its rail into a horizontal overflow-safe navigation row at 700px. Reuse the shared density rhythm—compact labels, 12–24px component padding, and 24–36px region spacing—while allowing each surface to keep its task-appropriate structure.

**The First-Viewport Rule.** On the local display, preserve the sequence verdict, freshness, vitals, trend, actionable issue, and service plane; evidence priority outranks symmetry.

## Elevation & Depth

The system is flat by default. Ground, fills, one-pixel dividers, and gaps create structure. The local trend plot alone uses a broad, low-opacity shadow to read as a live signal surface; admin panels remain bordered and unshadowed.

### Shadow Vocabulary

- **Live Plot:** `0 18px 45px rgba(0,0,0,.28)` gives the only persistent lift to the plotted evidence surface.

**The One Lift Rule.** Persistent shadow belongs only to live plotted evidence; do not turn cards, service cells, navigation, or status blocks into floating tiles.

## Shapes

Corners are modest and functional. Dashboard plots and service matrices use 14px outer corners, the identity mark uses 11px, and the accessible skip link uses 8px. Admin panels use 12px, controls use 8px, navigation uses 7px, and badges use 5px. Circular geometry is limited to tiny status and timeline dots. One-pixel borders and dividers remain the dominant silhouette.

**The Ruled-Board Rule.** Prefer continuous sections divided by one-pixel lines over collections of individually elevated cards.

## Components

### Identity Mark

- **Shape:** A 38px square with an 11px corner radius and a one-pixel mint border.
- **Color:** Mint initial on the ground; it is a quiet locator, not a logo illustration.
- **Behavior:** No hover or motion. It is hidden from assistive technology because the adjacent name carries the identity.

### Verdict and Freshness

- **Verdict:** One large conclusion, one uppercase state word, and one muted evidence explanation.
- **Freshness:** Three definition-list cells separated by a one-pixel divider gap, each containing an uppercase key and a tabular or wrapping value.
- **State:** Healthy changes only the state word to mint; attention is amber; failure is red. Unknown data retains explicit unavailable language.

### Vitals Strip

- **Structure:** Five equal ruled regions at wide widths, with measurements as large tabular values and muted source notes beneath.
- **Responsive behavior:** Three columns at medium widths, two columns at small widths, with the final vital spanning the row.
- **State:** A missing observation says Collecting or Unavailable and preserves its source note.

### Live Plot

- **Surface:** Instrument surface, 14px corners, 12px padding, and the sole persistent shadow.
- **Series:** Mint for CPU and blue for memory, both two pixels wide over one-pixel divider-colored guides.
- **Accessibility:** The canvas exposes a changing text alternative with sample count and latest values; missing samples break the line instead of being interpolated.

### Alert Timeline

- **Structure:** Small semantic dots joined by a one-pixel vertical rule, with a strong title and muted detail.
- **State:** Amber is the default attention item, red marks critical failure, and blue marks information.
- **Overflow:** The region scrolls after 294px while retaining newest verified issues first.

### Service Matrix

- **Structure:** A line-backed grid of flat surface cells, four columns wide, collapsing to two and then one.
- **Content:** Service name, uppercase state, and a clipped single-line detail. Missing observations render an Unavailable state and explanatory sentence.

### Admin Controls

- **Shape:** Native-feeling 8px controls with one-pixel borders and at least 42px button height.
- **Primary:** The admin accent fills the button; ordinary controls stay on the ground and use a subtle hover fill.
- **Focus:** Keep the browser-native interaction model with an explicit three-pixel solid focus outline and four-pixel offset.
- **Disabled:** Muted text, dashed border, no false affordance, and copy that names the unavailable action.

### Admin Navigation

- **Style:** Muted links become primary ink on hover or current state. The current view adds a three-pixel inset accent rule rather than a detached pill.
- **Responsive behavior:** The fixed rail becomes a horizontal, overflow-safe row on small screens.

## Do's and Don'ts

### Do:

- **Do** lead with the strongest supported verdict and place its source and freshness nearby.
- **Do** preserve stale last-known values while clearly labeling the interrupted or stale transport state.
- **Do** use lines, spacing, and typographic contrast as the main hierarchy tools.
- **Do** pair every semantic color with explicit text and retain keyboard-visible focus.
- **Do** keep refresh as a data update; honor reduced-motion preferences and preserve stable layout.

### Don't:

- **Don't** present unavailable, missing, or stale evidence as healthy.
- **Don't** use mint, amber, red, or blue as decorative accents detached from their semantic roles.
- **Don't** add decorative animation, pulsing status, ornamental gradients, illustrations, or fake telemetry.
- **Don't** add shadows to ordinary cards or split the local dashboard into a floating-card mosaic.
- **Don't** imply controls or credentials on the read-only local display.
