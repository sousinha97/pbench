from argparse import Namespace
from logging import Logger
import os
from os import stat_result
from pathlib import Path
from signal import SIGHUP
import time
from typing import Any, Dict, List, Optional

import pytest

from pbench.server import JSONARRAY, JSONOBJECT, JSONVALUE, PbenchServerConfig
from pbench.server.database.models.datasets import (
    Dataset,
    Metadata,
    MetadataBadKey,
    States,
)
from pbench.server.indexing_tarballs import (
    Index,
    SigIntException,
    SigTermException,
    TarballData,
)
from pbench.server.sync import Operation
from pbench.server.templates import TemplateError


class FakeDataset:
    logger: Logger
    new_state: Optional[States] = None
    advance_error: Optional[Exception] = None

    def __init__(self, name: str, resource_id: str):
        self.name = name
        self.resource_id = resource_id
        self.owner_id = 1

    def advance(self, state: States):
        if __class__.advance_error:
            raise __class__.advance_error
        __class__.new_state = state

    def __repr__(self) -> str:
        return self.name

    @classmethod
    def reset(cls):
        cls.new_state = None
        cls.advance_error = None


class FakeMetadata:
    INDEX_MAP = Metadata.INDEX_MAP
    REINDEX = Metadata.REINDEX
    TARBALL_PATH = Metadata.TARBALL_PATH
    INDEX_MAP = Metadata.INDEX_MAP

    no_tarball: list[str] = []
    index_map: dict[str, JSONOBJECT] = {}
    set_values: dict[str, dict[str, Any]] = {}

    @staticmethod
    def getvalue(dataset: FakeDataset, key: str) -> Optional[JSONVALUE]:
        if key == Metadata.TARBALL_PATH:
            if dataset.name in __class__.no_tarball:
                return None
            else:
                return f"{dataset.name}.tar.xz"
        elif key == Metadata.INDEX_MAP:
            return __class__.index_map.get(dataset.name)
        else:
            raise MetadataBadKey(key)

    @staticmethod
    def setvalue(dataset: FakeDataset, key: str, value: JSONVALUE) -> JSONVALUE:
        ds_cur = __class__.set_values.get(dataset.name, {})
        ds_cur[key] = value
        __class__.set_values[dataset.name] = ds_cur
        return value

    @classmethod
    def reset(cls):
        cls.no_tarball = []
        cls.index_map = {}
        cls.set_values = {}


class FakePbenchTemplates:
    templates_updated = False
    failure: Optional[Exception] = None

    def __init__(self, basepath, idx_prefix, logger, known_tool_handlers=None, _dbg=0):
        pass

    def update_templates(self, es_instance):
        __class__.templates_updated = True
        if self.failure:
            raise self.failure

    @classmethod
    def reset(cls):
        cls.templates_updated = False
        cls.failure = None


class FakeReport:
    reported = False
    failure: Optional[Exception] = None

    def __init__(
        self,
        config,
        name,
        es=None,
        pid=None,
        group_id=None,
        user_id=None,
        hostname=None,
        version=None,
        templates=None,
    ):
        self.config = config
        self.name = name

    def post_status(
        self, timestamp: str, doctype: str, file_to_index: Optional[Path] = None
    ) -> str:
        __class__.reported = True
        if self.failure:
            raise self.failure
        return "tracking_id"

    @classmethod
    def reset(cls):
        cls.reported = False
        cls.failure = None


class FakeIdxContext:
    def __init__(self, config: PbenchServerConfig, logger: Logger):
        self.config = config
        self.logger = logger
        self.tracking_id = None
        self.es = None
        self.TS = "FAKE_TS"
        self.templates = FakePbenchTemplates("path", "test", logger)
        self._dbg = False

    def getpid(self) -> int:
        return 1

    def getgid(self) -> int:
        return 1

    def getuid(self) -> int:
        return 1

    def gethostname(self) -> str:
        return "localhost"

    def dump_opctx(self):
        pass

    def set_tracking_id(self, id: str):
        self.tracking_id = id

    def time(self) -> float:
        return time.time()


class FakePbenchTarBall:
    make_tool_called = 0
    make_all_called = 0

    def __init__(
        self,
        idxctx: FakeIdxContext,
        username: str,
        tbarg: str,
        tmpdir: str,
        extracted_root: str,
    ):
        self.idxctx = idxctx
        self.tbname = tbarg
        self.name = Path(tbarg).name
        self.username = username
        self.extracted_root = extracted_root
        self.index_map = {"idx1": ["id1", "id2"]}

    def mk_tool_data_actions(self) -> JSONARRAY:
        __class__.make_tool_called += 1
        return [{"action": "mk_tool_data_actions", "name": self.name}]

    def make_all_actions(self) -> JSONARRAY:
        __class__.make_all_called += 1
        return [{"action": "make_all_actions", "name": self.name}]

    @classmethod
    def reset(cls):
        cls.make_tool_called = 0
        cls.make_all_called = 0


class FakeSync:
    tarballs: Dict[Operation, List[Dataset]] = {}
    called: List[str] = []
    did: Optional[Operation] = None
    updated: Optional[List[Operation]] = None
    errors: JSONOBJECT = {}

    @classmethod
    def reset(cls):
        cls.tarballs = {}
        cls.called = []
        cls.did = None
        cls.updated = None
        cls.errors = {}

    def __init__(self, logger: Logger, component: str):
        self.logger = logger
        self.component = component

    def next(self, operation: Operation) -> List[Dataset]:
        __class__.called.append(f"next-{operation.name}")
        assert operation in __class__.tarballs
        return __class__.tarballs[operation]

    def update(
        self,
        dataset: Dataset,
        did: Optional[Operation],
        enabled: Optional[List[Operation]],
    ):
        __class__.did = did
        __class__.updated = enabled

    def error(self, dataset: Dataset, message: str):
        __class__.errors[dataset.name] = message


class FakeController:
    def __init__(self, path: Path, incoming: Path, results: Path, logger: Logger):
        self.name = path.name
        self.path = path
        self.incoming = incoming / self.name
        self.results = results / self.name
        self.logger = logger


class FakeTarball:
    def __init__(self, path: Path, controller: FakeController):
        self.name = path.name
        self.tarball_path = path
        self.controller = controller
        self.unpacked = f"/incoming/{path.name}"


class FakeCacheManager:
    def __init__(self, config: PbenchServerConfig, logger: Logger):
        self.config = config
        self.logger = logger
        self.datasets = {}

    def find_dataset(self, resource_id: str):
        controller = FakeController(
            Path("/archive/ctrl"), Path("/incoming"), Path("/results"), self.logger
        )
        return FakeTarball(
            Path(f"/archive/ctrl/tarball-{resource_id}.tar.xz"), controller
        )


@pytest.fixture()
def mocks(monkeypatch, make_logger):
    FakeDataset.logger = make_logger
    with monkeypatch.context() as m:
        m.setattr("pbench.server.indexing_tarballs.Sync", FakeSync)
        m.setattr("pbench.server.indexing_tarballs.PbenchTarBall", FakePbenchTarBall)
        m.setattr("pbench.server.indexing_tarballs.Report", FakeReport)
        m.setattr("pbench.server.indexing_tarballs.Dataset", FakeDataset)
        m.setattr("pbench.server.indexing_tarballs.Metadata", FakeMetadata)
        m.setattr("pbench.server.indexing_tarballs.CacheManager", FakeCacheManager)
        yield m
    FakeDataset.reset()
    FakeMetadata.reset()
    FakePbenchTemplates.reset()
    FakeReport.reset()
    FakeSync.reset()
    FakePbenchTarBall.reset()


@pytest.fixture()
def index(server_config, make_logger):
    return Index(
        "test",
        Namespace(index_tool_data=False, re_index=False),
        FakeIdxContext(server_config, make_logger),
    )


sizes = {"ds1": 5, "ds2": 2, "ds3": 20}
ds1 = FakeDataset(name="ds1", resource_id="ABC")
ds2 = FakeDataset(name="ds2", resource_id="ACDF")
ds3 = FakeDataset(name="ds3", resource_id="GHIJ")
tarball_1 = TarballData(
    dataset=ds1,
    size=sizes["ds1"],
    tarball=f"{ds1.name}.tar.xz",
)
tarball_2 = TarballData(
    dataset=ds2,
    size=sizes["ds2"],
    tarball=f"{ds2.name}.tar.xz",
)
tarball_3 = TarballData(
    dataset=ds3,
    size=sizes["ds3"],
    tarball=f"{ds3.name}.tar.xz",
)


class TestIndexingTarballs:

    stat_failure: dict[str, Exception] = {}

    @staticmethod
    def mock_stat(file: str) -> stat_result:
        name = Dataset.stem(file)
        if name in __class__.stat_failure:
            raise __class__.stat_failure[name]
        return stat_result([0o777, 123, 300, 1, 100, 100, sizes[name], 0, 0, 0])

    def test_load_templates(self, mocks, index):
        error = index.load_templates()
        assert error == index.error_code["OK"]
        assert FakePbenchTemplates.templates_updated
        assert FakeReport.reported

    def test_load_templates_error(self, mocks, index):
        FakePbenchTemplates.failure = TemplateError("erroneous")
        error = index.load_templates()
        assert error == index.error_code["TEMPLATE_CREATION_ERROR"]
        assert FakePbenchTemplates.templates_updated
        FakePbenchTemplates.reset()

        FakePbenchTemplates.failure = Exception("erroneous")
        error = index.load_templates()
        assert error == index.error_code["GENERIC_ERROR"]
        FakePbenchTemplates.reset()

        FakePbenchTemplates.failure = SigTermException("abort! abort!")
        with pytest.raises(SigTermException):
            index.load_templates()
        assert not FakeReport.reported

    def test_load_templates_report_err(self, mocks, index):
        FakeReport.failure = Exception("I'm a teapot")
        error = index.load_templates()
        assert error == index.error_code["GENERIC_ERROR"]
        assert FakePbenchTemplates.templates_updated
        assert FakeReport.reported

    def test_load_templates_report_abort(self, mocks, index):
        FakeReport.failure = SigTermException("done here")
        with pytest.raises(SigTermException):
            index.load_templates()
        assert FakeReport.reported

    def test_collect_tb_empty(self, mocks, index):
        FakeSync.tarballs[Operation.INDEX] = []
        tb_list = index.collect_tb()
        assert FakeSync.called == ["next-INDEX"]
        assert tb_list == (0, [])

    def test_collect_tb_missing_tb(self, mocks, index):
        mocks.setattr("pbench.server.indexing_tarballs.os.stat", __class__.mock_stat)
        FakeSync.tarballs[Operation.INDEX] = [ds1, ds2]
        FakeMetadata.no_tarball = ["ds2"]
        tb_list = index.collect_tb()
        assert FakeSync.called == ["next-INDEX"]
        assert FakeSync.errors["ds2"] == "ds2 does not have a tarball-path"
        assert tb_list == (0, [tarball_1])

    def test_collect_tb_fail(self, mocks, index):
        mocks.setattr("pbench.server.indexing_tarballs.os.stat", __class__.mock_stat)
        __class__.stat_failure = {"ds1": OSError("something wicked that way goes")}
        FakeSync.tarballs[Operation.INDEX] = [ds1, ds2]
        tb_list = index.collect_tb()
        assert FakeSync.called == ["next-INDEX"]
        assert tb_list == (0, [tarball_2])
        __class__.stat_failure = {}

    def test_collect_tb_exception(self, mocks, index):
        mocks.setattr("pbench.server.indexing_tarballs.os.stat", __class__.mock_stat)
        __class__.stat_failure = {"ds1": Exception("the greater of two weevils")}
        FakeSync.tarballs[Operation.INDEX] = [ds1, ds2]
        tb_list = index.collect_tb()
        assert FakeSync.called == ["next-INDEX"]
        assert tb_list == (12, [])
        __class__.stat_failure = {}

    def test_collect_tb(self, mocks, index):
        mocks.setattr("pbench.server.indexing_tarballs.os.stat", self.mock_stat)
        FakeSync.tarballs[Operation.INDEX] = [ds1, ds2]
        tb_list = index.collect_tb()
        assert FakeSync.called == ["next-INDEX"]
        assert tb_list == (0, [tarball_2, tarball_1])

    def test_process_tb_none(self, mocks, index):
        stat = index.process_tb(tarballs=[])
        assert (
            stat == 0
            and not FakePbenchTarBall.make_all_called
            and not FakePbenchTarBall.make_tool_called
        )

    def test_process_tb_bad_load(self, mocks, index):
        FakePbenchTemplates.failure = Exception("I think I can't")
        stat = index.process_tb(tarballs=[])
        assert stat == 12
        assert FakePbenchTemplates.templates_updated

    def test_process_tb_term(self, mocks, index):
        def fake_es_index(es, actions, errorsfp, logger, _dbg=0):
            raise SigTermException("ter-min-ate; ter-min-ate")

        mocks.setattr("pbench.server.indexing_tarballs.es_index", fake_es_index)
        stat = index.process_tb(tarballs=[tarball_2, tarball_1])
        assert stat == 0

    def test_process_tb_interrupt(self, mocks, index):
        def fake_es_index(es, actions, errorsfp, logger, _dbg=0):
            raise SigIntException("cease. also desist. and stop. that too.")

        mocks.setattr("pbench.server.indexing_tarballs.es_index", fake_es_index)
        stat = index.process_tb(tarballs=[tarball_2, tarball_1])
        assert stat == 0

    def test_process_tb_int(self, mocks, index):
        """Test behavior when a SIGHUP occurs during processing.

        This should trigger the indexer to re-evaluate the set of enabled
        datasets after completing the current dataset. Because the mock
        here generates a SIGHUP on the first index operation any additional
        enabled datasets would be missed unless they're still enabled and
        are reported by the subsequent collect_tb() call. The verified action
        sequence here confirms that the second (skipped) dataset in the
        initial parameter list (tarball_1) is only indexed once despite also
        appearing in the SIGHUP collect_tb.
        """
        index_actions = []
        first_index = True

        def fake_es_index(es, actions, errorsfp, logger, _dbg=0):
            nonlocal first_index
            if first_index:
                first_index = False
                os.kill(os.getpid(), SIGHUP)
            index_actions.append(actions)
            return (1000, 2000, 1, 0, 0, 0)

        mocks.setattr("pbench.server.indexing_tarballs.es_index", fake_es_index)
        mocks.setattr(Index, "collect_tb", lambda self: (0, [tarball_1, tarball_3]))
        stat = index.process_tb(tarballs=[tarball_2, tarball_1])
        assert (
            stat == 0
            and FakePbenchTarBall.make_all_called == 3
            and not FakePbenchTarBall.make_tool_called
        )
        assert index_actions == [
            [{"action": "make_all_actions", "name": f"{ds2.name}.tar.xz"}],
            [{"action": "make_all_actions", "name": f"{ds1.name}.tar.xz"}],
            [{"action": "make_all_actions", "name": f"{ds3.name}.tar.xz"}],
        ]

    def test_process_tb_merge(self, mocks, index):
        def fake_es_index(es, actions, errorsfp, logger, _dbg=0):
            return (1000, 2000, 1, 0, 0, 0)

        FakeMetadata.index_map = {"ds1": {"idx": ["a", "b"]}}
        mocks.setattr("pbench.server.indexing_tarballs.es_index", fake_es_index)
        stat = index.process_tb(tarballs=[tarball_1])
        assert (
            stat == 0
            and FakePbenchTarBall.make_all_called == 1
            and not FakePbenchTarBall.make_tool_called
        )
        assert FakeMetadata.set_values == {
            "ds1": {
                Metadata.REINDEX: False,
                Metadata.INDEX_MAP: {"idx": ["a", "b"], "idx1": ["id1", "id2"]},
            }
        }

    def test_process_tb(self, mocks, index):
        index_actions = []

        def fake_es_index(es, actions, errorsfp, logger, _dbg=0):
            index_actions.append(actions)
            return (1000, 2000, 1, 0, 0, 0)

        mocks.setattr("pbench.server.indexing_tarballs.es_index", fake_es_index)
        stat = index.process_tb(tarballs=[tarball_2, tarball_1])
        assert (
            stat == 0
            and FakePbenchTarBall.make_all_called == 2
            and not FakePbenchTarBall.make_tool_called
        )
        assert index_actions == [
            [{"action": "make_all_actions", "name": f"{ds2.name}.tar.xz"}],
            [{"action": "make_all_actions", "name": f"{ds1.name}.tar.xz"}],
        ]
