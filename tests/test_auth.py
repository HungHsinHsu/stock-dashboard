from core.auth import hash_password, verify_password


def test_hash_roundtrip_and_wrong():
    h = hash_password("secret123")
    assert "$" in h                       # salt$hash
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)


def test_verify_bad_format():
    assert not verify_password("x", None)
    assert not verify_password("x", "")
    assert not verify_password("x", "no-dollar-sign")


def test_salt_makes_hashes_differ():
    assert hash_password("same") != hash_password("same")   # 隨機鹽
