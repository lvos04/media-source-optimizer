from pydantic import BaseModel, ConfigDict


class NoQuery(BaseModel):
    """Een leesroute zonder opties weigert ook een impliciete verversopdracht."""

    model_config = ConfigDict(extra="forbid")
