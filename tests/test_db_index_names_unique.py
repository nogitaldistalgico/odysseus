"""init_db() must succeed on a fresh database.

SQLite index names are database-wide. StudioMedia once declared its owner
index twice (Column(index=True) plus Index('ix_studio_media_owner')), so every
fresh install and the test conftest failed with "index ix_studio_media_owner
already exists".
"""
import collections

import sqlalchemy as sa

import core.database as database


def test_index_names_are_unique_across_the_schema():
    names = collections.Counter(
        str(index.name)
        for table in database.Base.metadata.sorted_tables
        for index in table.indexes
    )
    assert [name for name, count in names.items() if count > 1] == []


def test_fresh_database_gets_one_studio_media_owner_index():
    engine = sa.create_engine("sqlite://")
    database.Base.metadata.create_all(engine)

    owner_indexes = [
        index["name"]
        for index in sa.inspect(engine).get_indexes("studio_media")
        if index["column_names"] == ["owner"]
    ]
    assert owner_indexes == ["ix_studio_media_owner"]
