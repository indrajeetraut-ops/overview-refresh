from server.uploader.ids import make_producer_id


def test_uuid5_is_deterministic():
    a = make_producer_id("acme", "anil")
    b = make_producer_id("acme", "anil")
    assert a == b


def test_uuid5_varies_by_org_and_user():
    base = make_producer_id("acme", "anil")
    assert make_producer_id("acme", "other-user") != base
    assert make_producer_id("other-org", "anil") != base
