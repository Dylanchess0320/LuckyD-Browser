"""Generate an RSA key for the extension, bake the public key into
manifest.json (fixing the extension ID), and write a CRX3 header file.

The extension ID is the first 16 bytes of the SHA-256 of the DER-encoded
SubjectPublicKeyInfo, mapped 0-15 -> a-p. Baking "key" into the manifest makes
the ID stable across every install, which lets us register the extension in
Edge/Chrome/Brave via the registry (hardcoded, no "Load unpacked")."""

import base64
import hashlib
import json
import struct
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
KEYFILE = HERE / "key.pem"
CRX_HEADER = HERE / "crx_header.bin"


def ext_id_from_der(der: bytes) -> str:
    digest = hashlib.sha256(der).digest()
    return "".join(chr(ord("a") + (b & 0x0F)) for b in digest[:16])


def main() -> int:
    if KEYFILE.exists():
        priv_pem = KEYFILE.read_bytes()
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        key = load_pem_private_key(priv_pem, password=None)
        print("reusing existing key.pem")
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        KEYFILE.write_bytes(priv_pem)
        print("generated new key.pem")

    pub = key.public_key()
    pub_der = pub.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    b64_key = base64.b64encode(pub_der).decode("ascii")
    ext_id = ext_id_from_der(pub_der)

    # Bake key into manifest
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data["key"] = b64_key
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # CRX3 header: magic + version + header-length placeholder.
    # (A real CRX needs a signature; for registry "path" installs the browser
    #  loads the UNPACKED folder, so this header is only kept for reference /
    #  future packing. The registry path install is what actually hardcodes it.)
    header = b"Cr24" + struct.pack("<I", 3) + struct.pack("<I", 0)
    CRX_HEADER.write_bytes(header)

    print(f"extension id: {ext_id}")
    print(f"manifest updated with key ({len(b64_key)} chars)")
    (HERE / "extension_id.txt").write_text(ext_id + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
