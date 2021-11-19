"""
A script for liquidating underwater flash loans using
the Liquidator flash loaning contract.
"""

import os
from collections import namedtuple
import time
import random
import sys

from brownie import web3, convert, accounts
from brownie import Liquidator
import httpx


LIQUIDATOR_ADDRESS = "0x0"  # Add your deployed liquidator address here.


WAVAX = "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7"
WETH = "0x49d5c2bdffac6ce2bfdb6640f4f80f226bc10bab"
WBTC = "0x50b7545627a5162f82a992c33b87adc75187b218"
USDC = "0xA7D7079b0FEaD91F3e65f86E8915Cb59c1a4C664"
USDT = "0xc7198437980c041c805a1edcba50c1ce5db95118"
DAI = "0xd586e7f844cea2f87f50152665bcbc2c279d8d70"
LINK = "0x5947bb275c521040051d82396192181b413227a3"
MIM = "0x130966628846bfd36ff31a822705796e8cb8c18d"
XJOE = "0x57319d41f71e81f3c65f2a47ca4e001ebafd4f33"

JAVAX = "0xC22F01ddc8010Ee05574028528614634684EC29e"
JWETH = "0x929f5caB61DFEc79a5431a7734a68D714C4633fa"
JWBTC = "0x3fE38b7b610C0ACD10296fEf69d9b18eB7a9eB1F"
JUSDC = "0xEd6AaF91a2B084bd594DBd1245be3691F9f637aC"
JUSDT = "0x8b650e26404AC6837539ca96812f0123601E4448"
JDAI = "0xc988c170d0E38197DC634A45bF00169C7Aa7CA19"
JLINK = "0x585E7bC75089eD111b656faA7aeb1104F5b96c15"
JMIM = "0xcE095A9657A02025081E0607c8D8b081c76A75ea"
JXJOE = "0xC146783a59807154F92084f9243eb139D58Da696"

# Provides a lookup for underlying token addresses.
JOE_TO_ERC20 = {
    JAVAX: WAVAX,
    JWETH: WETH,
    JWBTC: WBTC,
    JUSDC: USDC,
    JUSDT: USDT,
    JDAI: DAI,
    JLINK: LINK,
    JMIM: MIM,
    JXJOE: XJOE,
}


TRADER_JOB_LENDING_SUBGRAPH_URL = (
    "https://api.thegraph.com/subgraphs/name/traderjoe-xyz/lending")

UNDERWATER_ACCOUNTS_QUERY = """\
{{
  accounts(where: {{
        health_gt: {health_gt},
        health_lt: {health_lt},
        totalBorrowValueInUSD_gt: {borrow_value_usd_gt} }}
    ) {{
    id
    health
    totalBorrowValueInUSD
    totalCollateralValueInUSD
    tokens {{
        id
        symbol
        supplyBalanceUnderlying
        borrowBalanceUnderlying
        enteredMarket
    }}
  }}
}}
"""

MARKET_QUERY = """\
{
  markets {
    id
    symbol
    underlyingPriceUSD
  }
}
"""

LiquidationParameters = namedtuple(
    'LiquidationParameters', [
        'borrower',
        'liquidation_contract',
        'liquidation_underlying',
        'collateral_contract',
        'collateral_underlying',
        'flashloan_contract',
        'flashloan_underlying',
    ])


def query_underwater_accounts(
        health_lt=1.0,
        health_gt=0,
        borrow_value_usd_gt=0
    ):
    """
    Query thegraph API to find loans with given health values and underlying
    borrowed collaterall.
    """
    query = UNDERWATER_ACCOUNTS_QUERY.format(
        health_lt=health_lt,
        health_gt=health_gt,
        borrow_value_usd_gt=borrow_value_usd_gt,
    )
    response = httpx.post(
        TRADER_JOB_LENDING_SUBGRAPH_URL,
        json={"query": query},
    )
    response.raise_for_status()
    return response.json()['data']['accounts']


def query_underling_price_usd():
    """
    Get the current USD price for all banker joe markets
    """
    response = httpx.post(
        TRADER_JOB_LENDING_SUBGRAPH_URL,
        json={"query": MARKET_QUERY},
    )
    response.raise_for_status()
    return {
        oracle['symbol']: oracle['underlyingPriceUSD']
        for oracle in
        response.json()['data']['markets']
    }


def liquidation_parameters(accounts):
    """
    Iterator over a series of underwater accounts.

    Yields `LiquidationParameters` named tuples containing
    the parameters for our liquidation contract.
    """

    # supplyBalanceUnderlying > 0
    # enterMarket == true (otherwise it’s not posted as collateral)
    # Must have enough supplyBalanceUnderlying to seize 50% of borrow value
    for account in accounts:

        # We use a naieve algorithm here. We first find the max
        # seizable collateral and then find an account that has
        # borrowed more than double of this. Our contract doesn't
        # do this logic for us so we either get all or nothing here.
        # If there is no single collateral we can seize to repay
        # a full amount the account is skipped (lucky borrower).
        max_seizable = max([
            token
            for token in account['tokens']
            if token['enteredMarket'] is True
            and float(token['supplyBalanceUnderlying']) > 0],
            key=lambda t: float(t['supplyBalanceUnderlying']))
        max_repayable = max([
            token
            for token in account['tokens']
            if token['enteredMarket'] is True
            and (
                (float(token['borrowBalanceUnderlying']) / 2)
                < float(max_seizable['supplyBalanceUnderlying']))],
            key=lambda t: float(t['borrowBalanceUnderlying']))
        if not max_seizable and max_repayable:
            continue

        # Addresses aren't checksumed in graph response.
        repayable = convert.to_address(max_repayable['id'].split('-')[0])
        seizable = convert.to_address(max_seizable['id'].split('-')[0])

        # For flash loaning we just choose one token not represented here.
        flash_loanable = set(JOE_TO_ERC20)
        flash_loanable.remove(repayable)
        try:
            flash_loanable.remove(seizable)
        except KeyError:
            # collateral and borrowed same token.
            pass

        # Choose a random token for our flash loan.
        flash_loan = random.choice(list(flash_loanable))

        yield LiquidationParameters(
            convert.to_address(account['id']),
            repayable,
            JOE_TO_ERC20[repayable],
            seizable,
            JOE_TO_ERC20[seizable],
            flash_loan,
            JOE_TO_ERC20[flash_loan],
        )


def main():
    """
    Our main function that.
    1. Listens for new blocks.
    2. Queries the graph.
    3. Sends liquidation params to our flash loan contract.
    """
    # Our pre-deployed liquidator contract.
    liquidator = Liquidator.at(LIQUIDATOR_ADDRESS)

    # Filter for new blocks.
    new_blocks = web3.eth.filter('latest')

    # Continuous loop waiting for new blocks.
    while True:
        if new_blocks.get_new_entries():
            accounts = query_underwater_accounts()
            for liquidation_params in liquidation_parameters(accounts):
                try:
                    Liquidator.liquidateLoan(*liquidation_params, {'from': accounts[0]})
                except brownie.exceptions.VirtualMachineError as exc:
                    print(f"Exception liquidation loan {exc}", file=sys.stderr)
                else:
                    # Call to discord etc.
                    print(f"Liquidated loan {liquidation_params}")


if __name__ == "__main__":
    main()
