# Purview SDK (processContent) and DLP

OpenTelemetry carries telemetry; it never enforces policy. To make a DLP policy **block** a prompt or response, the agent
must call the Purview Graph APIs itself, before the LLM and after it. Template: `agent/purview.py` and `agent/guard.py`.

## Calls (Graph v1.0, delegated, agent identity OBO for the user)

| Step | API | Notes |
|---|---|---|
| 1 | `POST /me/dataSecurityAndGovernance/protectionScopes/compute` | Body: activities `uploadText,downloadText`, `locations=[{"@odata.type":"#microsoft.graph.policyLocationApplication","value":"<app id>"}]`. Cache per user for 60 minutes with the ETag. Returns `executionMode` per activity: `evaluateInline` or `evaluateOffline` |
| 2a | `POST /me/dataSecurityAndGovernance/processContent` | Inline: wait for it and honor `policyActions` (`restrictAccess` with `action: block`). Offline: fire it in the background. Send `If-None-Match: <etag>`. `protectionScopeState: modified` clears the cache |
| 2b | `POST /me/dataSecurityAndGovernance/activities/contentActivities` | When no scope applies, metadata only (`PURVIEW_LOG_WHEN_UNSCOPED`) |

Payload essentials:
- `contentToProcess.contentEntries[0]` is a `processConversationMetadata` with `content.data`, `correlationId` = conversation ID,
  `sequenceNumber`, `isTruncated`, `createdDateTime` and `modifiedDateTime`.
- `activityMetadata.activity`: `uploadText` for prompts, `downloadText` for responses.
- `deviceMetadata.ipAddress`.
- `protectedAppMetadata` with `name`, `version` and `applicationLocation` = the policyLocationApplication value. This defaults to the agent
  identity appId (`PURVIEW_APP_LOCATION_ID` overrides it) and **must match the DLP policy location**.
- `integratedAppMetadata` and `aiAgentInfo` (agent ID and blueprint ID).

Scopes (`PURVIEW_OBO_SCOPE`): `https://graph.microsoft.com/Content.Process.User ContentActivity.Write ProtectionScopes.Compute.User`,
granted by blueprint inheritance (see registration.md). An AADSTS65001 error means consent or inheritance is missing.

## Statuses shown in the UI meta line (`purview: prompt:X · response:Y`)

`inline-allowed`, `inline-blocked`, `offline-sent`, `no-scope-activity-logged`, `no-scope`, `error-blocked` (fail-closed),
`error: …` (fail-open, the default for demos; `PURVIEW_FAIL_CLOSED=true` for production).

## DLP policy for the agent

The portal UI can't create inline-blocking DLP for Entra-registered apps. A policy created in the portal yields only
`evaluateOffline`. Use Security & Compliance PowerShell:

1. Create a custom SIT (portal → Data classification → Classifiers → Sensitive info types), e.g. `Demo - Restricted City`,
   with the keyword "Dallas" (or the new domain's blocked value) at high confidence.
2. Run `.\scripts\New-PurviewDlpDemo.ps1 -WhatIf`, then run it for real. It runs `Connect-IPPSSession`, then
   `New-DlpCompliancePolicy -Locations <Entra app JSON> -EnforcementPlanes Application`, then
   `New-DlpComplianceRule -ContentContainsSensitiveInformation @{Name=<SIT>} -RestrictAccess @(@{setting='UploadText';value='Block'},@{setting='DownloadText';value='Block'}) -GenerateAlert`.
   The location JSON is `[{"Workload":"Applications","Location":"<agent app id>","LocationDisplayName":"<name>"}]`.
3. Wait for propagation, typically 15 to 60 minutes. The protectionScopes ETag changes, and the next compute returns `evaluateInline`.
4. Demo: "weather in San Francisco" is answered (`inline-allowed`). "weather in Dallas" returns the block message
   (`inline-blocked`). Show the DLP alert and the AI activities row in Purview.

Prerequisites: Purview pay-as-you-go billing or DSPM for AI may need to be enabled. Opt in under DSPM → Getting started.
The presenter needs the content viewer role to read the blocked text (see observability.md).
`Connect-IPPSSession` doesn't accept an az token and fails in headless terminals because of WAM, so run it in a real console window.
