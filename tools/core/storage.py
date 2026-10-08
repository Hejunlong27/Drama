"""SQLite 存储层。

定位：Markdown 是唯一真源，SQLite 只做索引与引用完整性校验，
可随时由 `cli.py reindex` 从 md 全量重建。禁止直接手改库。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .schema import Character, Scene, sha1

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects(
  slug TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  platform TEXT,
  total_ep INTEGER,
  ep_seconds INTEGER DEFAULT 110,
  style_lock TEXT NOT NULL,
  style_lock_hash TEXT NOT NULL,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS characters(
  project TEXT NOT NULL REFERENCES projects(slug),
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  anchor_text TEXT NOT NULL,
  anchor_hash TEXT NOT NULL,
  anchor_version INTEGER DEFAULT 1,
  lora_path TEXT,
  trigger_word TEXT,
  palette TEXT,
  voice TEXT,
  PRIMARY KEY(project, code)
);

CREATE TABLE IF NOT EXISTS scenes(
  project TEXT NOT NULL REFERENCES projects(slug),
  code TEXT NOT NULL,
  desc TEXT NOT NULL,
  int_ext TEXT DEFAULT '内景',
  time TEXT DEFAULT '夜',
  anchor_hash TEXT,
  ref_image TEXT,
  PRIMARY KEY(project, code)
);

CREATE TABLE IF NOT EXISTS episodes(
  project TEXT NOT NULL REFERENCES projects(slug),
  ep_no INTEGER NOT NULL,
  title TEXT,
  summary TEXT,
  hook TEXT,
  script_path TEXT,
  storyboard_path TEXT,
  shot_count INTEGER,
  total_sec INTEGER,
  status TEXT,
  PRIMARY KEY(project, ep_no)
);

CREATE TABLE IF NOT EXISTS shots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project TEXT NOT NULL,
  ep_no INTEGER NOT NULL,
  sc_id TEXT NOT NULL,
  seq INTEGER,
  shot TEXT,
  location TEXT,
  time TEXT,
  char TEXT,
  action TEXT,
  dialog TEXT,
  vo TEXT,
  sfx TEXT,
  est_sec INTEGER,
  UNIQUE(project, ep_no, sc_id),
  FOREIGN KEY(project, ep_no) REFERENCES episodes(project, ep_no)
);

CREATE TABLE IF NOT EXISTS assets(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project TEXT,
  owner_type TEXT,
  owner_id TEXT,
  kind TEXT,
  path TEXT,
  sha1 TEXT,
  note TEXT
);

-- Phase 2/3 预留，本期只建表不实现
CREATE TABLE IF NOT EXISTS generation_jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project TEXT,
  ep_no INTEGER,
  sc_id TEXT,
  engine TEXT,
  workflow_id TEXT,
  params_json TEXT,
  status TEXT,
  output_path TEXT,
  cost REAL,
  created_at TEXT
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def upsert_project(conn, slug, title, platform, total_ep, ep_seconds, style_lock):
    conn.execute(
        "INSERT INTO projects(slug,title,platform,total_ep,ep_seconds,"
        "style_lock,style_lock_hash,created_at) VALUES(?,?,?,?,?,?,?,datetime('now')) "
        "ON CONFLICT(slug) DO UPDATE SET title=excluded.title,platform=excluded.platform,"
        "total_ep=excluded.total_ep,ep_seconds=excluded.ep_seconds,"
        "style_lock=excluded.style_lock,style_lock_hash=excluded.style_lock_hash",
        (slug, title, platform, total_ep, ep_seconds, style_lock, sha1(style_lock)),
    )
    conn.commit()


def upsert_character(conn, project: str, c: Character) -> None:
    c.freeze()
    conn.execute(
        "INSERT INTO characters(project,code,name,anchor_text,anchor_hash,"
        "anchor_version,lora_path,trigger_word,palette,voice) "
        "VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(project,code) DO UPDATE SET name=excluded.name,"
        "anchor_text=excluded.anchor_text,anchor_hash=excluded.anchor_hash,"
        "palette=excluded.palette,voice=excluded.voice,"
        "lora_path=excluded.lora_path,trigger_word=excluded.trigger_word",
        (project, c.code, c.name, c.anchor_text, c.anchor_hash, c.anchor_version,
         c.lora_path, c.trigger_word, c.palette, c.voice),
    )
    conn.commit()


def upsert_scene(conn, project: str, s: Scene) -> None:
    s.freeze()
    conn.execute(
        "INSERT INTO scenes(project,code,desc,int_ext,time,anchor_hash,ref_image) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(project,code) DO UPDATE SET desc=excluded.desc,"
        "int_ext=excluded.int_ext,time=excluded.time,anchor_hash=excluded.anchor_hash",
        (project, s.code, s.desc, s.int_ext, s.time, s.anchor_hash, s.ref_image),
    )
    conn.commit()


def upsert_episode(conn, project, ep_no, **kw):
    cols = ["project", "ep_no"]
    vals = [project, ep_no]
    for k in ("title", "summary", "hook", "script_path",
              "storyboard_path", "shot_count", "total_sec", "status"):
        if k in kw:
            cols.append(k)
            vals.append(kw[k])
    placeholders = ",".join("?" * len(cols))
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("project", "ep_no"))
    sql = (f"INSERT INTO episodes({','.join(cols)}) VALUES({placeholders}) "
           f"ON CONFLICT(project,ep_no) DO UPDATE SET {updates}")
    conn.execute(sql, vals)
    conn.commit()


def upsert_shot(conn, project, ep_no, s) -> None:
    conn.execute(
        "INSERT INTO shots(project,ep_no,sc_id,seq,shot,location,time,char,"
        "action,dialog,vo,sfx,est_sec) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(project,ep_no,sc_id) DO UPDATE SET seq=excluded.seq,"
        "shot=excluded.shot,location=excluded.location,time=excluded.time,"
        "char=excluded.char,action=excluded.action,dialog=excluded.dialog,"
        "vo=excluded.vo,sfx=excluded.sfx,est_sec=excluded.est_sec",
        (project, ep_no, s.sc_id, s.seq, s.shot, s.location, s.time, s.char,
         s.action, s.dialog, s.vo, s.sfx, s.est_sec),
    )
    conn.commit()


def char_codes(conn, project) -> set:
    return {r["code"] for r in conn.execute(
        "SELECT code FROM characters WHERE project=?", (project,))}


def scene_codes(conn, project) -> set:
    return {r["code"] for r in conn.execute(
        "SELECT code FROM scenes WHERE project=?", (project,))}


def get_character(conn, project, code):
    r = conn.execute("SELECT * FROM characters WHERE project=? AND code=?",
                     (project, code)).fetchone()
    return dict(r) if r else None


def get_project(conn, slug):
    r = conn.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
    return dict(r) if r else None
