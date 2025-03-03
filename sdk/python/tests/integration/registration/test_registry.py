# Copyright 2021 The Feast Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import time
from datetime import timedelta
from tempfile import mkstemp

import pandas as pd
import pytest
from pytest_lazyfixture import lazy_fixture

from feast import FileSource
from feast.aggregation import Aggregation
from feast.data_format import AvroFormat, ParquetFormat
from feast.data_source import KafkaSource
from feast.entity import Entity
from feast.feature import Feature
from feast.feature_view import FeatureView
from feast.field import Field
from feast.on_demand_feature_view import RequestSource, on_demand_feature_view
from feast.registry import Registry
from feast.repo_config import RegistryConfig
from feast.stream_feature_view import StreamFeatureView
from feast.types import Array, Bytes, Float32, Int32, Int64, String
from feast.value_type import ValueType


@pytest.fixture
def local_registry() -> Registry:
    fd, registry_path = mkstemp()
    registry_config = RegistryConfig(path=registry_path, cache_ttl_seconds=600)
    return Registry(registry_config, None)


@pytest.fixture
def gcs_registry() -> Registry:
    from google.cloud import storage

    storage_client = storage.Client()
    bucket_name = f"feast-registry-test-{int(time.time() * 1000)}"
    bucket = storage_client.bucket(bucket_name)
    bucket = storage_client.create_bucket(bucket)
    bucket.add_lifecycle_delete_rule(
        age=14
    )  # delete buckets automatically after 14 days
    bucket.patch()
    bucket.blob("registry.db")
    registry_config = RegistryConfig(
        path=f"gs://{bucket_name}/registry.db", cache_ttl_seconds=600
    )
    return Registry(registry_config, None)


@pytest.fixture
def s3_registry() -> Registry:
    registry_config = RegistryConfig(
        path=f"s3://feast-integration-tests/registries/{int(time.time() * 1000)}/registry.db",
        cache_ttl_seconds=600,
    )
    return Registry(registry_config, None)


@pytest.mark.parametrize(
    "test_registry", [lazy_fixture("local_registry")],
)
def test_apply_entity_success(test_registry):
    entity = Entity(
        name="driver_car_id", description="Car driver id", tags={"team": "matchmaking"},
    )

    project = "project"

    # Register Entity
    test_registry.apply_entity(entity, project)

    entities = test_registry.list_entities(project)

    entity = entities[0]
    assert (
        len(entities) == 1
        and entity.name == "driver_car_id"
        and entity.description == "Car driver id"
        and "team" in entity.tags
        and entity.tags["team"] == "matchmaking"
    )

    entity = test_registry.get_entity("driver_car_id", project)
    assert (
        entity.name == "driver_car_id"
        and entity.description == "Car driver id"
        and "team" in entity.tags
        and entity.tags["team"] == "matchmaking"
    )

    test_registry.delete_entity("driver_car_id", project)
    entities = test_registry.list_entities(project)
    assert len(entities) == 0

    test_registry.teardown()

    # Will try to reload registry, which will fail because the file has been deleted
    with pytest.raises(FileNotFoundError):
        test_registry._get_registry_proto()


@pytest.mark.integration
@pytest.mark.parametrize(
    "test_registry", [lazy_fixture("gcs_registry"), lazy_fixture("s3_registry")],
)
def test_apply_entity_integration(test_registry):
    entity = Entity(
        name="driver_car_id", description="Car driver id", tags={"team": "matchmaking"},
    )

    project = "project"

    # Register Entity
    test_registry.apply_entity(entity, project)

    entities = test_registry.list_entities(project)

    entity = entities[0]
    assert (
        len(entities) == 1
        and entity.name == "driver_car_id"
        and entity.description == "Car driver id"
        and "team" in entity.tags
        and entity.tags["team"] == "matchmaking"
    )

    entity = test_registry.get_entity("driver_car_id", project)
    assert (
        entity.name == "driver_car_id"
        and entity.description == "Car driver id"
        and "team" in entity.tags
        and entity.tags["team"] == "matchmaking"
    )

    test_registry.teardown()

    # Will try to reload registry, which will fail because the file has been deleted
    with pytest.raises(FileNotFoundError):
        test_registry._get_registry_proto()


@pytest.mark.parametrize(
    "test_registry", [lazy_fixture("local_registry")],
)
def test_apply_feature_view_success(test_registry):
    # Create Feature Views
    batch_source = FileSource(
        file_format=ParquetFormat(),
        path="file://feast/*",
        timestamp_field="ts_col",
        created_timestamp_column="timestamp",
    )

    entity = Entity(name="fs1_my_entity_1", join_keys=["test"])

    fv1 = FeatureView(
        name="my_feature_view_1",
        schema=[
            Field(name="fs1_my_feature_1", dtype=Int64),
            Field(name="fs1_my_feature_2", dtype=String),
            Field(name="fs1_my_feature_3", dtype=Array(String)),
            Field(name="fs1_my_feature_4", dtype=Array(Bytes)),
        ],
        entities=[entity],
        tags={"team": "matchmaking"},
        batch_source=batch_source,
        ttl=timedelta(minutes=5),
    )

    project = "project"

    # Register Feature View
    test_registry.apply_feature_view(fv1, project)

    feature_views = test_registry.list_feature_views(project)

    # List Feature Views
    assert (
        len(feature_views) == 1
        and feature_views[0].name == "my_feature_view_1"
        and feature_views[0].features[0].name == "fs1_my_feature_1"
        and feature_views[0].features[0].dtype == Int64
        and feature_views[0].features[1].name == "fs1_my_feature_2"
        and feature_views[0].features[1].dtype == String
        and feature_views[0].features[2].name == "fs1_my_feature_3"
        and feature_views[0].features[2].dtype == Array(String)
        and feature_views[0].features[3].name == "fs1_my_feature_4"
        and feature_views[0].features[3].dtype == Array(Bytes)
        and feature_views[0].entities[0] == "fs1_my_entity_1"
    )

    feature_view = test_registry.get_feature_view("my_feature_view_1", project)
    assert (
        feature_view.name == "my_feature_view_1"
        and feature_view.features[0].name == "fs1_my_feature_1"
        and feature_view.features[0].dtype == Int64
        and feature_view.features[1].name == "fs1_my_feature_2"
        and feature_view.features[1].dtype == String
        and feature_view.features[2].name == "fs1_my_feature_3"
        and feature_view.features[2].dtype == Array(String)
        and feature_view.features[3].name == "fs1_my_feature_4"
        and feature_view.features[3].dtype == Array(Bytes)
        and feature_view.entities[0] == "fs1_my_entity_1"
    )

    test_registry.delete_feature_view("my_feature_view_1", project)
    feature_views = test_registry.list_feature_views(project)
    assert len(feature_views) == 0

    test_registry.teardown()

    # Will try to reload registry, which will fail because the file has been deleted
    with pytest.raises(FileNotFoundError):
        test_registry._get_registry_proto()


@pytest.mark.parametrize(
    "test_registry", [lazy_fixture("local_registry")],
)
def test_apply_on_demand_feature_view_success(test_registry):
    # Create Feature Views
    driver_stats = FileSource(
        name="driver_stats_source",
        path="data/driver_stats_lat_lon.parquet",
        timestamp_field="event_timestamp",
        created_timestamp_column="created",
        description="A table describing the stats of a driver based on hourly logs",
        owner="test2@gmail.com",
    )

    driver_daily_features_view = FeatureView(
        name="driver_daily_features",
        entities=["driver"],
        ttl=timedelta(seconds=8640000000),
        schema=[
            Field(name="daily_miles_driven", dtype=Float32),
            Field(name="lat", dtype=Float32),
            Field(name="lon", dtype=Float32),
            Field(name="string_feature", dtype=String),
        ],
        online=True,
        source=driver_stats,
        tags={"production": "True"},
        owner="test2@gmail.com",
    )

    @on_demand_feature_view(
        sources=[driver_daily_features_view],
        schema=[Field(name="first_char", dtype=String)],
    )
    def location_features_from_push(inputs: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame()
        df["first_char"] = inputs["string_feature"].str[:1].astype("string")
        return df

    project = "project"

    # Register Feature View
    test_registry.apply_feature_view(location_features_from_push, project)

    feature_views = test_registry.list_on_demand_feature_views(project)

    # List Feature Views
    assert (
        len(feature_views) == 1
        and feature_views[0].name == "location_features_from_push"
        and feature_views[0].features[0].name == "first_char"
        and feature_views[0].features[0].dtype == String
    )

    feature_view = test_registry.get_on_demand_feature_view(
        "location_features_from_push", project
    )
    assert (
        feature_view.name == "location_features_from_push"
        and feature_view.features[0].name == "first_char"
        and feature_view.features[0].dtype == String
    )

    test_registry.delete_feature_view("location_features_from_push", project)
    feature_views = test_registry.list_on_demand_feature_views(project)
    assert len(feature_views) == 0

    test_registry.teardown()

    # Will try to reload registry, which will fail because the file has been deleted
    with pytest.raises(FileNotFoundError):
        test_registry._get_registry_proto()


@pytest.mark.parametrize(
    "test_registry", [lazy_fixture("local_registry")],
)
def test_apply_stream_feature_view_success(test_registry):
    # Create Feature Views
    def simple_udf(x: int):
        return x + 3

    entity = Entity(name="driver_entity", join_keys=["test_key"])

    stream_source = KafkaSource(
        name="kafka",
        timestamp_field="event_timestamp",
        bootstrap_servers="",
        message_format=AvroFormat(""),
        topic="topic",
        batch_source=FileSource(path="some path"),
    )

    sfv = StreamFeatureView(
        name="test kafka stream feature view",
        entities=[entity],
        ttl=timedelta(days=30),
        owner="test@example.com",
        online=True,
        schema=[Field(name="dummy_field", dtype=Float32)],
        description="desc",
        aggregations=[
            Aggregation(
                column="dummy_field", function="max", time_window=timedelta(days=1),
            ),
            Aggregation(
                column="dummy_field2", function="count", time_window=timedelta(days=24),
            ),
        ],
        timestamp_field="event_timestamp",
        mode="spark",
        source=stream_source,
        udf=simple_udf,
        tags={},
    )

    project = "project"

    # Register Feature View
    test_registry.apply_feature_view(sfv, project)

    stream_feature_views = test_registry.list_stream_feature_views(project)

    # List Feature Views
    assert len(stream_feature_views) == 1
    assert stream_feature_views[0] == sfv

    test_registry.delete_feature_view("test kafka stream feature view", project)
    stream_feature_views = test_registry.list_stream_feature_views(project)
    assert len(stream_feature_views) == 0

    test_registry.teardown()

    # Will try to reload registry, which will fail because the file has been deleted
    with pytest.raises(FileNotFoundError):
        test_registry._get_registry_proto()


@pytest.mark.parametrize(
    "test_registry", [lazy_fixture("local_registry")],
)
# TODO(kevjumba): remove this in feast 0.23 when deprecating
@pytest.mark.parametrize(
    "request_source_schema",
    [[Field(name="my_input_1", dtype=Int32)], {"my_input_1": ValueType.INT32}],
)
def test_modify_feature_views_success(test_registry, request_source_schema):
    # Create Feature Views
    batch_source = FileSource(
        file_format=ParquetFormat(),
        path="file://feast/*",
        timestamp_field="ts_col",
        created_timestamp_column="timestamp",
    )

    request_source = RequestSource(name="request_source", schema=request_source_schema,)

    entity = Entity(name="fs1_my_entity_1", join_keys=["test"])

    fv1 = FeatureView(
        name="my_feature_view_1",
        schema=[Field(name="fs1_my_feature_1", dtype=Int64)],
        entities=[entity],
        tags={"team": "matchmaking"},
        batch_source=batch_source,
        ttl=timedelta(minutes=5),
    )

    @on_demand_feature_view(
        features=[
            Feature(name="odfv1_my_feature_1", dtype=ValueType.STRING),
            Feature(name="odfv1_my_feature_2", dtype=ValueType.INT32),
        ],
        sources=[request_source],
    )
    def odfv1(feature_df: pd.DataFrame) -> pd.DataFrame:
        data = pd.DataFrame()
        data["odfv1_my_feature_1"] = feature_df["my_input_1"].astype("category")
        data["odfv1_my_feature_2"] = feature_df["my_input_1"].astype("int32")
        return data

    project = "project"

    # Register Feature Views
    test_registry.apply_feature_view(odfv1, project)
    test_registry.apply_feature_view(fv1, project)

    # Modify odfv by changing a single feature dtype
    @on_demand_feature_view(
        features=[
            Feature(name="odfv1_my_feature_1", dtype=ValueType.FLOAT),
            Feature(name="odfv1_my_feature_2", dtype=ValueType.INT32),
        ],
        sources=[request_source],
    )
    def odfv1(feature_df: pd.DataFrame) -> pd.DataFrame:
        data = pd.DataFrame()
        data["odfv1_my_feature_1"] = feature_df["my_input_1"].astype("float")
        data["odfv1_my_feature_2"] = feature_df["my_input_1"].astype("int32")
        return data

    # Apply the modified odfv
    test_registry.apply_feature_view(odfv1, project)

    # Check odfv
    on_demand_feature_views = test_registry.list_on_demand_feature_views(project)

    assert (
        len(on_demand_feature_views) == 1
        and on_demand_feature_views[0].name == "odfv1"
        and on_demand_feature_views[0].features[0].name == "odfv1_my_feature_1"
        and on_demand_feature_views[0].features[0].dtype == Float32
        and on_demand_feature_views[0].features[1].name == "odfv1_my_feature_2"
        and on_demand_feature_views[0].features[1].dtype == Int32
    )
    request_schema = on_demand_feature_views[0].get_request_data_schema()
    assert (
        list(request_schema.keys())[0] == "my_input_1"
        and list(request_schema.values())[0] == ValueType.INT32
    )

    feature_view = test_registry.get_on_demand_feature_view("odfv1", project)
    assert (
        feature_view.name == "odfv1"
        and feature_view.features[0].name == "odfv1_my_feature_1"
        and feature_view.features[0].dtype == Float32
        and feature_view.features[1].name == "odfv1_my_feature_2"
        and feature_view.features[1].dtype == Int32
    )
    request_schema = feature_view.get_request_data_schema()
    assert (
        list(request_schema.keys())[0] == "my_input_1"
        and list(request_schema.values())[0] == ValueType.INT32
    )

    # Make sure fv1 is untouched
    feature_views = test_registry.list_feature_views(project)

    # List Feature Views
    assert (
        len(feature_views) == 1
        and feature_views[0].name == "my_feature_view_1"
        and feature_views[0].features[0].name == "fs1_my_feature_1"
        and feature_views[0].features[0].dtype == Int64
        and feature_views[0].entities[0] == "fs1_my_entity_1"
    )

    feature_view = test_registry.get_feature_view("my_feature_view_1", project)
    assert (
        feature_view.name == "my_feature_view_1"
        and feature_view.features[0].name == "fs1_my_feature_1"
        and feature_view.features[0].dtype == Int64
        and feature_view.entities[0] == "fs1_my_entity_1"
    )

    test_registry.teardown()

    # Will try to reload registry, which will fail because the file has been deleted
    with pytest.raises(FileNotFoundError):
        test_registry._get_registry_proto()


@pytest.mark.integration
@pytest.mark.parametrize(
    "test_registry", [lazy_fixture("gcs_registry"), lazy_fixture("s3_registry")],
)
def test_apply_feature_view_integration(test_registry):
    # Create Feature Views
    batch_source = FileSource(
        file_format=ParquetFormat(),
        path="file://feast/*",
        timestamp_field="ts_col",
        created_timestamp_column="timestamp",
    )

    entity = Entity(name="fs1_my_entity_1", join_keys=["test"])

    fv1 = FeatureView(
        name="my_feature_view_1",
        schema=[
            Field(name="fs1_my_feature_1", dtype=Int64),
            Field(name="fs1_my_feature_2", dtype=String),
            Field(name="fs1_my_feature_3", dtype=Array(String)),
            Field(name="fs1_my_feature_4", dtype=Array(Bytes)),
        ],
        entities=[entity],
        tags={"team": "matchmaking"},
        batch_source=batch_source,
        ttl=timedelta(minutes=5),
    )

    project = "project"

    # Register Feature View
    test_registry.apply_feature_view(fv1, project)

    feature_views = test_registry.list_feature_views(project)

    # List Feature Views
    assert (
        len(feature_views) == 1
        and feature_views[0].name == "my_feature_view_1"
        and feature_views[0].features[0].name == "fs1_my_feature_1"
        and feature_views[0].features[0].dtype == Int64
        and feature_views[0].features[1].name == "fs1_my_feature_2"
        and feature_views[0].features[1].dtype == String
        and feature_views[0].features[2].name == "fs1_my_feature_3"
        and feature_views[0].features[2].dtype == Array(String)
        and feature_views[0].features[3].name == "fs1_my_feature_4"
        and feature_views[0].features[3].dtype == Array(Bytes)
        and feature_views[0].entities[0] == "fs1_my_entity_1"
    )

    feature_view = test_registry.get_feature_view("my_feature_view_1", project)
    assert (
        feature_view.name == "my_feature_view_1"
        and feature_view.features[0].name == "fs1_my_feature_1"
        and feature_view.features[0].dtype == Int64
        and feature_view.features[1].name == "fs1_my_feature_2"
        and feature_view.features[1].dtype == String
        and feature_view.features[2].name == "fs1_my_feature_3"
        and feature_view.features[2].dtype == Array(String)
        and feature_view.features[3].name == "fs1_my_feature_4"
        and feature_view.features[3].dtype == Array(Bytes)
        and feature_view.entities[0] == "fs1_my_entity_1"
    )

    test_registry.delete_feature_view("my_feature_view_1", project)
    feature_views = test_registry.list_feature_views(project)
    assert len(feature_views) == 0

    test_registry.teardown()

    # Will try to reload registry, which will fail because the file has been deleted
    with pytest.raises(FileNotFoundError):
        test_registry._get_registry_proto()


@pytest.mark.integration
@pytest.mark.parametrize(
    "test_registry", [lazy_fixture("gcs_registry"), lazy_fixture("s3_registry")],
)
def test_apply_data_source(test_registry: Registry):
    # Create Feature Views
    batch_source = FileSource(
        name="test_source",
        file_format=ParquetFormat(),
        path="file://feast/*",
        timestamp_field="ts_col",
        created_timestamp_column="timestamp",
    )

    entity = Entity(name="fs1_my_entity_1", join_keys=["test"])

    fv1 = FeatureView(
        name="my_feature_view_1",
        schema=[
            Field(name="fs1_my_feature_1", dtype=Int64),
            Field(name="fs1_my_feature_2", dtype=String),
            Field(name="fs1_my_feature_3", dtype=Array(String)),
            Field(name="fs1_my_feature_4", dtype=Array(Bytes)),
        ],
        entities=[entity],
        tags={"team": "matchmaking"},
        batch_source=batch_source,
        ttl=timedelta(minutes=5),
    )

    project = "project"

    # Register data source and feature view
    test_registry.apply_data_source(batch_source, project, commit=False)
    test_registry.apply_feature_view(fv1, project, commit=True)

    registry_feature_views = test_registry.list_feature_views(project)
    registry_data_sources = test_registry.list_data_sources(project)
    assert len(registry_feature_views) == 1
    assert len(registry_data_sources) == 1
    registry_feature_view = registry_feature_views[0]
    assert registry_feature_view.batch_source == batch_source
    registry_data_source = registry_data_sources[0]
    assert registry_data_source == batch_source

    # Check that change to batch source propagates
    batch_source.timestamp_field = "new_ts_col"
    test_registry.apply_data_source(batch_source, project, commit=False)
    test_registry.apply_feature_view(fv1, project, commit=True)
    registry_feature_views = test_registry.list_feature_views(project)
    registry_data_sources = test_registry.list_data_sources(project)
    assert len(registry_feature_views) == 1
    assert len(registry_data_sources) == 1
    registry_feature_view = registry_feature_views[0]
    assert registry_feature_view.batch_source == batch_source
    registry_batch_source = test_registry.list_data_sources(project)[0]
    assert registry_batch_source == batch_source

    test_registry.teardown()

    # Will try to reload registry, which will fail because the file has been deleted
    with pytest.raises(FileNotFoundError):
        test_registry._get_registry_proto()


def test_commit():
    fd, registry_path = mkstemp()
    registry_config = RegistryConfig(path=registry_path, cache_ttl_seconds=600)
    test_registry = Registry(registry_config, None)

    entity = Entity(
        name="driver_car_id", description="Car driver id", tags={"team": "matchmaking"},
    )

    project = "project"

    # Register Entity without commiting
    test_registry.apply_entity(entity, project, commit=False)

    # Retrieving the entity should still succeed
    entities = test_registry.list_entities(project, allow_cache=True)

    entity = entities[0]
    assert (
        len(entities) == 1
        and entity.name == "driver_car_id"
        and entity.description == "Car driver id"
        and "team" in entity.tags
        and entity.tags["team"] == "matchmaking"
    )

    entity = test_registry.get_entity("driver_car_id", project, allow_cache=True)
    assert (
        entity.name == "driver_car_id"
        and entity.description == "Car driver id"
        and "team" in entity.tags
        and entity.tags["team"] == "matchmaking"
    )

    # Create new registry that points to the same store
    registry_with_same_store = Registry(registry_config, None)

    # Retrieving the entity should fail since the store is empty
    entities = registry_with_same_store.list_entities(project)
    assert len(entities) == 0

    # commit from the original registry
    test_registry.commit()

    # Reconstruct the new registry in order to read the newly written store
    registry_with_same_store = Registry(registry_config, None)

    # Retrieving the entity should now succeed
    entities = registry_with_same_store.list_entities(project)

    entity = entities[0]
    assert (
        len(entities) == 1
        and entity.name == "driver_car_id"
        and entity.description == "Car driver id"
        and "team" in entity.tags
        and entity.tags["team"] == "matchmaking"
    )

    entity = test_registry.get_entity("driver_car_id", project)
    assert (
        entity.name == "driver_car_id"
        and entity.description == "Car driver id"
        and "team" in entity.tags
        and entity.tags["team"] == "matchmaking"
    )

    test_registry.teardown()

    # Will try to reload registry, which will fail because the file has been deleted
    with pytest.raises(FileNotFoundError):
        test_registry._get_registry_proto()
