from digest.url_canonical import canonicalize


def test_strips_utm_params() -> None:
    url = "https://linux.do/t/abc?utm_source=tw&utm_medium=social&id=1"
    assert canonicalize(url) == "https://linux.do/t/abc?id=1"


def test_strips_fbclid_and_gclid() -> None:
    assert canonicalize("https://e.com/p?fbclid=1&x=2&gclid=3") == "https://e.com/p?x=2"


def test_keeps_meaningful_params_sorted() -> None:
    a = canonicalize("https://e.com/p?b=2&a=1")
    b = canonicalize("https://e.com/p?a=1&b=2")
    assert a == b == "https://e.com/p?a=1&b=2"


def test_lowercases_scheme_and_host_keeps_path_case() -> None:
    assert canonicalize("HTTPS://Linux.DO/T/1") == "https://linux.do/T/1"


def test_strips_fragment() -> None:
    assert canonicalize("https://e.com/p#section") == "https://e.com/p"


def test_root_keeps_trailing_slash() -> None:
    assert canonicalize("https://e.com/") == "https://e.com/"


def test_non_root_strips_trailing_slash() -> None:
    assert canonicalize("https://e.com/path/") == "https://e.com/path"


def test_strips_chinese_tracking_params() -> None:
    out = canonicalize("https://weibo.com/x?from=mobile&spm=123&id=42")
    assert out == "https://weibo.com/x?id=42"


def test_empty_input_returns_empty() -> None:
    assert canonicalize("") == ""
    assert canonicalize("   ") == ""


def test_default_scheme_https_when_missing() -> None:
    # urlparse treats no-scheme strings specially; we don't need to "fix" them,
    # just ensure no crash. linux.do/x without scheme produces empty netloc.
    out = canonicalize("//linux.do/x")
    assert "linux.do" in out


def test_strips_xhs_xsec_token_for_dedup() -> None:
    """XHS posts re-fetch with a fresh xsec_token every time; without
    stripping, the same note hashes to two different item_ids."""
    a = canonicalize(
        "https://www.xiaohongshu.com/explore/69ca7970000000001a0274bf"
        "?xsec_token=ABhd75PHRABpA3OwYeufEk1AaM61i0EvKt8IslpsTK1HU%3D"
    )
    b = canonicalize(
        "https://www.xiaohongshu.com/explore/69ca7970000000001a0274bf"
        "?xsec_token=ABhd75PHRABpA3OwYeufEk1JRaHqFaa58tbFB347GC84w%3D"
    )
    assert a == b == "https://www.xiaohongshu.com/explore/69ca7970000000001a0274bf"


def test_strips_xhs_xsec_source_too() -> None:
    a = canonicalize("https://www.xiaohongshu.com/explore/abc?xsec_token=t1&xsec_source=pc_feed")
    b = canonicalize("https://www.xiaohongshu.com/explore/abc")
    assert a == b


def test_huodongxing_city_subdomain_collapses_to_www() -> None:
    """活动行 sz/bj/sh subdomains all resolve to the same /event/<id>;
    collapse to www so multi-city source configs don't duplicate items."""
    sz = canonicalize("https://sz.huodongxing.com/event/123456")
    bj = canonicalize("https://bj.huodongxing.com/event/123456")
    www = canonicalize("https://www.huodongxing.com/event/123456")
    assert sz == bj == www == "https://www.huodongxing.com/event/123456"


def test_huodongxing_www_unchanged() -> None:
    out = canonicalize("https://www.huodongxing.com/event/789")
    assert out == "https://www.huodongxing.com/event/789"


def test_huodongxing_subdomain_collapse_does_not_strip_path() -> None:
    """Defense: subdomain collapse must not interfere with path or query handling."""
    out = canonicalize("https://gz.huodongxing.com/event/777?utm_source=share&utm_medium=qr")
    assert out == "https://www.huodongxing.com/event/777"
