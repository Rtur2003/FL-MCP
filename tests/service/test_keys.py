from fl_mcp.commerce import keys


def test_new_keys_are_well_formed_and_unique():
    seen = {keys.new_key() for _ in range(200)}
    assert len(seen) == 200
    for key in seen:
        assert key.startswith("FLMCP-") and len(key) == 29 and key == key.upper()
        assert keys.normalize(key) is not None and keys.display(key) == key


def test_typing_variants_of_a_key_are_accepted():
    key = keys.new_key()
    plain = key.replace("-", "")
    for variant in (key.lower(), plain, plain[5:], f" {key} ", key.replace("-", " ")):
        assert keys.hash_key(variant) == keys.hash_key(key)


def test_look_alike_characters_are_read_the_crockford_way():
    key = keys.new_key()
    body = keys.normalize(key)
    swapped = body.replace("0", "O").replace("1", "L")
    assert keys.hash_key(swapped) == keys.hash_key(key)


def test_every_single_wrong_character_is_caught():
    body = keys.normalize(keys.new_key())
    for i in range(20):
        for wrong in keys.ALPHABET:
            if wrong != body[i]:
                assert keys.normalize(body[:i] + wrong + body[i + 1 :]) is None, (i, wrong)


def test_swapping_neighbours_is_almost_always_caught():
    caught = total = 0
    for _ in range(40):
        body = keys.normalize(keys.new_key())
        for i in range(19):
            if body[i] != body[i + 1]:
                swapped = body[:i] + body[i + 1] + body[i] + body[i + 2 :]
                total += 1
                caught += keys.normalize(swapped) is None
    assert caught / total > 0.9


def test_garbage_is_not_a_key():
    for text in ("", "FLMCP", "hello world", "FLMCP-AAAAA-AAAAA-AAAAA", "ÿ" * 20, "U" * 20):
        assert keys.normalize(text) is None and keys.hash_key(text) is None
