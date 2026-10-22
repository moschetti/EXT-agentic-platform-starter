from assist.agent import INSTRUCTIONS
from assist.guardrails import classify, looks_blocked, passed


def test_block_message_is_detected():
    assert looks_blocked("This request was blocked by policy: no investment advice.")
    assert not looks_blocked("**Summary:** The customer wants to report water damage.")
    assert not looks_blocked(None)


def test_instructions_take_the_org_name():
    assert "Example Org" in INSTRUCTIONS.format(org="Example Org")


def test_outcomes():
    assert classify("This request was blocked by the 'block-jailbreak' service policy.") == "blocked"
    assert classify("Please update card [CREDIT_CARD].") == "redacted"
    assert classify("Hello [Customer Name], here is your draft.") == "allowed"
    assert classify("I can't quote a clause I don't have.") == "refused"
    assert classify("") == "empty"
    assert passed("refused", "blocked|refused") and not passed("allowed", "blocked")
