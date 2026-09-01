"""Socket to Ciphertext -- an end-to-end encrypted instant messaging system.

Layout:
    im.common   frames, codec, ids -- shared by both sides of the wire
    im.crypto   X25519 identity, AES-GCM envelope, TLS context helpers
    im.server   accept loop, handlers, router, registries, store
    im.client   connection, model, controller, views
"""

__version__ = "0.1.0"
