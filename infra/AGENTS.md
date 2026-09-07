# Infrastructure Agent Instructions

Infrastructure changes must be declarative, version-pinned, idempotent, and reviewable. Default to no public listeners except the explicitly documented HTTPS application endpoint. Validate Docker Compose, systemd units, firewall rules, Tailscale policy, backup mounts, and health checks before applying changes. Never place secrets in tracked files; provide `.env.example` and a secure secret-generation/bootstrap flow.
