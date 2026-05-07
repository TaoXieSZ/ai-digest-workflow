"""URL canonicalization — strip tracking params, normalize host/path/fragment."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Tracking params to strip. Lowercased; matched case-insensitively.
# xsec_token / xsec_source: 小红书 — 同一帖每次访问都返回不同 token，不去掉
# 会让 hash(canonical_url) 把同一帖识别成不同 item，dedup 全废。
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "yclid",
        "msclkid",
        "ref",
        "ref_src",
        "ref_url",
        "spm",  # 阿里系
        "from",  # 微信/微博
        "share_token",
        "share_source",
        "share_from",
        "xsec_token",  # 小红书：每次抓都不同的访问令牌
        "xsec_source",  # 小红书：访问入口标识，跟 token 同样易变
    }
)


def canonicalize(url: str) -> str:
    """Strip tracking params, lowercase host, drop fragment, sort remaining query.

    Returns empty string if input is empty/whitespace.
    """
    s = (url or "").strip()
    if not s:
        return ""

    parsed = urlparse(s)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()

    query_pairs = parse_qsl(parsed.query, keep_blank_values=False)
    cleaned = [(k, v) for k, v in query_pairs if k.lower() not in _TRACKING_PARAMS]
    cleaned.sort()
    query = urlencode(cleaned)

    # Trailing slash: keep on root, strip elsewhere
    path = parsed.path
    if path and path != "/":
        path = path.rstrip("/") or "/"

    return urlunparse((scheme, netloc, path, parsed.params, query, ""))
