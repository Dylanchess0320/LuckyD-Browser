"""Pack the extension into a signed CRX3 and emit a self-hosted update manifest.

Edge force-installs self-hosted extensions via ExtensionInstallForcelist
"extension_id;update_url". It fetches update_url (an XML update manifest),
downloads the CRX it points to, verifies the embedded CRX3 signature against
the extension id, and installs it silently and permanently.

This script:
  * loads key.pem (created by gen_key.py)
  * zips the extension (manifest.json first, uncompressed at offset 0-ish)
  * builds the CRX3 protobuf header (AsymmetricKeyProof + CrxFileHeader)
  * signs sha256(with_header) with the RSA key (PKCS1v15-SHA256)
  * writes extension.crx  and  update.xml
"""

import hashlib
import json
import struct
import sys
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as apadding
from cryptography.hazmat.primitives.serialization import load_pem_private_key

HERE = Path(__file__).resolve().parent
KEYFILE = HERE / "key.pem"
MANIFEST = HERE / "manifest.json"
CRX_OUT = HERE / "dist" / "luckyd-yt-adblock.crx"
UPDATE_XML = HERE / "dist" / "update.xml"
ID_FILE = HERE / "extension_id.txt"

EXCLUDE = {".pem", ".py", ".md", ".txt", ".bin", ".crx"}
EXCLUDE_DIRS = {"dist", "__pycache__"}


# ── minimal protobuf encoder (just what CRX3 needs) ───────────────────────
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _bytes(field: int, payload: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(payload)) + payload


def _int32(field: int, value: int) -> bytes:
    return _tag(field, 0) + _varint(value)


def build_zip() -> bytes:
    files = []
    for p in sorted(HERE.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(HERE)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if p.suffix.lower() in EXCLUDE:
            continue
        files.append(rel)
    # manifest first
    files.sort(key=lambda r: (r.name != "manifest.json", str(r)))
    from io import BytesIO

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for rel in files:
            z.write(HERE / rel, str(rel).replace("\\", "/"))
    return buf.getvalue()


def main() -> int:
    if not KEYFILE.exists():
        print("key.pem missing — run gen_key.py first")
        return 1
    key = load_pem_private_key(KEYFILE.read_bytes(), password=None)
    pub_der = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    ext_id = hashlib.sha256(pub_der).digest()[:16].hex()
    ext_id = "".join(chr(ord("a") + (b & 0x0F)) for b in bytes.fromhex(ext_id))
    ID_FILE.write_text(ext_id + "\n", encoding="utf-8")

    zip_bytes = build_zip()

    # AsymmetricKeyProof { public_key = 1; signature = 2; }  (signature empty for
    # the pre-signature pass; we sign the header that contains the empty proof,
    # per the CRX3 spec.)
    proof = _bytes(1, pub_der) + _bytes(2, b"")
    # CrxFileHeader {
    #   repeated AsymmetricKeyProof sha256_with_rsa = 2;
    #   bytes signed_header_data = 10000;   // SignedData { bytes crx_id = 1; }
    # }
    crx_id_raw = hashlib.sha256(pub_der).digest()[:16]
    signed_data = _bytes(1, crx_id_raw)
    crx_header = _bytes(2, proof) + _bytes(10000, signed_data)

    # signature input = "CRX3 SignedData\x00" + len(crx_header) + crx_header + zip
    magic_signed = b"CRX3 SignedData\x00"
    sig_input = magic_signed + struct.pack("<I", len(crx_header)) + crx_header + zip_bytes
    signature = key.sign(sig_input, apadding.PKCS1v15(), hashes.SHA256())

    # now rebuild header with the real signature in the proof
    proof = _bytes(1, pub_der) + _bytes(2, signature)
    crx_header = _bytes(2, proof) + _bytes(10000, signed_data)

    crx = (
        b"Cr24" + struct.pack("<I", 3) + struct.pack("<I", len(crx_header)) + crx_header + zip_bytes
    )

    CRX_OUT.parent.mkdir(parents=True, exist_ok=True)
    CRX_OUT.write_bytes(crx)

    version = json.loads(MANIFEST.read_text(encoding="utf-8")).get("version", "1.0.0")
    codebase = "http://127.0.0.1:8791/luckyd-yt-adblock.crx"
    UPDATE_XML.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gupdate xmlns="http://www.google.com/update2/response" protocol="2.0">\n'
        f'  <app appid="{ext_id}">\n'
        f'    <updatecheck codebase="{codebase}" version="{version}" />\n'
        "  </app>\n"
        "</gupdate>\n",
        encoding="utf-8",
    )

    print(f"extension id : {ext_id}")
    print(f"version      : {version}")
    print(f"zip bytes    : {len(zip_bytes)}")
    print(f"crx bytes    : {len(crx)}  -> {CRX_OUT}")
    print(f"update.xml   -> {UPDATE_XML}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
