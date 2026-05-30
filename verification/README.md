# Verification — Kane CLI Specs

Plain-English browser automation specifications for end-to-end visual testing.

## Files

- `asymmetric_swarm_verification.md` — Full dashboard verification flow

## Running

```bash
kane run verification/asymmetric_swarm_verification.md --output=ndjson
```

## Test Flow

1. Load dashboard at localhost:3000
2. Verify leader/follower panels render
3. Trigger exploration simulation → verify canvas paths
4. Trigger follower disconnect → verify OFFLINE badge
5. Trigger target found → verify COOPERATIVE_ENGAGED state
