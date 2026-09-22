import pytest
from browser.extension.gen_key import ext_id_from_der


def test_ext_id_from_der_known_input():
    # Known input and expected output
    der_input = b"test"
    # hashlib.sha256(b"test").hexdigest()[:32] == "9f86d081884c7d659a2feaa0c55ad015"
    # Mapping hex characters to a-p:
    # 9 -> j
    # f -> p
    # 8 -> i
    # 6 -> g
    # d -> n
    # 0 -> a
    # 8 -> i
    # 1 -> b
    # 8 -> i
    # 8 -> i
    # 4 -> e
    # c -> m
    # 7 -> h
    # d -> n
    # 6 -> g
    # 5 -> f
    # 9 -> j
    # a -> k
    # 2 -> c
    # f -> p
    # e -> o
    # a -> k
    # a -> k
    # 0 -> a
    # c -> m
    # 5 -> f
    # 5 -> f
    # a -> k
    # d -> n
    # 0 -> a
    # 1 -> b
    # 5 -> f
    expected_id = "jpignaibiiemhngfjkcpokkamffknabf"
    assert ext_id_from_der(der_input) == expected_id

def test_ext_id_from_der_empty():
    der_input = b""
    expected_id = "odlameecjipmbmbejkplpemijjgpljce"
    assert ext_id_from_der(der_input) == expected_id

def test_ext_id_from_der_determinism():
    der_input = b"another test"
    assert ext_id_from_der(der_input) == ext_id_from_der(der_input)
