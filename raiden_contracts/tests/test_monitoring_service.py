import pytest
from raiden_contracts.utils.config import (
    E_CHANNEL_CLOSED
)
from raiden_contracts.utils.events import check_channel_closed
from raiden_contracts.utils.sign import sign_reward_proof
from eth_utils import denoms


@pytest.fixture()
def create_reward_proof(token_network, get_private_key):
    def get(
            signer,
            channel_identifier,
            reward_amount,
            token_network_address,
            nonce=0,
            v=27
    ):
        private_key = get_private_key(signer)

        signature = sign_reward_proof(
            private_key,
            channel_identifier,
            reward_amount,
            token_network_address,
            int(token_network.call().chain_id()),
            nonce,
            v
        )
        return (
            channel_identifier,
            reward_amount,
            token_network_address,
            int(token_network.call().chain_id()),
            nonce,
            signature
        )
    return get


def test_msc_happy_path(
    token_network,
    monitoring_service_external,
    get_accounts,
    create_channel,
    channel_deposit,
    create_balance_proof,
    create_balance_proof_update_signature,
    create_reward_proof,
    event_handler,
    raiden_service_bundle,
    custom_token
):
    # setup: two parties + MS
    ev_handler = event_handler(token_network)
    (A, B, MS) = get_accounts(3)
    reward_amount = 10
    # mint some tokens
    custom_token.transact({'from': MS, 'value': 100 * denoms.finney}).mint()
    custom_token.transact({'from': A, 'value': 100 * denoms.finney}).mint()
    custom_token.transact({'from': B, 'value': 100 * denoms.finney}).mint()
    # register MS in the RaidenServiceBundle contract
    custom_token.transact({'from': MS}).approve(raiden_service_bundle.address, 20)
    raiden_service_bundle.transact({'from': MS}).deposit(20)
    ms_balance_after_deposit = monitoring_service_external.call().balances(MS)
    # raiden node deposit
    custom_token.transact({'from': B}).approve(monitoring_service_external.address, 20)
    monitoring_service_external.transact({'from': B}).deposit(B, 20)

    # 1) open a channel (c1, c2)
    channel_identifier = create_channel(A, B)[0]
    txn_hash = channel_deposit(A, 20, B)
    txn_hash = channel_deposit(B, 20, A)
    # 2) create balance proof
    balance_proof_A = create_balance_proof(channel_identifier, B, transferred_amount=10, nonce=1)
    balance_proof_B = create_balance_proof(channel_identifier, A, transferred_amount=20, nonce=2)
    non_closing_signature_B = create_balance_proof_update_signature(
        B,
        channel_identifier,
        *balance_proof_B
    )
    # 2a) create reward proof
    reward_proof = create_reward_proof(
        B,
        channel_identifier,
        reward_amount,
        token_network.address,
        nonce=balance_proof_B[1],
    )
    # 3) c1 closes channel
    txn_hash = token_network.transact({'from': A}).closeChannel(B, *balance_proof_A)
    ev_handler.add(txn_hash, E_CHANNEL_CLOSED, check_channel_closed(channel_identifier, A))
    ev_handler.check()
    # 4) MS calls `MSC::monitor()` using c1's BP and reward proof

    txn_hash = monitoring_service_external.transact({'from': MS}).monitor(
        A,
        B,
        balance_proof_B[0],  # balance_hash
        balance_proof_B[1],  # nonce
        balance_proof_B[2],  # additional_hash
        balance_proof_B[3],  # closing signature
        non_closing_signature_B,  # non-closing signature
        reward_proof[1],     # reward amount
        token_network.address,  # token network address
        reward_proof[5]      # reward proof signature
    )
    # 5) MSC calls TokenNetwork updateTransfer
    # 6) channel is settled
    token_network.web3.testing.mine(8)
    token_network.transact().settleChannel(
        A,                   # participant1
        20,                  # participant1_transferred_amount
        0,                   # participant1_locked_amount
        b'\x00' * 32,        # participant1_locksroot
        B,                   # participant2
        10,                  # participant2_transferred_amount
        0,                   # participant2_locked_amount
        b'\x00' * 32,        # participant2_locksroot
    )
    # 7) MS claims the reward
    monitoring_service_external.transact({'from': MS}).claimReward(
        token_network.address,
        A,
        B,
    )
    ms_balance_after_reward = monitoring_service_external.call().balances(MS)
    assert ms_balance_after_reward == (ms_balance_after_deposit + reward_amount)