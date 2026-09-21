# Phase 10 — brand and installation site

Delivered an original MacServer mark/banner and responsive static installation
site. The README is a short entry point; the complete prior instructions live in
docs/GETTING-STARTED.md and are rendered with the detailed runbooks on Pages.

Outfit, GSAP, documentation rendering and browser-test packages were reviewed,
integrity-locked, installed without lifecycle scripts, and audited with no known
advisories. No external browser assets, tracking, or secret collection is used.

Validation: five Playwright tests cover navigation, accordion, code examples,
motion controls, every local page/asset link, three viewport widths, heading wrap,
horizontal overflow, and axe accessibility. Screenshots reviewed locally.
Existing Linux CI passed phase 9. Pages deploys only the static build artifact.

No appliance deployment or real project qualification is implied by the example
workspace. Public application routing is the next delivery unit.
