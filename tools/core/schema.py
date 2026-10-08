"""数据模型与字段常量。

对应 FFS（Fixed Format Script）规范：
  HEADER 6 行：TITLE / EP / PLATFORM / STYLE LOCK / CHAR / SCENE
  镜头块 8 字段：SHOT / LOCATION / TIME / CHAR / ACTION / DIALOG / VO / SFX
  本项目扩展 1 个可选字段：DUR（单镜预估秒数，缺省 8 秒）
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

HEADER_FIELDS = ("TITLE", "EP", "PLATFORM", "STYLE LOCK", "CHAR", "SCENE")
SHOT_FIELDS = ("SHOT", "LOCATION", "TIME", "CHAR", "ACTION", "DIALOG", "VO", "SFX")
EXT_FIELDS = ("DUR",)  # 本项目扩展，白名单，不触发 E004
ALL_SHOT_FIELDS = SHOT_FIELDS + EXT_FIELDS

DEFAULT_SHOT_SEC = 8
MIN_SHOT_SEC = 4
MAX_SHOT_SEC = 15
MIN_EP_SEC = 90
MAX_EP_SEC = 120
MIN_EP_SHOTS = 6
MAX_EP_SHOTS = 15

# E009：ACTION 抽象词黑名单
ABSTRACT_WORDS = (
    "气氛微妙", "微妙", "意味深长", "情绪复杂", "心情复杂", "百感交集",
    "若有所思地", "复杂的心情", "气氛凝重", "氛围感",
)

# E003：出现在 ACTION 里但不算代号的全大写英文词
CODE_TOKEN_WHITELIST = {
    "AI", "USB", "GPS", "DNA", "SMS", "WiFi", "WIFI", "TV", "LED", "CCTV",
    "OK", "PM", "AM", "ID", "PC", "APP", "VR", "AR", "MRI", "CT",
}

LEVEL_ERROR = "ERROR"
LEVEL_WARN = "WARN"


def sha1(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


@dataclass
class Character:
    code: str
    name: str
    anchor_text: str
    palette: str = ""
    voice: str = ""
    lora_path: str = ""
    trigger_word: str = ""
    anchor_hash: str = ""
    anchor_version: int = 1

    def freeze(self) -> "Character":
        self.anchor_hash = sha1(self.anchor_text)
        return self


@dataclass
class Scene:
    code: str
    desc: str
    int_ext: str = "内景"   # 内景 / 外景
    time: str = "夜"
    ref_image: str = ""
    anchor_hash: str = ""

    def freeze(self) -> "Scene":
        self.anchor_hash = sha1(self.desc)
        return self


@dataclass
class Shot:
    sc_id: str
    seq: int
    shot: str
    location: str
    time: str
    char: str
    action: str
    dialog: str
    vo: str
    sfx: str
    est_sec: int = DEFAULT_SHOT_SEC


@dataclass
class EpisodeScript:
    ep_no: int
    title: str
    header: dict = field(default_factory=dict)
    shots: list = field(default_factory=list)

    @property
    def total_sec(self) -> int:
        return sum(s.est_sec for s in self.shots)


@dataclass
class Issue:
    code: str          # E001..E011
    level: str         # ERROR / WARN
    where: str         # 定位，如 SC-03
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "level": self.level,
                "where": self.where, "message": self.message}


@dataclass
class ShotPrompt:
    """分镜阶段的一镜提示词（从 seedance 输出回灌）。"""
    ep_no: int
    no: str            # 镜头一
    est_sec: int
    text: str
    sfx: str = ""
    dialogs: list = field(default_factory=list)
