import hashlib


def hash_password(password: str) -> str:
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def cache_key(url: str, params: dict) -> str:
    raw = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def etag(body: bytes) -> str:
    return '"' + hashlib.sha1(body).hexdigest() + '"'
