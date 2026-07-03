"""Uniswap V3 swap calldata helpers for cross-venue arbitrage."""

from __future__ import annotations

from web3 import Web3

from agent.protocols.aave_v3_base import UNISWAP_V3_SWAP_ROUTER_BASE

SWAP_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "recipient", "type": "address"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "amountOutMinimum", "type": "uint256"},
                    {"name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    }
]

FEE_TIERS = (100, 500, 3000)


def build_uni_exact_input_single(
    w3: Web3,
    *,
    token_in: str,
    token_out: str,
    fee: int,
    recipient: str,
    amount_in: int,
    amount_out_minimum: int = 0,
) -> tuple[str, str]:
    """Return (router_address, calldata) for Uniswap V3 exactInputSingle."""
    router = w3.eth.contract(
        address=Web3.to_checksum_address(UNISWAP_V3_SWAP_ROUTER_BASE),
        abi=SWAP_ROUTER_ABI,
    )
    params = (
        Web3.to_checksum_address(token_in),
        Web3.to_checksum_address(token_out),
        fee,
        Web3.to_checksum_address(recipient),
        amount_in,
        amount_out_minimum,
        0,
    )
    data = router.functions.exactInputSingle(params)._encode_transaction_data()
    return UNISWAP_V3_SWAP_ROUTER_BASE, data
