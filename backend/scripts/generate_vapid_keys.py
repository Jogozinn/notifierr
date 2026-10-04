from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


private_key = ec.generate_private_key(ec.SECP256R1())
private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
public_bytes = private_key.public_key().public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint,
)

print("VAPID_PUBLIC_KEY=" + base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode())
print("VAPID_PRIVATE_KEY_B64=" + base64.b64encode(private_pem).decode())
print("VAPID_SUBJECT=mailto:YOUR_EMAIL@example.com")
