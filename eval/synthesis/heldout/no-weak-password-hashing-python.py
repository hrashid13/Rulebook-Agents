import hashlib

import bcrypt


def store(password, salt):
    # ruleid: no-weak-password-hashing-python
    a = hashlib.md5(password.encode()).hexdigest()
    # ruleid: no-weak-password-hashing-python
    b = hashlib.sha1(password.encode()).hexdigest()
    # ruleid: no-weak-password-hashing-python
    c = hashlib.new("md5", password.encode()).hexdigest()
    # ok: no-weak-password-hashing-python
    d = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
    # ok: no-weak-password-hashing-python
    e = hashlib.sha256(salt + password.encode()).hexdigest()
    return a, b, c, d, e
