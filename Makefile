compile:
	poetry run brownie compile

test:
	poetry run brownie test --network hardhat

test-hardhat: clean
	poetry run brownie test --network hardhat

test-hardhat-debug: clean
	poetry run brownie test --network hardhat -vv -s --interactive

console:
	poetry run brownie console --network hardhat

clean:
	rm -rf build/*

bot:
	poetry run brownie run scripts/liquidation.py --network hardhat
