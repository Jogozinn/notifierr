"""HTTP-only service entrypoint: python -m backend.api."""

import os


def main() -> None:
    os.environ.setdefault("NOTIFIERR_ROLE", "api")
    from .config import load_settings

    if load_settings().process_role != "api":
        raise RuntimeError("API entrypoint requires NOTIFIERR_ROLE=api")
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("TRUSTED_PROXY_IPS", "127.0.0.1"),
        workers=1,
    )


if __name__ == "__main__":
    main()
