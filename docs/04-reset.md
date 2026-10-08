# 04 – Reset between demos and clean up

> 📊 Diagrams for this walkthrough: [00-diagrams.html](00-diagrams.html) (open in a browser)

## Quick reset (keep the registration)

**Multi-instance limitation:** this script stops only ports 8000 and 16686. Stop other instances
separately by their verified PID. Tenant cleanup does not remove DLP/CA policies, custom sensitive
info types, content-viewer memberships or billing resources. Inspect all additional identities and
registry entries afterwards; overlays/generated configuration may retain secrets and stale IDs.

```powershell
.\scripts\reset.ps1      # stops the app + Jaeger (in-memory traces are cleared); sessions are cleared
.\scripts\run.ps1
```

Browser: sign out in the UI and close the private window so the next demo starts with a fresh sign‑in.

## Full tenant cleanup (demo registration from scratch)

```powershell
.\scripts\reset.ps1 -Tenant
```

This does the following:

1. `a365 cleanup`: deletes the blueprint, its principal, the agent identity and the registry entry (it prompts for confirmation)
2. Deletes the `Laszlo-AgentRegistryDemo1-WebClient` app registration
3. Blanks the tenant values in `.env` and sets `AUTH_ENABLED=false` and `ENABLE_A365_OBSERVABILITY_EXPORTER=false`

Manual equivalents: `a365 cleanup blueprint` and `a365 cleanup instance` for granular removal. For the web client,
Entra → App registrations → delete.

Notes:

- Deleted apps sit in Entra **Deleted applications** for 30 days. Re‑running `setup all` with the same name creates new IDs.
- Telemetry already ingested into Defender and Purview is **not** removed; it follows those services' retention.
- Secrets created by `Configure-Entra.ps1` expire after 30 days anyway. To rotate them, delete the value in `.env`
  and re‑run the script.

## Moving to a VM

1. Copy the repo (without `.venv`, `tools`, `.env`), or `git clone` it.
2. `.\scripts\setup.ps1` on the VM.
3. Either copy `.env` from the laptop (same registration, same redirect URI `http://localhost:8000/auth/callback`)
   or run `Configure-Entra.ps1` on the VM (reuses the objects and creates new secrets).
4. Present from a browser **on the VM**: the redirect URI is `localhost`. To use it from another machine, add an
   HTTPS redirect URI to the web client and set `APP_HOST` / `WEB_REDIRECT_URI` accordingly.
