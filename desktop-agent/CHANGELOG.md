# Desktop Agent Changelog

## [0.4.0] - 2026-07-01

### Features

- **MorphoFlashArbitrage** — zero-fee Morpho flash loan + two-hop DEX arbitrage; profit sent to owner EOA
- Deployed on Base: `0x775e355B3b41882B7A30986cf763Cd06FE0a5779`
- `scripts/run_arbitrage.py` — scan + execute with simulation gate
- `scripts/arbitrage_monitor.py` — continuous monitor, auto-executes when `eth_call` passes
- Cross-venue routing: Kyber ↔ Uniswap V3 fee tiers

## [0.3.0] - 2026-07-01

### Features

- **Real execution mode** — `EXECUTE_ENABLED=true` with deployed Base mainnet contracts (no placeholders)
- **MorphoFlashLiquidator deployed** — `0x263B4C3670101F1dF25D683427e0f183C1332Ab4` owned by CDP Smart Account
- **Execution history** — persistent `data/executions.json` + dashboard history panel + `/api/executions`
- **`scripts/run_liquidation.py`** — one-shot scan-and-execute for immediate liquidation attempts
- **Aggressive rules engine** — executes when profit clears `MIN_PROFIT_USD` (risk flags are advisory)

### Fixes

- `ExecutionResult.to_dict()` serializes targets correctly for dashboard/API
- Test fixtures use real deployed contract addresses (not placeholder `0x1111…`)

## [0.2.0] - 2026-06-29

### Features

- **Morpho Blue execution** — `MorphoFlashLiquidator.sol` uses zero-fee Morpho flash loans for atomic liquidations with Uniswap V3 swap-back
- **Protocol-routed executor** — `FlashLiquidationExecutor` dispatches to Aave or Morpho contracts by `protocol_id`
- **Live swap quotes** — KyberSwap aggregator (default) and optional 1inch API for profit estimation
- **Morpho scanner** — GraphQL discovery with `borrowShares`, oracle, IRM, and LLTV; executable when `MORPHO_FLASH_LIQUIDATOR_ADDRESS` is set
- **Multi-protocol scanning** — Aave V3, Moonwell, Compound V3, and Morpho Blue on Base mainnet
- **Profit layer** — urgency scoring, watch list, oracle monitor, slippage-aware profit, simulation gate

### Tooling

- `python scripts/deploy_contract.py --morpho` deploys `MorphoFlashLiquidator`
- Unit tests in `tests/` for profit engine, swap quotes, and executor calldata encoding

### Notes

- Monorepo `typescript/` and `python/` SDK READMEs are unchanged — this application lives under `desktop-agent/` only.

## [0.1.0] - 2026-06-29

### Features

- Initial CDP Flash Liquidation Desktop Agent for Base mainnet
- `FlashLiquidator.sol` for Aave V3 flash-loan liquidations
- CDP Smart Account + paymaster integration
- Live dashboard on port 8787
