from unittest.mock import ANY

from brownie import (
    Liquidator,
    TestLiquidator,
    Wei,
    accounts,
    chain,
    convert,
    interface,
    reverts,
)
from brownie.exceptions import VirtualMachineError
import pytest

from scripts.liquidation import liquidation_parameters, LiquidationParameters


WAVAX = "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7"
LINK = "0x5947bb275c521040051d82396192181b413227a3"
USDT = "0xc7198437980c041c805a1edcba50c1ce5db95118"
USDC = "0xa7d7079b0fead91f3e65f86e8915cb59c1a4c664"

JOE_ROUTER_ADDRESS = "0x60aE616a2155Ee3d9A68541Ba4544862310933d4"
JOE_COMPTROLLER_ADDRESS = "0xdc13687554205E5b89Ac783db14bb5bba4A1eDaC"

JAVAX_ADDRESS = "0xC22F01ddc8010Ee05574028528614634684EC29e"
JLINK_ADDRESS = "0x585E7bC75089eD111b656faA7aeb1104F5b96c15"
JUSDT_ADDRESS = "0x8b650e26404AC6837539ca96812f0123601E4448"
JUSDC_ADDRESS = "0xEd6AaF91a2B084bd594DBd1245be3691F9f637aC"

ORACLE_ADDRESS = "0xe34309613B061545d42c4160ec4d64240b114482"


@pytest.fixture
def liquidator():
    contract = Liquidator.deploy(
        JOE_ROUTER_ADDRESS,
        JOE_COMPTROLLER_ADDRESS,
        {'from': accounts[0]})
    return contract


@pytest.fixture
def test_liquidator():
    contract = TestLiquidator.deploy(
        JOE_ROUTER_ADDRESS,
        JOE_COMPTROLLER_ADDRESS,
        {'from': accounts[0]})
    return contract


@pytest.fixture
def comptroller():
    return interface.JComptroller(JOE_COMPTROLLER_ADDRESS)


@pytest.fixture
def javax():
    return interface.JTokenNative(JAVAX_ADDRESS)


@pytest.fixture
def jlink():
    return interface.JToken(JLINK_ADDRESS)


@pytest.fixture
def jusdt():
    return interface.JToken(JUSDT_ADDRESS)


@pytest.fixture
def oracle():
    return interface.PriceOracle(ORACLE_ADDRESS)


@pytest.fixture
def joe_router():
    return interface.IJoeRouter02(JOE_ROUTER_ADDRESS)


def test_contract_owner(liquidator):
    assert liquidator.owner() == accounts[0].address


def test_swap_erc20_no_balance(test_liquidator):
    with reverts():
        test_liquidator._swapERC20(LINK, USDT)


def test_swap_from_native_no_balance(test_liquidator):
    with reverts():
        test_liquidator._swapFromNative(LINK);


def test_swap_from_native(test_liquidator):
    test_liquidator.fund({'from': accounts[0], 'amount': Wei("1 ether")})
    assert test_liquidator.balance() == Wei("1 ether")
    assert interface.IERC20(LINK).balanceOf(test_liquidator.address) == 0
    test_liquidator._swapFromNative(LINK)
    # XXX: Get oracle rate and assert actual balance.
    assert interface.IERC20(LINK).balanceOf(test_liquidator.address) > 0


def test_swap_erc20(test_liquidator):
    test_liquidator.fund({'from': accounts[0], 'amount': Wei("1 ether")})
    test_liquidator._swapFromNative(LINK)

    test_liquidator._swapERC20(LINK, USDT)

    assert interface.IERC20(LINK).balanceOf(test_liquidator) == 0
    assert interface.IERC20(USDT).balanceOf(test_liquidator) > 0


def test_swap_to_native(test_liquidator):
    test_liquidator.fund({'from': accounts[0], 'amount': Wei("1 ether")})
    test_liquidator._swapFromNative(LINK)

    assert test_liquidator.balance() == 0
    test_liquidator._swapToNative(LINK)
    assert test_liquidator.balance() >= 0
    assert interface.IERC20(LINK).balanceOf(test_liquidator.address) == 0


def test_liquidate_borrow(joe_router, comptroller, jusdt, jlink, oracle):
    """
    This test what my learning experience for liquidating underwater accounts.

    It is quite long and appears complicated but is quite simple.

    1. account[1] supplies some LINK and borrows max USDT
    2. Fast forward the block to accrue interest and put our account underwater
    3. Repay the loan from account[0] and seize LINK collateral

    Any extra code is just swapping AVAX for the tokens needed to perform
    the interactions in the above steps.
    """

    # First we need to get some link to supply, performs a swqap
    # on trader joe.
    joe_router.swapExactAVAXForTokens(
        Wei("1 ether"),
        [WAVAX, LINK],
        accounts[1].address,
        chain.time() + 60,
        {'from': accounts[1], 'value': Wei("1 ether")},
    )
    link_balance = interface.IERC20(LINK).balanceOf(accounts[1].address)
    assert link_balance > 0

    # Now we want to supply our ERC20 link into the JLink contract
    # and enter the link and usdt markets.
    assert interface.IERC20(LINK).approve(
        JLINK_ADDRESS,
        link_balance,
        {'from': accounts[1]})
    jlink.mint(link_balance, {'from': accounts[1]})
    comptroller.enterMarkets(
        [JLINK_ADDRESS, JUSDT_ADDRESS],
        {'from': accounts[1]})

    assert jlink.balanceOfUnderlying.call(
        accounts[1].address,
        {'from': accounts[1]}) >= link_balance   # GTE as we may have accrued interest.
    assert comptroller.checkMembership(accounts[1].address, JLINK_ADDRESS) == True

    # Now we want to borrow USDT with our link as collateral

    borrow_amount = _get_max_borrow_amount(
        comptroller,
        oracle,
        jusdt,
        USDT,
        accounts[1])
    #borrow_amount *= .9999

    jusdt.borrow(borrow_amount, {'from': accounts[1]})
    usdt_balance = interface.IERC20(USDT).balanceOf(accounts[1].address)
    assert usdt_balance > 0

    jusdt.borrowBalanceCurrent(accounts[1].address, {'from': accounts[1]})

    chain.mine(blocks=1, timestamp=chain.time() + 60*60*24*352)

    jusdt.borrowBalanceCurrent(accounts[1].address, {'from': accounts[1]})

    err, liquidity, shortfall = comptroller.getAccountLiquidity(accounts[1].address)
    assert shortfall > 0, "account not in shortfall"

    # OK here we have a shortfall of liquidity, lets see if we can liquidate
    # the position.
    borrow_balance = jusdt.borrowBalanceCurrent.call(accounts[1].address, {'from': accounts[0]})
    close_factor = comptroller.closeFactorMantissa.call({'from': accounts[0]}) / 10 ** 18
    repay_amount = borrow_balance * close_factor / 10 ** interface.IERC20(USDT).decimals()

    # Our liquidator needs this many tokens, we swap on traderjoe.
    joe_router.swapExactAVAXForTokens(
        1,
        [WAVAX, USDT],
        accounts[0].address,
        chain.time() + 60,
        {'from': accounts[0], 'value': Wei("2 ether")},
    )
    usdt_balance = interface.IERC20(USDT).balanceOf(accounts[1].address)
    assert usdt_balance / 10** interface.IERC20(USDT).decimals() > repay_amount, "Dont have correct repay amount"

    # Perform the liquidation.
    interface.IERC20(USDT).approve(JUSDT_ADDRESS, repay_amount, {'from': accounts[0]})
    # Check we have no balance before liquidate
    assert jlink.balanceOfUnderlying.call(accounts[0].address, {'from': accounts[0]}) ==  0

    # Perform the liquidation.
    jusdt.liquidateBorrow(accounts[1].address, repay_amount, jlink, {'from': accounts[0]})

    # Check to make sure account 1 has some seized link tokens.
    underlying  = jlink.balanceOfUnderlying.call(accounts[0], {'from': accounts[0]})
    assert underlying > 0, "We haven't seized any tokens."
    balance = jlink.balanceOf(accounts[0].address)

    # Finally lets redeem our seized tokens.
    jlink.redeem(balance, {'from': accounts[0]})
    assert interface.IERC20(LINK).balanceOf(accounts[0].address) > 0
    assert interface.IERC20(LINK).balanceOf(accounts[0].address) > underlying  # Small interest build up


def _get_max_borrow_amount(comptroller, oracle, borrow_token, borrow_token_address, account):
    """
    Get the maximum this account can borrow.
    """
    # Get account liquidity.
    err, liquidity, shortfall = comptroller.getAccountLiquidity(account.address)
    assert err == 0, "error getting account liquidity"

    # Get price of borrow token.
    price = oracle.getUnderlyingPrice(borrow_token)
    return liquidity * (10 ** 18) / price


def test_liquidator_contract(joe_router, comptroller, jusdt, jlink, oracle):
    """
    Testcase for our liquidator contract.

    This test is largely similar to `test_liquidate_borrow` except instead
    of liquidating the loan with a EOA (externally owned account) we deploy
    our liquidator contract which used flash loans to liquidate the underwater
    position. Note: Normally I would make a fixture for the underwater account
    but in this case I am just copying and pasting the code as this will probably
    be one of the last tests for this bounty challenge.
    """

    # First we need to get some link to supply, performs a swqap
    # on trader joe.
    joe_router.swapExactAVAXForTokens(
        1,
        [WAVAX, USDT],
        accounts[2].address,
        chain.time() + 60,
        {'from': accounts[2], 'value': Wei("1 ether")},
    )
    usdt_balance = interface.IERC20(USDT).balanceOf(accounts[2].address)
    assert usdt_balance > 0

    # Now we want to supply our ERC20 link into the JLink contract
    # and enter the link and usdt markets.
    assert interface.IERC20(USDT).approve(
        JUSDT_ADDRESS,
        usdt_balance,
        {'from': accounts[2]})
    jusdt.mint(usdt_balance, {'from': accounts[2]})
    comptroller.enterMarkets(
        [JLINK_ADDRESS, JUSDT_ADDRESS],
        {'from': accounts[2]})

    assert jusdt.balanceOfUnderlying.call(
        accounts[2].address,
        {'from': accounts[2]}) >= usdt_balance   # GTE as we may have accrued interest.
    assert comptroller.checkMembership(accounts[2].address, JUSDT_ADDRESS) == True

    # Now we want to borrow USDT with our link as collateral

    borrow_amount = _get_max_borrow_amount(
        comptroller,
        oracle,
        jlink,
        LINK,
        accounts[2])
    borrow_amount *= .999

    jlink.borrow(borrow_amount, {'from': accounts[2]})
    link_balance = interface.IERC20(LINK).balanceOf(accounts[2].address)
    assert link_balance > 0

    jlink.borrowBalanceCurrent(accounts[2].address, {'from': accounts[2]})

    chain.mine(blocks=1, timestamp=chain.time() + 60*60*24*352)

    jlink.borrowBalanceCurrent(accounts[2].address, {'from': accounts[2]})

    err, liquidity, shortfall = comptroller.getAccountLiquidity(accounts[2].address)
    assert shortfall > 0, "account not in shortfall"

    # Now we deploy our contract and liquidate.
    contract = Liquidator.deploy(
        JOE_ROUTER_ADDRESS,
        JOE_COMPTROLLER_ADDRESS,
        {'from': accounts[0]})

    # Let the contract liquidate.
    tx = contract.liquidateLoan(
        accounts[2].address,
        JLINK_ADDRESS,
        LINK,
        JUSDT_ADDRESS,
        USDT,
        JUSDC_ADDRESS,
        USDC,
        {'from': accounts[0]}
    );
    # Assert we have some profit.
    assert interface.IERC20(USDC).balanceOf(accounts[0].address) > 0


@pytest.mark.parametrize(
    "accounts,expected", [
        ([], []),
        ([{'health': '0.040151181763124301',
  'id': '0xd9233c98d84e50f07b122ee0de0a6a50f49127e0',
  'tokens': [{'borrowBalanceUnderlying': '0',
              'enteredMarket': True,
              'id': '0x8b650e26404ac6837539ca96812f0123601e4448-0xd9233c98d84e50f07b122ee0de0a6a50f49127e0',
              'supplyBalanceUnderlying': '0',
              'symbol': 'jUSDT'},
             {'borrowBalanceUnderlying': '0',
              'enteredMarket': True,
              'id': '0x929f5cab61dfec79a5431a7734a68d714c4633fa-0xd9233c98d84e50f07b122ee0de0a6a50f49127e0',
              'supplyBalanceUnderlying': '0',
              'symbol': 'jWETH'},
             {'borrowBalanceUnderlying': '0',
              'enteredMarket': True,
              'id': '0xc988c170d0e38197dc634a45bf00169c7aa7ca19-0xd9233c98d84e50f07b122ee0de0a6a50f49127e0',
              'supplyBalanceUnderlying': '0',
              'symbol': 'jDAI'},
             {'borrowBalanceUnderlying': '8479.700995064730131663373878343748',
              'enteredMarket': True,
              'id': '0xed6aaf91a2b084bd594dbd1245be3691f9f637ac-0xd9233c98d84e50f07b122ee0de0a6a50f49127e0',
              'supplyBalanceUnderlying': '10940.151993880858163609064043',
              'symbol': 'jUSDC'}],
  'totalBorrowValueInUSD': '8414.278374',
  'totalCollateralValueInUSD': '8752.1215944'}
        ], [
            LiquidationParameters(
                convert.to_address('0xd9233c98d84e50f07b122ee0de0a6a50f49127e0'),
                convert.to_address('0xed6aaf91a2b084bd594dbd1245be3691f9f637ac'),
                convert.to_address('0xa7d7079b0fead91f3e65f86e8915cb59c1a4c664'),
                convert.to_address('0xed6aaf91a2b084bd594dbd1245be3691f9f637ac'),
                convert.to_address('0xa7d7079b0fead91f3e65f86e8915cb59c1a4c664'),
                ANY,
                ANY,
            )
	])
])
def test_liquidation_params(accounts, expected):
    """
    Test for our liquidation algo.
    """
    assert list(liquidation_parameters(accounts)) == expected
