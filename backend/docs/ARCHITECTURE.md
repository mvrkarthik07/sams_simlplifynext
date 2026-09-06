# Deadbolt architecture

```text
  AWS IAM / GitHub / Slack / Notion / fixture captures
                         |
                         v
              snapshot + identity normalization
                         |
                         v
                 graph store (DDB/S3)
                         |
                         v
       +-------------------------------------------+
       | pure drift engine + integer risk scoring  |
       | deterministic plan builder + SHA-256 hash  |
       +-------------------------------------------+
                         |
                         v
                 findings / plan / audit
                    |             |
                    v             v
             Dashboard API   Approval broker
                                  |
                         bounded LLM proposal
                                  |
                         closed-graph validation
                                  |
                                  v
                         executor -> verify -> rollback
```

## Deterministic boundary

The provider and graph layers supply normalized immutable entitlements. Once the engine receives those records and an explicit UTC `evaluated_at`, risk scoring, tier selection, finding order, action order, canonical JSON, and `plan_hash` are deterministic. The plan envelope may contain a fresh `plan_id`, timestamp, trace ID, and attempt; those mutable fields are excluded from the hash.

The broker is the only request-path location allowed to use an LLM. Its output is treated as an untrusted proposal: it must remain inside the existing graph, cannot widen scope, and cannot bypass approval. The LLM does not compute risk, choose action ordering, hash plans, call providers, or verify rollback.

## Demo boundary

The currently published dashboard and MCP links predate the authenticated redeploy and must be
treated as legacy demo surfaces. The current stack can require Cognito bearer authentication for
both boundaries, and a redacted GitHub capture is loaded as read-only evidence. A live provider
must not be exposed until the post-submission M14 prerequisites are complete: SSM SecureString
retrieval, least-privilege IAM, an expiring fine-grained PAT, and a separately recorded rehearsal.
