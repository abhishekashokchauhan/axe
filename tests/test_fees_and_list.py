import pytest

from grocer.config import parse_list_line
from grocer.fees import Observation, learn


def test_learn_instamart_like_history():
    # shape of your real Instamart bills: delivery varies, packaging always 11
    obs = [Observation(195, {"deliveryFee": 30, "packagingFee": 11}),
           Observation(211, {"deliveryFee": 16, "packagingFee": 11}),
           Observation(420, {"deliveryFee": 0, "packagingFee": 11})]
    learned = learn(obs)
    fees = {f.name: f for f in learned.schedule.fees}
    assert fees["delivery fee"].amount == 23 and fees["delivery fee"].below == 420
    assert fees["packaging fee"].amount == 11 and fees["packaging fee"].below is None
    assert learned.schedule.fees_for(300) == [("delivery fee", 23), ("packaging fee", 11)]
    assert learned.schedule.fees_for(500) == [("packaging fee", 11)]


def test_learn_zepto_like_history_no_fees():
    learned = learn([Observation(203, {"fees": 0}), Observation(596, {"fees": 0})])
    assert learned.schedule.fees == () and "no fees charged" in learned.summary
    assert learn([]).schedule.fees == ()


@pytest.mark.parametrize("line,expected", [
    ("Amul Gold milk pouch, 500ml", ("Amul Gold milk pouch", "Amul", "500ml", 1)),
    ("Tata groundnut oil, 5L", ("Tata groundnut oil", "Tata", "5L", 1)),
    ("Amul butter, 500 g, 2", ("Amul butter", "Amul", "500g", 2)),
    ("Amul cheese block 200g", ("Amul cheese block", "Amul", "200g", 1)),
    ("Amul butter, 500g, x3", ("Amul butter", "Amul", "500g", 3)),
])
def test_parse_list_line(line, expected):
    i = parse_list_line(line)
    assert (i.name, i.brand, i.size, i.qty) == expected


def test_parse_list_line_blank_comment_and_errors():
    assert parse_list_line("   # just a note") is None
    assert parse_list_line("") is None
    with pytest.raises(ValueError, match="no pack size"):
        parse_list_line("Amul butter")


def test_token_scope_check_uses_requested_scope(tmp_path, monkeypatch):
    import anyio

    from grocer import auth
    from mcp.shared.auth import OAuthToken

    monkeypatch.setattr(auth, "TOKEN_DIR", tmp_path)
    tok = OAuthToken(access_token="x", scope="tools:read")  # Zepto reports read-only...
    store = auth.FileTokenStorage("zepto", "tools:read tools:write")
    anyio.run(store.set_tokens, tok)
    assert anyio.run(store.get_tokens) is not None  # ...but we asked for write: no re-login loop
    old = auth.FileTokenStorage("zepto", "tools:read")
    anyio.run(old.set_tokens, tok)
    assert anyio.run(store.get_tokens) is None  # a login made asking read-only must be redone
