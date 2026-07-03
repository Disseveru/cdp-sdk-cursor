# AGENTS.md

## User context (read first)

The repository owner **does not know how to code**. Treat them as a non-technical
stakeholder and handle all technical work yourself.

- **Do all coding** — implement features, fix bugs, write tests, commit, push, and
  open or update PRs. Do not ask them to edit source files or run terminal commands.
- **No hand-offs** — avoid instructions like "add this to `.env`" or "run `pytest`".
  Do it in the cloud environment unless a secret or portal action is impossible for
  you (e.g. creating a CDP API key in their account).
- **Plain-language updates** — report outcomes simply: what was deployed, which
  addresses/contracts, whether execution is live, and what happens next without jargon.
- **Be proactive** — if something is broken, incomplete, or reverted, fix and merge it
  without waiting for them to diagnose the issue.
- **Secrets** — CDP credentials are injected as cloud secrets when available; configure
  `.env` from `.env.example` yourself when running `desktop-agent/`.

### `desktop-agent/` (liquidation + arbitrage bot)

When the user asks about liquidations, arbitrage, or "completing the subproject":

| Task | Agent action |
|------|----------------|
| Setup | `pip install -r requirements.txt` in `desktop-agent/`, copy `.env.example` → `.env` |
| Tests | `pytest -v` (install `requirements-dev.txt` first) |
| Live agent | `python agent_runner.py` (dashboard on port 8787) |
| One-shot liquidation | `python scripts/run_liquidation.py` |
| Arbitrage monitor | `python scripts/arbitrage_monitor.py` |

Deployed Base mainnet contracts (see `desktop-agent/.env.example` for current addresses):

- Aave `FlashLiquidator`, Morpho `MorphoFlashLiquidator`, Morpho `MorphoFlashArbitrage`
- Moonwell `MoonwellFlashLiquidator` — deploy with `python scripts/deploy_contract.py --moonwell`, set `MOONWELL_FLASH_LIQUIDATOR_ADDRESS`, and add the contract to the CDP paymaster allowlist (same as Aave/Morpho) so sponsored liquidations work
- Moonwell OEV path — deploy `MoonwellOEVFlashLiquidator` via `--moonwell-oev` for WETH collateral during Chainlink early-price windows (~10s); set `MOONWELL_OEV_FLASH_LIQUIDATOR_ADDRESS`
- Optional paid liquidation intel — sign in to Agentic Wallet (`npx awal auth login`), fund USDC on Base, set `AGENTIC_WALLET_ENABLED=true` for Klymax/n0brains x402 APIs (~$0.003–0.005/query)
- CDP Smart Account executor — execution uses paymaster gas sponsorship

Tier 1/2 profitability features (oracle-triggered scan, watch-list pre-staging,
Flashblocks WS, dynamic profit thresholds, Odos/1inch/Kyber quotes, macro calendar,
95th-percentile gas bidding) are enabled by default via `.env.example` settings.

No liquidatable positions on Base is normal market conditions, not a configuration bug.
When `EXECUTE_ENABLED=true` and contracts are deployed, the agent auto-submits when
profit clears `MIN_PROFIT_USD` after simulation.

---

## Cursor Cloud specific instructions

This is the Coinbase Developer Platform (CDP) SDK monorepo: four independent client
SDKs — `typescript/`, `python/`, `go/`, `rust/` (plus a `java/` SDK). Standard
per-language build/lint/test commands live in `CLAUDE.md` and `CONTRIBUTING.md`; use
those. Notes below are the non-obvious caveats for running things in this environment.

The update script already installs project deps (`pnpm -C typescript install`,
`uv sync --extra dev --directory python`, `go -C go mod download`,
`cargo fetch`). System tools (`uv`, `golangci-lint` v2, Go toolchain, `libssl-dev`)
are provided by the VM snapshot and are on `PATH` in a normal login shell.

### Per-SDK run commands (from each language dir)
- TypeScript (`typescript/`): `pnpm build`, `pnpm lint`, `pnpm test` (Vitest + MSW mocks, no network).
- Python (`python/`): `make test` (pytest, excludes e2e), `make lint`, `make build`. Uses `uv`.
- Go (`go/`): `make test`, `make lint`. Or `go test ./...` directly (see tidy caveat below).
- Rust (`rust/`): `cargo build`, `cargo test --lib`, `cargo clippy --all-targets --all-features -- -D warnings`.

### Non-obvious gotchas
- CDP secrets leak into Rust unit tests: `CDP_WALLET_SECRET` / `CDP_API_KEY_ID` /
  `CDP_API_KEY_SECRET` may be set in the environment. The Rust `WalletAuth` builder
  falls back to these env vars, so `cargo test`/`make test`/`make verify` fail
  `auth::tests::test_wallet_auth_builder_with_required_fields_only` (asserts the
  fallback is `None`). Run Rust tests with them cleared:
  `env -u CDP_WALLET_SECRET -u CDP_API_KEY_ID -u CDP_API_KEY_SECRET cargo test --lib`.
- Go `make test` / `make lint` run `go mod tidy` (via the `clean` target) and can
  modify `go/go.mod` / `go/go.sum`. If the change is unintended, `git checkout -- go/go.mod go/go.sum`.
  To avoid the side effect entirely, use `go test ./...` directly.
- Go toolchain: `go.mod` requires Go 1.24.x while the base image ships an older Go;
  `GOTOOLCHAIN=auto` (default) auto-downloads the pinned toolchain on first use (needs network).
- Go lint requires golangci-lint v2 (`.golangci.yaml` is `version: "2"`).
- Rust needs OpenSSL dev headers (`libssl-dev` + `pkg-config`) to build; already installed in the snapshot.
- pnpm prints "Ignored build scripts" (esbuild, msw, etc.). This is expected; build and tests work regardless.
- E2E tests (TS `test:e2e`, Python `make e2e`, Rust `make test-e2e`) require live CDP
  credentials and are optional — skip them without valid keys.
- OpenAPI client generation (`make generate-all-clients`, `pnpm orval`, `make python-client`,
  `go make client`, `rust make generate`) is NOT needed for normal dev — generated code is
  committed and only changes when `openapi.yaml` updates; regeneration needs extra tools.
