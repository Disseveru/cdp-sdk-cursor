// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

interface IMorpho {
    function flashLoan(address token, uint256 assets, bytes calldata data) external;
}

interface IMorphoFlashLoanCallback {
    function onMorphoFlashLoan(uint256 assets, bytes calldata data) external;
}

/// @title MorphoFlashArbitrage
/// @notice Zero-fee Morpho flash loan + two-hop DEX arbitrage; profit sent to EOA.
contract MorphoFlashArbitrage is IMorphoFlashLoanCallback, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    struct SwapLeg {
        address target;
        bytes data;
    }

    struct ArbParams {
        address loanToken;
        address intermediateToken;
        address profitRecipient;
        SwapLeg leg1;
        SwapLeg leg2;
        uint256 minProfit;
    }

    IMorpho public immutable MORPHO;

    event ArbitrageExecuted(
        address indexed loanToken,
        address indexed profitRecipient,
        uint256 borrowed,
        uint256 profit
    );

    error OnlyMorpho();
    error InsufficientProfit();
    error SwapFailed();

    constructor(address morpho, address owner_) Ownable(owner_) {
        MORPHO = IMorpho(morpho);
    }

    /// @notice Flash-borrow `amount`, swap loanToken→intermediate→loanToken, repay, send profit to EOA.
    function executeArbitrage(address loanToken, uint256 amount, ArbParams calldata params) external onlyOwner nonReentrant {
        if (params.loanToken != loanToken) revert SwapFailed();
        MORPHO.flashLoan(loanToken, amount, abi.encode(params));
    }

    function onMorphoFlashLoan(uint256 assets, bytes calldata data) external {
        if (msg.sender != address(MORPHO)) revert OnlyMorpho();

        ArbParams memory p = abi.decode(data, (ArbParams));
        IERC20 loan = IERC20(p.loanToken);
        IERC20 mid = IERC20(p.intermediateToken);

        loan.forceApprove(p.leg1.target, assets);
        (bool ok1, ) = p.leg1.target.call(p.leg1.data);
        if (!ok1) revert SwapFailed();

        uint256 midBalance = mid.balanceOf(address(this));
        if (midBalance == 0) revert SwapFailed();
        mid.forceApprove(p.leg2.target, midBalance);
        (bool ok2, ) = p.leg2.target.call(p.leg2.data);
        if (!ok2) revert SwapFailed();

        uint256 finalBalance = loan.balanceOf(address(this));
        if (finalBalance < assets) revert InsufficientProfit();

        loan.forceApprove(address(MORPHO), assets);

        uint256 profit = finalBalance - assets;
        if (profit < p.minProfit) revert InsufficientProfit();

        if (profit > 0) {
            loan.safeTransfer(p.profitRecipient, profit);
        }

        emit ArbitrageExecuted(p.loanToken, p.profitRecipient, assets, profit);
    }

    function rescueToken(address token, uint256 amount) external onlyOwner nonReentrant {
        IERC20(token).safeTransfer(owner(), amount);
    }

    receive() external payable {}
}
