import hashlib

def encrypt_salt(raw: str) -> str:
    result = []
    for ch in raw:
        c = ord(ch)
        if ch.isalpha():
            result.append(chr(0x9B - c) if ch.isupper() else chr(0xDB - c))
        elif ch.isdigit():
            result.append(chr(0x69 - c))
        else:
            result.append(ch)
    return "".join(result)

SALT = encrypt_salt("6zF0R8;/")  # → "3aU9I1;/"

def sign(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hashlib.md5((SALT + query).encode()).hexdigest()
