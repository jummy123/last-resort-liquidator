// SPDX-License-Identifier: UNLICENSED

pragma solidity ^0.8.0;

import "interfaces/joecore/IERC20.sol";
import "interfaces/joecore/IWAVAX.sol";
import "interfaces/joecore/IJoeRouter02.sol";

import "interfaces/JoeLending.sol";
import "interfaces/ERC3156FlashBorrowerInterface.sol";
import "interfaces/ERC3156FlashLenderInterface.sol";


contract Liquidator is ERC3156FlashBorrowerInterface {
    /*
    // Event emitted by our contract.
    */
    // Event emitted after performing a trader-joe swap.
    event Swapped(
        address fromTokenAddress,
        address toTokenAddress,
        uint fromTokenAmount,
        uint toTokensAmount
    );
    // Event emitted when we receive a flash loan.
    event Flashloaned(
        address tokenAddress,
        uint amount,
        uint fee
    );
    // Event emitted when we liquidate an loan.
    event Liquidated(
        address accountAddress,
        address tokenAddress,
        uint amount
    );
    // Event used for debugging.
    event Debug(
        string key,
        string stringValue,
        uint uintValue,
        address addressValue
    );

    address public owner;   // Where to send profits of liquidating

    // Addresses of contracts used by this contract.
    address public WAVAX = 0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7;

    // Interfaces for contracts we interact with.
    IJoeRouter02 public joeRouter;  // Interface for the trader joe router.
    JComptroller public joeComptroller;  // Interface for comptroller.

    constructor(address joeRouterAddress, address joeComptrollerAddress) {
        owner = msg.sender;
        joeRouter = IJoeRouter02(joeRouterAddress);
        joeComptroller = JComptroller(joeComptrollerAddress);
    }

    // Swap all the `tokenFrom` this contract holds to `tokenTo`.
    function swapERC20(address tokenFrom, address tokenTo) internal {
        require(IERC20(tokenFrom).balanceOf(address(this)) > 0, "Contract has no balance of tokenFrom");

        uint amountFrom = IERC20(tokenFrom).balanceOf(address(this));

        IERC20(tokenFrom).approve(address(joeRouter), amountFrom);
        address[] memory path = new address[](2);
        path[0] = tokenFrom;
        path[1] = tokenTo;

        joeRouter.swapExactTokensForTokens(
            amountFrom,
            1, // XXX: Should not have 1 Wei minimum out.
            path,
            address(this),
            block.timestamp + 1 minutes);
        require(IERC20(tokenTo).balanceOf(address(this)) > 0, "Didn't receive token");
        emit Swapped(
            tokenFrom,
            tokenTo,
            amountFrom,
            IERC20(tokenTo).balanceOf(address(this)));
    }

    // Swap all native avax for tokens.
    function swapFromNative(address tokenTo) internal {
        require(address(this).balance > 0, "Contract has no native balance");

        uint amountAvax = address(this).balance;
        address[] memory path = new address[](2);
        path[0] = WAVAX;
        path[1] = tokenTo;

        // XXX: Should not have 1 Wei minimum out.
        joeRouter.swapExactAVAXForTokens{value: amountAvax}(
            1,
            path,
            address(this),
            block.timestamp + 1 minutes);

        require(IERC20(tokenTo).balanceOf(address(this)) > 0, "Didn't receive token");
        emit Swapped(
            WAVAX,
            tokenTo,
            amountAvax,
            IERC20(tokenTo).balanceOf(address(this)));
    }

    function swapToNative(address tokenFrom) internal {
        require(IERC20(tokenFrom).balanceOf(address(this)) > 0, "Contract has no balance of tokenFrom");

        uint amountFrom = IERC20(tokenFrom).balanceOf(address(this));
        address[] memory path = new address[](2);
        path[0] = tokenFrom;
        path[1] = WAVAX;
        IERC20(tokenFrom).approve(address(joeRouter), amountFrom);
        joeRouter.swapExactTokensForAVAX(
             amountFrom,
             1,
             path,
             address(this),
             block.timestamp + 1 minutes);

        require(address(this).balance > 0, "has no native balance");
        emit Swapped(
            tokenFrom,
            WAVAX,
            IERC20(tokenFrom).balanceOf(address(this)),
            address(this).balance);
    }

    function liquidateLoan(
        address borrower,
        address jTokenLiquidateAddress,
        address jTokenLiquidateUnderlying,
        address jTokenCollateral,
        address jTokenCollateralUnderlying,
        address jTokenFlashLoan,
        address jTokenFlashLoanUnderlying
    ) external {
        // So the steps are as follows.
        // 1. Work out how much we need to repay.
        // 2. Work out how much we need to flash loan.
        // 3. Flash loan a token that is not the token to repay (non re-entrant).
        // 4. Swap the token for the loan to repay.
        // 5. Repay the loan.
        // 6. Withdraw the seized funds.
        // 7. Repay the flashloan
        // 9. Send the seized funds to the owner.

        // Due to the re-entrant protection on trader joe
        // we must flash loan a token that will not be
        // seized or liquidated.

        // 1. How much we need to repay.
        uint repayAmount = amountToRepay(
            borrower,
            jTokenLiquidateAddress,
            jTokenFlashLoanUnderlying);

        // 2. How much we need to flashloan.
        uint flashLoanAmount = getFlashLoanAmount(
            jTokenFlashLoanUnderlying,
            jTokenLiquidateUnderlying,
            repayAmount);

        // Data to pass through to the callback function.
        bytes memory data = abi.encode(
            borrower,
            repayAmount,
            jTokenLiquidateAddress,
            jTokenLiquidateUnderlying,
            jTokenCollateral,
            jTokenCollateralUnderlying,
            jTokenFlashLoanUnderlying
        );
        // 3. Perform the flash loan.
        ERC3156FlashLenderInterface(jTokenFlashLoan).flashLoan(
            this,
            jTokenFlashLoan,
            flashLoanAmount,
            data);
    }

    function onFlashLoan(
        address initiator,
        address token,
        uint256 amount,
        uint256 fee,
        bytes calldata data
    ) override external returns (bytes32) {
        emit Flashloaned(token, amount, fee);
        require(joeComptroller.isMarketListed(msg.sender), "untrusted message sender");

        (
            address borrower,
            uint repayAmount,
            address jTokenLiquidateAddress,
            address jTokenLiquidateUnderlying,
            address jTokenCollateral,
            address jTokenCollateralUnderlying,
            address jTokenFlashLoanUnderlying
        ) = abi.decode(data, (
            address, uint, address, address, address, address, address
        ));
        // 4. Swap the flash loan for the amount we will repay.
        swapERC20(jTokenFlashLoanUnderlying, jTokenLiquidateUnderlying);

        // 5. Liquidate the borrower.
        // Approve the jtoken to spend our repayment.
        liquidateBorrower(
            jTokenLiquidateUnderlying,
            jTokenLiquidateAddress,
            borrower,
            repayAmount,
            jTokenCollateral
        );
        emit Liquidated(borrower, jTokenLiquidateAddress, repayAmount);

        // 6. Withdraw the seized funds.
        JToken(jTokenCollateral).redeem(JToken(jTokenCollateral).balanceOf(address(this)));

        // 7. Repay the flash loan.
        swapERC20(jTokenCollateralUnderlying, jTokenFlashLoanUnderlying);

        IERC20(token).approve(msg.sender, amount + fee);

        // 8. Send seized funds to owner.
        IERC20(jTokenFlashLoanUnderlying).transfer(
            owner,
            IERC20(jTokenFlashLoanUnderlying).balanceOf(address(this)) - (amount + fee));

        // Done.
        return keccak256("ERC3156FlashBorrowerInterface.onFlashLoan");
    }

    // 1. How much we need to repay.
    function amountToRepay(
        address borrower,
        address jTokenLiquidateAddress,
        address jTokenFlashLoanUnderlying
    ) internal returns (uint) {
        uint borrowBalance = JToken(jTokenLiquidateAddress).borrowBalanceCurrent(borrower);
        uint closeFactor = joeComptroller.closeFactorMantissa();
        uint repayAmount = (borrowBalance * closeFactor) / (10 ** 18);
        return repayAmount;
    }

    // 2. How much we need to flashloan.
    function getFlashLoanAmount(
        address jTokenFlashLoanUnderlying,
        address jTokenLiquidateUnderlying,
        uint repayAmount
    ) internal returns (uint) {
            address[] memory path = new address[](2);
            path[0] = jTokenFlashLoanUnderlying;
            path[1] = jTokenLiquidateUnderlying;
            uint flashLoanAmount = joeRouter.getAmountsIn(repayAmount, path)[0];
            return flashLoanAmount;
    }

    // 5. Liquidate the borrower.
    function liquidateBorrower(
        address jTokenLiquidateUnderlying,
        address jTokenLiquidateAddress,
        address borrower,
        uint repayAmount,
        address jTokenCollateral
    ) internal {
        // Approve the jtoken to spend our repayment.
        IERC20(jTokenLiquidateUnderlying).approve(
            jTokenLiquidateAddress,
            IERC20(jTokenLiquidateUnderlying).balanceOf(address(this)));
        // The actual liquidation
        JToken(jTokenLiquidateAddress).liquidateBorrow(
            borrower,
            repayAmount,
            JToken(jTokenCollateral));
    }

    // Function to allow us to fund our contract with seed funds.
    // Not actually needed.
    function fund () public payable {}
    fallback() external payable {}
}


// A contract we just use for testing
// XXX: Do not deploy this contract.
contract TestLiquidator is Liquidator {
    constructor(
        address joeRouterAddress,
        address joeComptrollerAddress
    ) Liquidator(
        joeRouterAddress,
        joeComptrollerAddress
    ) {}

    function _swapERC20(address tokenFrom, address tokenTo) public {
        swapERC20(tokenFrom, tokenTo);
    }
    function _swapFromNative(address tokenTo) public {
        swapFromNative(tokenTo);
    }

    function _swapToNative(address tokenFrom) public {
        swapToNative(tokenFrom);
    }

}
