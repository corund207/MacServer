# MacServer Phased Build Plan

1. Main infrastructure and server foundation.
2. Supabase-compatible data services and managed-project migration tooling.
3. Tailscale-only administration app.
4. Local fullscreen operations dashboard.
5. Resilience: encrypted backups, restore drills, updates, and recovery automation.
6. Authorized non-destructive security audit and safe hardening.
7. End-to-end verification and production-readiness review.

Each phase runs in a fresh Codex task. The next task must read all `AGENTS.md` files and the prior phase's context files before making changes. The coordinator must not start a new phase while the account usage window is near exhaustion.
