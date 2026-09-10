"""TLS for the socket, and a self-signed certificate to develop against.

Two layers of protection that do different jobs, and it is worth being clear
about which is which.

TLS protects the *link*: anyone between a client and the server -- on the same
wifi, at the hosting provider, inside a tunnel -- sees an encrypted stream
rather than usernames and message metadata.

End-to-end encryption protects the *content*: even the server cannot read it.

Neither replaces the other. Without TLS, an eavesdropper learns who is talking
to whom even though bodies are ciphertext. Without end-to-end encryption, the
server sees everything.
"""

from __future__ import annotations

import datetime
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

DEV_DAYS = 365


def generate_self_signed(
    cert_path: str | Path,
    key_path: str | Path,
    common_name: str = "localhost",
) -> tuple[Path, Path]:
    """Write a development certificate and its key, if they are not there.

    Self-signed, so no client will trust it without being told to. That is the
    correct behaviour, not a bug -- a certificate nobody vouches for should
    not be accepted silently. A real deployment gets one from a certificate
    authority instead.
    """
    cert_path, key_path = Path(cert_path), Path(key_path)
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)

    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)  # Self-signed: subject and issuer are the same.
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=DEV_DAYS))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(common_name)]), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    return cert_path, key_path


def server_context(cert_path: str | Path, key_path: str | Path) -> ssl.SSLContext:
    """The server side of the handshake."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert_path), str(key_path))
    return context


def client_context(cert_path: str | Path | None = None) -> ssl.SSLContext:
    """The client side.

    Given the development certificate, it is trusted as a certificate
    authority and the hostname is still checked -- so the client verifies it
    is talking to the server it was told about, rather than skipping
    verification entirely. Given nothing, the system's trust store is used,
    which is what a real deployment wants.
    """
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if cert_path is not None:
        context.load_verify_locations(str(cert_path))
    return context
