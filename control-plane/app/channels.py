"""Channel registry: maps platform keys to runner API paths."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Channel:
    key: str
    label: str
    prefix: str
    login_open: str | None = None
    login_qr: str | None = None
    maintained: bool = True

    def endpoint(self, action: str) -> str:
        return f"{self.prefix}/{action}"


CHANNELS = {
    "boss": Channel(
        "boss",
        "Boss直聘",
        "/api/boss",
        login_open="/api/offerbuddy/login",
        login_qr="/api/offerbuddy/qr",
    ),
    "liepin": Channel("liepin", "猎聘", "/api/liepin"),
    "51job": Channel("51job", "51job", "/api/51job", maintained=False),
    "zhilian": Channel("zhilian", "智联招聘", "/api/zhilian", maintained=False),
}