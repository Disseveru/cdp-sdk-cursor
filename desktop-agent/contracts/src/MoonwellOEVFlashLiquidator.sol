// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IPool} from "aave-v3-core/contracts/interfaces/IPool.sol";
import {IPoolAddressesProvider} from "aave-v3-core/contracts/interfaces/IPoolAddressesProvider.sol";
import {IFlashLoanSimpleReceiver} from "aave-v3-core/contracts/flashloan/interfaces/IFlashLoanSimpleReceiver.sol";

interface IChainlinkOEVWrapper {
    function updatePriceEarlyAndLiquidate(
        address borrower,
        uint256 repayAmount,
        address mTokenCollateral,
        address mTokenLoan
    ) external;
}

interface IMToken {
    function redeem(uint256 redeemTokens) external returns (uint256);
}

interface ISwapRouter02 {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);
}

/// @title MoonwellOEVFlashLiquidator
/// @notice Aave flash-loan + Moonwell OEV wrapper liquidation during the ~10s early-price window.
/// @dev Use the ChainlinkOEVWrapper that matches collateral (WETH on Base: 0xeb083d234ec636A10325ea42bCbbE09Aa56d1547).
contract MoonwellOEVFlashLiquidator is IFlashLoanSimpleReceiver, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    struct OEVLiquidationParams {
        address oevWrapper;
        address mTokenBorrowed;
        address mTokenCollateral;
        address debtUnderlying;
        address collateralUnderlying;
        address borrower;
        uint256 repayAmount;
        uint24 swapFee;
        uint256 minAmountOut;
    }

    IPool public immutable POOL;
    ISwapRouter02 public immutable SWAP_ROUTER;
    IPoolAddressesProvider public immutable ADDRESSES_PROVIDER;

    event MoonwellOEVLiquidationExecuted(
        address indexed borrower,
        address indexed oevWrapper,
        uint256 repayAmount,
        uint256 profit
    );

    error OnlyPool();
    error InvalidInitiator();
    error InsufficientProfit();
    error SwapFailed();

    constructor(address addressesProvider, address swapRouter, address owner_) Ownable(owner_) {
        ADDRESSES_PROVIDER = IPoolAddressesProvider(addressesProvider);
        POOL = IPool(ADDRESSES_PROVIDER.getPool());
        SWAP_ROUTER = ISwapRouter02(swapRouter);
    }

    function liquidateOEV(
        address debtAsset,
        uint256 flashAmount,
        OEVLiquidationParams calldata params
    ) external onlyOwner nonReentrant {
        POOL.flashLoanSimple(address(this), debtAsset, flashAmount, abi.encode(params), 0);
    }

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        if (msg.sender != address(POOL)) revert OnlyPool();
        if (initiator != address(this)) revert InvalidInitiator();

        OEVLiquidationParams memory lp = abi.decode(params, (OEVLiquidationParams));
        if (lp.debtUnderlying != asset) revert SwapFailed();

        IERC20 debtToken = IERC20(asset);
        debtToken.forceApprove(lp.oevWrapper, lp.repayAmount);

        IChainlinkOEVWrapper(lp.oevWrapper).updatePriceEarlyAndLiquidate(
            lp.borrower,
            lp.repayAmount,
            lp.mTokenCollateral,
            lp.mTokenBorrowed
        );

        uint256 mTokenBalance = IERC20(lp.mTokenCollateral).balanceOf(address(this));
        if (mTokenBalance > 0) {
            uint256 underlyingOut = IMToken(lp.mTokenCollateral).redeem(mTokenBalance);
            if (lp.collateralUnderlying != lp.debtUnderlying && underlyingOut > 0) {
                IERC20 collateral = IERC20(lp.collateralUnderlying);
                collateral.forceApprove(address(SWAP_ROUTER), underlyingOut);
                uint256 swapped = SWAP_ROUTER.exactInputSingle(
                    ISwapRouter02.ExactInputSingleParams({
                        tokenIn: lp.collateralUnderlying,
                        tokenOut: lp.debtUnderlying,
                        fee: lp.swapFee,
                        recipient: address(this),
                        amountIn: underlyingOut,
                        amountOutMinimum: lp.minAmountOut,
                        sqrtPriceLimitX96: 0
                    })
                );
                if (swapped < lp.minAmountOut) revert SwapFailed();
            }
        }

        uint256 debtBalance = debtToken.balanceOf(address(this));
        uint256 repayment = amount + premium;
        if (debtBalance < repayment) revert InsufficientProfit();

        debtToken.forceApprove(address(POOL), repayment);
        uint256 profit = debtBalance - repayment;
        if (profit > 0) {
            debtToken.safeTransfer(owner(), profit);
        }

        emit MoonwellOEVLiquidationExecuted(lp.borrower, lp.oevWrapper, lp.repayAmount, profit);
        return true;
    }

    function rescueToken(address token, uint256 amount) external onlyOwner nonReentrant {
        IERC20(token).safeTransfer(owner(), amount);
    }

    receive() external payable {}
}
