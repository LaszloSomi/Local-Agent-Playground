# Conditional Access for the agent and its users

There are two independent enforcement points. Show both and say which one fires.

## A. User sign-in CA (Alice allowed, Bob blocked)

CA is evaluated when the user signs into the web client and requests a token for the blueprint scope. The agent never
runs for a blocked user.

1. **Named location** `Corp network`: the office or VM public egress IPs, marked trusted. `curl https://ifconfig.me` on the demo machine shows the IP.
2. **Policy** `Demo - <Agent> requires managed device or corp network`:
   - Users: a demo group with Alice and Bob. Exclude break-glass accounts.
   - Target resources: `<Agent>-WebClient` **and** the blueprint resource (`api://<blueprint>`).
   - Network: include *Any location*, exclude `Corp network`.
   - Grant: *Require device to be marked as compliant* (or hybrid joined).
   - Mode: **Report-only** first, then On.
3. Alice uses an Intune-compliant laptop or the corp network. Bob uses an unmanaged VM or a phone hotspot.
4. Evidence: Bob's AADSTS53000 (device) or AADSTS53003 (blocked) page. Entra → Sign-in logs → the CA tab. Use the
   **What If** tool for both users. In the Diagnostics tab, the policy shows *applies* under "user sign-in".

## B. Agent identity CA (Entra Agent ID, preview)

This targets the **agent's own** token requests (T1 → OBO).

- In policy JSON: `conditions.clientApplications.includeAgentIdServicePrincipals` is `["All"]` or specific agent IDs, or
  `agentIdServicePrincipalFilter` is a custom-security-attribute rule such as `AgentApprovalStatus -eq "IT_Approved"`.
  Also `conditions.agentIdRiskLevels` and `applications.includeApplications: ["AllAgentIdResources"]`.
- Demo idea: block all agents **not** IT-approved, then set the custom security attribute on this agent identity to approve
  it. Setting the attribute needs the Attribute Assignment Administrator role. Show the OBO failure in the app log before approval, and success after.
- `conditions.agents.includeAgentUsers` applies only to agents with agentic users. A third-party OBO agent has none.

## Tips

- Always keep the presenter's admin account excluded, and test in report-only.
- CA changes take a few minutes. Sign out of the app (and restart it to clear sessions) before retesting.
- The Diagnostics tab's CA evaluation is static (assignments only). The sign-in logs are the proof.
