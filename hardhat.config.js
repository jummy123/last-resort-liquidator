
// autogenerated by brownie
// do not modify the existing settings
module.exports = {
    defaultNetwork: "hardhat",
    networks: {
        hardhat: {
            gasPrice: 225000000000,
            initialBaseFeePerGas: 0,
            forking: {
                url: 'https://api.avax.network/ext/bc/C/rpc',
                blockNumber: 7300000
            },
            // brownie expects calls and transactions to throw on revert
            throwOnTransactionFailures: true,
            throwOnCallFailures: true
       }
    }
}
