from dataclasses import dataclass, field


@dataclass(frozen=True)
class FieldDraft:
    key: str
    label: str = ""
    json_type: str = "string"
    enabled: bool = True
    order: int = 0


@dataclass(frozen=True)
class SheetDraft:
    name: str
    description: str = ""
    fields: list = field(default_factory=list)
