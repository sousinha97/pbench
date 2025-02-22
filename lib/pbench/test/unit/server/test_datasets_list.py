import datetime
from http import HTTPStatus
from typing import List
from urllib.parse import urlencode

import pytest
import requests

from pbench.server import JSON
from pbench.server.database.models.datasets import Dataset


class TestDatasetsList:
    """
    Test the `datasets/list` API. We perform a variety of queries using a
    set of datasets provided by the `attach_dataset` fixture and the
    `more_datasets` fixture.
    """

    @pytest.fixture()
    def query_as(
        self, client, server_config, more_datasets, provide_metadata, get_token
    ):
        """
        Helper fixture to perform the API query and validate an expected
        return status.

        Args:
            client: Flask test API client fixture
            server_config: Pbench config fixture
            more_datasets: Dataset construction fixture
            provide_metadata: Dataset metadata fixture
            get_token: Pbench token fixture
        """

        def query_api(
            payload: JSON, username: str, expected_status: HTTPStatus
        ) -> requests.Response:
            """
            Encapsulate an HTTP GET operation with proper authentication, and
            check the return status.

            Args:
                payload:            Query parameter dict
                username:           Username to authenticate (None to skip
                                    authentication)
                expected_status:    Expected HTTP status

            Return:
                HTTP Response object
            """
            headers = None
            if username:
                token = get_token(username)
                headers = {"authorization": f"bearer {token}"}
            response = client.get(
                f"{server_config.rest_uri}/datasets/list",
                headers=headers,
                query_string=payload,
            )
            assert response.status_code == expected_status
            return response

        return query_api

    def get_results(self, name_list: List[str], query: JSON, server_config) -> JSON:
        """
        Translate a list of names into a list of expected results of the
        abbreviated form returned by `datasets/list`: name, controller,
        run_id, and metadata.

        Args:
            name_list: List of dataset names
            query: A JSON representation of the query parameters
            server_config: Pbench config fixture

        Returns:
            Paginated JSON object containing list of dataset values
        """
        list: List[JSON] = []
        offset = query.get("offset", 0)
        limit = query.get("limit")

        if limit:
            next_offset = offset + limit
            paginated_name_list = name_list[offset:next_offset]
            if next_offset >= len(name_list):
                next_url = ""
            else:
                query["offset"] = next_offset
                next_url = (
                    f"http://localhost{server_config.rest_uri}/datasets/list?"
                    + urlencode(query)
                )
        else:
            paginated_name_list = name_list[offset:]
            next_url = ""

        for name in sorted(paginated_name_list):
            dataset = Dataset.query(name=name)
            list.append(
                {
                    "name": dataset.name,
                    "resource_id": dataset.resource_id,
                    "metadata": {
                        "dataset.created": datetime.datetime.isoformat(dataset.created)
                    },
                }
            )
        return {"next_url": next_url, "results": list, "total": len(name_list)}

    @pytest.mark.parametrize(
        "login,query,results",
        [
            ("drb", {"name": "fio"}, ["fio_1", "fio_2"]),
            ("drb", {"name": "fio", "limit": 1}, ["fio_1", "fio_2"]),
            ("drb", {"name": "fio", "limit": 1, "offset": 2}, ["fio_1", "fio_2"]),
            ("drb", {"name": "fio", "offset": 1}, ["fio_1", "fio_2"]),
            ("drb", {"name": "fio", "offset": 2}, ["fio_1", "fio_2"]),
            ("drb", {"owner": "drb"}, ["drb", "fio_1"]),
            ("drb", {"name": "drb"}, ["drb"]),
            ("test", {"name": "drb"}, []),
            ("test_admin", {"name": "drb"}, ["drb"]),
            ("drb", {}, ["drb", "fio_1", "fio_2"]),
            ("test", {}, ["test", "fio_1", "fio_2"]),
            ("test_admin", {}, ["drb", "test", "fio_1", "fio_2"]),
            ("drb", {"start": "2000-01-01", "end": "2005-12-31"}, ["fio_2"]),
            ("drb", {"start": "2005-01-01"}, ["drb", "fio_1"]),
            ("drb", {"end": "2020-09-01"}, ["drb", "fio_1", "fio_2"]),
            ("drb", {"end": "1970-09-01"}, []),
        ],
    )
    def test_dataset_list(self, query_as, login, query, results, server_config):
        """
        Test the operation of `datasets/list` against our set of test
        datasets.

        Args:
            query_as: A fixture to provide a helper that executes the API call
            login: The username as which to perform a query
            query: A JSON representation of the query parameters (these will be
                automatically supplemented with a metadata request term)
            results: A list of the dataset names we expect to be returned
        """
        query.update({"metadata": ["dataset.created"]})
        result = query_as(query, login, HTTPStatus.OK)
        assert result.json == self.get_results(results, query, server_config)

    def test_unauth_dataset_list(self, query_as):
        """
        Test the operation of `datasets/list` when the client doesn't have
        access to all of the requested datasets.

        Args:
            query_as: A fixture to provide a helper that executes the API call
            login: The username as which to perform a query
            query: A JSON representation of the query parameters (these will be
                automatically supplemented with a metadata request term)
            results: A list of the dataset names we expect to be returned
        """
        query_as({"access": "private"}, None, HTTPStatus.UNAUTHORIZED)

    def test_get_bad_keys(self, query_as):
        """
        Test case requesting non-existent metadata keys.

        Args:
            query_as: Query helper fixture
        """
        response = query_as(
            {"metadata": "xyzzy,plugh,dataset.owner,dataset.access"},
            "drb",
            HTTPStatus.BAD_REQUEST,
        )
        assert response.json == {
            "message": "Unrecognized list values ['plugh', 'xyzzy'] given for parameter metadata; expected ['dataset', 'global', 'server', 'user']"
        }

    def test_get_unknown_keys(self, query_as):
        """
        Test case requesting non-existent query parameter keys.

        Args:
            query_as: Query helper fixture
        """
        response = query_as(
            {"plugh": "xyzzy", "passages": "twisty"},
            "drb",
            HTTPStatus.BAD_REQUEST,
        )
        assert response.json == {"message": "Unknown URL query keys: passages,plugh"}

    def test_get_repeat_keys(self, query_as):
        """
        Test case requesting repeated single-value metadata keys.

        NOTE that the request package processes a list of values for a query
        parameter by repeating the key name with each value since the HTTP
        standard doesn't cover multiple values for a single key; so
        "name": ["one", "two"] will appear to the API as "?name=one&name=two".

        Args:
            query_as: Query helper fixture
        """
        response = query_as(
            {"name": ["one", "two"]},
            "drb",
            HTTPStatus.BAD_REQUEST,
        )
        assert response.json == {"message": "Repeated URL query key 'name'"}
