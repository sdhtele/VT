from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator, Optional, Union

import pymysql

from vinetrimmer.utils.AtomicSQL import AtomicSQL


class Vault:
    """
    Key Vault.
    This defines various details about the vault, including its Connection object.
    """

    def __init__(self, vault_type: str, name: str, ticket: Optional[bytes] = None, path: Optional[str] = None,
                 username: Optional[str] = None, password: Optional[str] = None, database: Optional[str] = None,
                 host: Optional[str] = None):
        self.vault_type = vault_type.upper()
        self.name = name
        self.con: Union[pymysql.connections.Connection, sqlite3.dbapi2.Connection]
        if self.vault_type == Vault.Types.LOCAL:
            if not path:
                raise ValueError("Local vault has no path specified")
            self.con = sqlite3.connect(Path(path).expanduser())
        elif self.vault_type == Vault.Types.REMOTE:
            self.con = pymysql.connect(
                user=username,
                password=password or "",
                db=database,
                host=host,
                cursorclass=pymysql.cursors.DictCursor  # TODO: Needed? Maybe use it on sqlite3 too?
            )
        else:
            raise ValueError(f"Invalid vault type [{self.vault_type}]")
        self.ph = {"LOCAL": "?", "REMOTE": "%s"}[self.vault_type]
        self.ticket = ticket

        self.perms = self.get_permissions()
        if not self.has_permission("SELECT"):
            raise ValueError(f"Cannot use vault. Vault {self.name} has no SELECT permission.")

    def get_permissions(self) -> list:
        if self.vault_type == self.Types.LOCAL:
            return [tuple([["*"], tuple(["*", "*"])])]

        with self.con.cursor() as c:
            c.execute("SHOW GRANTS")
            grants = c.fetchall()
            grants = [next(iter(x.values())) for x in grants]
        grants = [tuple(x[6:].split(" TO ")[0].split(" ON ")) for x in list(grants)]
        grants = [(
            list(map(str.strip, perms.replace("ALL PRIVILEGES", "*").split(","))),
            location.replace("`", "").split(".")
        ) for perms, location in grants]

        return grants

    def has_permission(self, operation: str, database: Optional[str] = None, table: Optional[str] = None) -> bool:
        grants = [x for x in self.perms if x[0] == ["*"] or operation.upper() in x[0]]
        if grants and database:
            grants = [x for x in grants if x[1][0] in (database, "*")]
        if grants and table:
            grants = [x for x in grants if x[1][1] in (table, "*")]
        return bool(grants)

    class Types:
        LOCAL = "LOCAL"
        REMOTE = "REMOTE"


class Vaults:
    """
    Key Vaults.
    Keeps hold of Vault objects, with convenience functions for
    using multiple vaults in one actions, e.g. searching vaults
    for a key based on kid.
    This object uses AtomicSQL for accessing the vault connections
    instead of directly. This is to provide thread safety but isn't
    strictly necessary.
    """

    def __init__(self, vaults: list[Vault], service: str):
        self.adb = AtomicSQL()
        self.vaults = sorted(vaults, key=lambda v: 0 if v.vault_type == Vault.Types.LOCAL else 1)
        self.service = service.lower()
        for vault in self.vaults:
            vault.ticket = self.adb.load(vault.con)
            if vault.vault_type == Vault.Types.LOCAL:
                self.create_table(vault, self.service, commit=True)

    def __iter__(self) -> Iterator[Vault]:
        return iter(self.vaults)

    def get(self, kid: str, title: Union[str, int]) -> tuple[Optional[str], Optional[Vault]]:
        for vault in self.vaults:
            # Note on why it matches by KID instead of PSSH:
            # Matching cache by pssh is not efficient. The PSSH can be made differently by all different
            # clients for all different reasons, e.g. only having the init data, but the cached PSSH is
            # a manually crafted PSSH, which may not match other clients manually crafted PSSH, and such.
            # So it searches by KID instead for this reason, as the KID has no possibility of being different
            # client to client other than capitalization. There is an unknown with KID matching, It's unknown
            # for *sure* if the KIDs ever conflict or not with another bitrate/stream/title. I haven't seen
            # this happen ever and neither has anyone I have asked.
            if not self.table_exists(vault, self.service):
                continue  # this service has no service table, so no keys, just skip
            if not vault.ticket:
                raise ValueError(f"Vault {vault.name} does not have a valid ticket available.")
            c = self.adb.safe_execute(
                vault.ticket,
                lambda db, cursor: cursor.execute(
                    "SELECT `id`, `key_`, `title` FROM `{1}` WHERE `kid`={0}".format(vault.ph, self.service),
                    [kid]
                )
            ).fetchone()
            if c:
                if isinstance(c, dict):
                    c = list(c.values())
                if not c[2] and vault.has_permission("UPDATE", table=self.service):
                    self.adb.safe_execute(
                        vault.ticket,
                        lambda db, cursor: cursor.execute(
                            "UPDATE `{1}` SET `title`={0} WHERE `id`={0}".format(vault.ph, self.service),
                            [title, c[0]]
                        )
                    )
                    self.commit(vault)
                return c[1], vault
        return None, None

    def table_exists(self, vault: Vault, table: str) -> bool:
        if not vault.ticket:
            raise ValueError(f"Vault {vault.name} does not have a valid ticket available.")
        if vault.vault_type == Vault.Types.LOCAL:
            return self.adb.safe_execute(
                vault.ticket,
                lambda db, cursor: cursor.execute(
                    f"SELECT count(name) FROM sqlite_master WHERE type='table' AND name={vault.ph}",
                    [table]
                )
            ).fetchone()[0] == 1
        return list(self.adb.safe_execute(
            vault.ticket,
            lambda db, cursor: cursor.execute(
                f"SELECT count(TABLE_NAME) FROM information_schema.TABLES WHERE TABLE_NAME={vault.ph}",
                [table]
            )
        ).fetchone().values())[0] == 1

    def create_table(self, vault: Vault, table: str, commit: bool = False) -> None:
        if self.table_exists(vault, table):
            return
        if not vault.ticket:
            raise ValueError(f"Vault {vault.name} does not have a valid ticket available.")
        if vault.has_permission("CREATE"):
            print(f"Creating `{table}` table in {vault.name} ({vault.vault_type}) key vault...")
            self.adb.safe_execute(
                vault.ticket,
                lambda db, cursor: cursor.execute(
                    "CREATE TABLE IF NOT EXISTS {} (".format(table) + (
                        """
                        "id"        INTEGER NOT NULL UNIQUE,
                        "kid"       TEXT NOT NULL COLLATE NOCASE,
                        "key_"      TEXT NOT NULL COLLATE NOCASE,
                        "title"     TEXT NULL,
                        PRIMARY KEY("id" AUTOINCREMENT),
                        UNIQUE("kid", "key_")
                        """ if vault.vault_type == Vault.Types.LOCAL else
                        """
                        id          int AUTO_INCREMENT PRIMARY KEY,
                        kid         VARCHAR(255) NOT NULL,
                        key_        VARCHAR(255) NOT NULL,
                        title       TEXT NULL,
                        UNIQUE(kid, key_)
                        """
                    ) + ");"
                )
            )
            if commit:
                self.commit(vault)

    def insert_key(
        self, vault: Vault, table: str, kid: str, key: str, title: Union[str, int], commit: bool = False
    ) -> bool:
        if not self.table_exists(vault, table):
            return False
        if not vault.ticket:
            raise ValueError(f"Vault {vault.name} does not have a valid ticket available.")
        if not vault.has_permission("INSERT", table=table):
            raise ValueError(f"Cannot insert key into Vault. Vault {vault.name} has no INSERT permission.")
        if self.adb.safe_execute(
            vault.ticket,
            lambda db, cursor: cursor.execute(
                "SELECT `id` FROM `{1}` WHERE `kid`={0} AND `key_`={0}".format(vault.ph, self.service),
                [kid, key]
            )
        ).fetchone():
            return True
        self.adb.safe_execute(
            vault.ticket,
            lambda db, cursor: cursor.execute(
                "INSERT INTO `{1}` (kid, key_, title) VALUES ({0}, {0}, {0})".format(vault.ph, table),
                (kid, key, title)
            )
        )
        if commit:
            self.commit(vault)
        return True

    def commit(self, vault: Vault) -> None:
        assert vault.ticket is not None
        self.adb.commit(vault.ticket)
