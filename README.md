# Last-resort liquidation

This is a sample liquidation bot for [traderjoe lending](https://traderjoexyz.com/#/lending) for the [liquidation bot bounty](https://docs.google.com/document/d/1k8GusDAk-dLO8heNG-d4YJkmx8Z8vVMsIfS1R6QeMUE/edit). This was my first _proper_ project in blockchain and is probably full of bad practices and security issues. You **should not** use this code in a production project although if you are interested in learning a bit about how compound and uniswap protocols work you may find it interesting.

## Overview

The project is comprised of numerous parts. It has dependencies on the [traderjoe subgraph](https://thegraph.com/hosted-service/subgraph/traderjoe-xyz/lending?query=underwater%20accounts) and for testing uses the public avalanche node at _https://api.avax.network/ext/bc/C/rpc_. The code in this project used [brownie](https://eth-brownie.readthedocs.io) for developing the smart contracts and the python bot.

The flow of the bot is fairly straightforward with just some complications in the contract due to re-entrancy protection on the trader joe contracts. These protections disallow you from calling multiple functions such as flash loan and liquidate in the same transaction.

1. A web3.py filter listens for new avalanche blocks
2. On each new block, thegraph is queried for underwater accounts
3. Each accounts tokens are analysed and the liquidation parameters are sent in a transaction to our liquidation smart contract
4. The contract flashloans a token (not the repay token) from trader joe
5. Converts that token into the loan to repay token
6. Pays the underwater loan and redeems the seized capital
7. Convert the seized capital back into the token from flashloan and repay flash loan
8. Sends any left over profit back to the contract owner

## Running the bot

To run the bot you will need to install some python dependencies. This project uses [poetry](https://python-poetry.org/) to manage its python dependencies. Once you have poetry installed on your system run `poetry install` to install the required libraries. If you want to run the test-suite on a forked mainnet you also need to have hardhat installed. I installed this using [npm](https://www.npmjs.com/) `npm install hardhat`.

There are a number of useful [make](https://www.gnu.org/software/make/) targets for running the parts of this bot.

`make compile` compiles the smart contracts
`make test-hardhat` and optionally `make test-hardhat-debug` runs the test suite.
`make bot` run the actual bot

The test suit contains some tests I wrote for extra learning along the way, the test `test_liquidate_borrow` is particularly useful if you want to see the end to end process of liquidating a loan without using any deployed contracts.

## Where the magic happens

There are 3 main files you should look at to see how this project works.
The [contract](https://github.com/jummy123/last-resort-liquidator/blob/master/contracts/Liquidator.sol) contains the solidity code with all on chain functionality.
The [bot](https://github.com/jummy123/last-resort-liquidator/blob/master/scripts/liquidation.py) that contains the functionality for finding liquidation opportunities and calling our contract.
the [test](https://github.com/jummy123/last-resort-liquidator/blob/master/tests/test_liquidator.py) where you can see how the code actually works.

## Things I learnt.

* I had to inline a lot of variables, the solidity stack is shallow!
* Using hardhat with brownie you don't have access to `TransactionReceipt.return_value` it is always `None`.
* Events are usefull for debugging, view them with `TransactionReceipt.events`, this is still a PITA and I found myself longing for a [structlog](https://www.structlog.org/en/stable/) equivalent.
* You really **don't** have to know JS to write/test/deploy solidity smart contracts

## TODO

Things I really should have done but didn't. As I mentioned this project was a learning opportunity for me. Below is a big list of improvements that could be done to this project. There are also a lot of features in the original spec I didn't implement and **haven't** included them in this list.

* Support liquidating native loans (only ERC20 supported)
* Swapping profits back to AVAX
* Any traderjoe swap without a direct path will fail
* Accounts with not enough collateral in a single token to liquidate half an entire loan will be skipped
* learn [NatSpec](https://docs.soliditylang.org/) documentation format
* listen to a real node and maintain a local database of account health
* Dockerfile so people don't need to install the deps
