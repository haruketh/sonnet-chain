# Sonnet Mission Web v0.1

The public path is one-way: the private participant reads its SQLite state, deterministically constructs public schema v1, atomically stages `state/public/sonnet-latest.json`, and may publish that validated file to the dedicated Cloudflare KV key `sonnet/latest.json`. No LLM participates. The Web Worker only reads KV and cannot access the runtime, filesystem, or write credential.

The schema contains mission status/stage, bounded counters, and at most 40 public-safe activities. Sources are structured formation events, Reflex processing records, application requests, active formation state, and phase/completion/submission metadata. Incoming text, signatures, DIDs, request IDs, secrets, paths, thresholds, and provider responses are excluded.

Run manually:

```bash
.venv/bin/python -m sonnet_chain.sonnet_public_export --dry-run
.venv/bin/python -m sonnet_chain.sonnet_public_export
.venv/bin/python -m sonnet_chain.sonnet_public_export --publish
```

Publication uses dedicated `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_SONNET_KV_NAMESPACE_ID`, and `CLOUDFLARE_SONNET_API_TOKEN` environment variables. The production run-once launchd schedule should invoke the non-interactive `--publish` command every 300 seconds. Cloudflare resources, secrets, deployment, and launchd installation are manual activation steps and are not performed by this implementation.

For launchd, pass `--credentials-file state/private/sonnet_cloudflare.json`. Create `state/private` owned by the runtime user with mode `0700`, and the JSON file with mode `0600`. It contains exactly the three configuration keys above and is ignored under `state/`; the exporter rejects symlinks, other owners, broad permissions, oversized files, and extra keys. Do not put the token in a plist or source a shell file. Environment variables remain available for manual diagnostics.

The dedicated Sonnet API token is restricted to the Second Session Cloudflare account with the account-level permission **Workers KV Storage Edit**. Cloudflare does not scope this permission to one KV namespace; application code still writes only the fixed Sonnet namespace/key configured above.
