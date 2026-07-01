"""Execute flash arbitrage via MorphoFlashArbitrage + CDP Smart Account."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from eth_abi import encode
from web3 import Web3

from agent.arbitrage.models import ArbitrageOpportunity
from agent.cdp_wallet import CdpWalletManager
from agent.protocols.morpho_base import MORPHO_BLUE_BASE
from config.settings import AgentSettings

logger = logging.getLogger(__name__)

MORPHO_FLASH_ARB_ABI = [
    {
        "inputs": [
            {"name": "loanToken", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {
                "components": [
                    {"name": "loanToken", "type": "address"},
                    {"name": "intermediateToken", "type": "address"},
                    {"name": "profitRecipient", "type": "address"},
                    {
                        "components": [
                            {"name": "target", "type": "address"},
                            {"name": "data", "type": "bytes"},
                        ],
                        "name": "leg1",
                        "type": "tuple",
                    },
                    {
                        "components": [
                            {"name": "target", "type": "address"},
                            {"name": "data", "type": "bytes"},
                        ],
                        "name": "leg2",
                        "type": "tuple",
                    },
                    {"name": "minProfit", "type": "uint256"},
                ],
                "name": "params",
                "type": "tuple",
            },
        ],
        "name": "executeArbitrage",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "loanToken", "type": "address"},
            {"indexed": True, "name": "profitRecipient", "type": "address"},
            {"indexed": False, "name": "borrowed", "type": "uint256"},
            {"indexed": False, "name": "profit", "type": "uint256"},
        ],
        "name": "ArbitrageExecuted",
        "type": "event",
    },
]


@dataclass
class ArbitrageResult:
    opportunity: ArbitrageOpportunity
    user_op_hash: str | None
    status: str
    message: str
    tx_hash: str | None = None
    profit_recipient: str = ""


class FlashArbitrageExecutor:
    def __init__(self, settings: AgentSettings, wallet: CdpWalletManager, contract_address: str) -> None:
        self.settings = settings
        self.wallet = wallet
        self.contract = Web3.to_checksum_address(contract_address)

    def encode_execute(self, opp: ArbitrageOpportunity, profit_recipient: str, min_profit: int) -> str:
        contract = self.wallet.bundle.w3.eth.contract(address=self.contract, abi=MORPHO_FLASH_ARB_ABI)
        params = (
            Web3.to_checksum_address(opp.loan_token),
            Web3.to_checksum_address(opp.intermediate_token),
            Web3.to_checksum_address(profit_recipient),
            (Web3.to_checksum_address(opp.leg1_router), bytes.fromhex(opp.leg1_calldata[2:])),
            (Web3.to_checksum_address(opp.leg2_router), bytes.fromhex(opp.leg2_calldata[2:])),
            min_profit,
        )
        fn = contract.functions.executeArbitrage(
            Web3.to_checksum_address(opp.loan_token),
            opp.amount_in,
            params,
        )
        return fn._encode_transaction_data()

    async def execute(
        self,
        opp: ArbitrageOpportunity,
        profit_recipient: str,
        *,
        min_profit: int | None = None,
        simulate: bool = True,
    ) -> ArbitrageResult:
        if min_profit is None:
            min_profit = max(1, opp.expected_profit_raw // 2)

        try:
            data = self.encode_execute(opp, profit_recipient, min_profit)
            smart_account = self.wallet.bundle.smart_account.address

            if simulate:
                try:
                    self.wallet.bundle.w3.eth.call(
                        {"from": smart_account, "to": self.contract, "data": data}
                    )
                except Exception as sim_exc:
                    return ArbitrageResult(
                        opportunity=opp,
                        user_op_hash=None,
                        status="simulation_failed",
                        message=f"Simulation reverted: {sim_exc}",
                        profit_recipient=profit_recipient,
                    )

            user_op = await self.wallet.send_contract_call(to=self.contract, data=data)
            receipt = await self.wallet.bundle.network_account.wait_for_user_operation(
                user_op_hash=user_op.user_op_hash,
                timeout_seconds=120,
            )
            tx_hash = getattr(receipt, "transaction_hash", None)
            return ArbitrageResult(
                opportunity=opp,
                user_op_hash=user_op.user_op_hash,
                status=receipt.status,
                message=f"Arbitrage executed; est. profit ${opp.expected_profit_usd:.4f} to {profit_recipient}",
                tx_hash=tx_hash,
                profit_recipient=profit_recipient,
            )
        except Exception as exc:
            logger.exception("Arbitrage execution failed")
            return ArbitrageResult(
                opportunity=opp,
                user_op_hash=None,
                status="failed",
                message=str(exc),
                profit_recipient=profit_recipient,
            )

    @staticmethod
    def encode_constructor_args(owner: str) -> bytes:
        return encode(
            ["address", "address"],
            [Web3.to_checksum_address(MORPHO_BLUE_BASE), Web3.to_checksum_address(owner)],
        )
