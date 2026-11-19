"""Formuliervelden voor het toevoegen van een torrent aan qBittorrent."""

FILENAME = "release.torrent"
CONTENT_TYPE = "application/x-bittorrent"


def add_fields(category: str, pause_field: str, *, paused: bool, tags: list[str] | None = None,
               save_path: str | None = None, skip_checking: bool = False) -> dict[str, str]:
    fields = {"category": category, pause_field: "true" if paused else "false",
              "skip_checking": "true" if skip_checking else "false"}
    if tags:
        fields["tags"] = ",".join(tags)
    if save_path:
        fields.update(savepath=save_path, autoTMM="false", contentLayout="Original")
    return fields
