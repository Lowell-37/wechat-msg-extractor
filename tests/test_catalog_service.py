from pathlib import Path

from openpyxl import Workbook

from schemas.catalog import CatalogDependencies
from schemas.wizard import WizardState
from services.catalog import filter_catalog, load_catalog


class FakeDatabase:
    def __init__(self):
        self.query_count = 0


def test_filter_catalog_reuses_loaded_catalog_without_requerying_database(tmp_path: Path):
    template_path = tmp_path / "template.xlsx"
    workbook = Workbook()
    workbook.active.title = "项目群"
    workbook.save(template_path)
    workbook.close()

    database = FakeDatabase()

    def query_chatrooms(queried_database):
        assert queried_database is database
        queried_database.query_count += 1
        return [("room-1", "项目群"), ("room-2", "运营群")]

    state = {
        "database": database,
        "wizard": WizardState(),
        "catalog_dependencies": CatalogDependencies(
            query_chatrooms=query_chatrooms,
            database_key="database",
        ),
    }

    first = load_catalog(state, str(template_path))
    second = filter_catalog(state, "项目")

    assert database.query_count == 1
    assert second == [first[0]]
    assert first[0].suggested_sheet == "项目群"


def test_catalog_records_are_cached_as_an_immutable_tuple(tmp_path: Path):
    template_path = tmp_path / "template.xlsx"
    workbook = Workbook()
    workbook.active.title = "项目群"
    workbook.save(template_path)
    workbook.close()

    state = {
        "database": FakeDatabase(),
        "catalog_dependencies": CatalogDependencies(
            query_chatrooms=lambda _: [("room-1", "项目群")],
            database_key="database",
        ),
    }

    result = load_catalog(state, str(template_path))

    assert isinstance(state["chatroom_catalog"], tuple)
    assert result is state["chatroom_catalog"]
