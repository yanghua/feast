from datetime import timedelta

import pytest

from feast.aggregation import Aggregation
from feast.batch_feature_view import BatchFeatureView
from feast.data_format import AvroFormat
from feast.data_source import KafkaSource, PushSource
from feast.entity import Entity
from feast.field import Field
from feast.infra.offline_stores.file_source import FileSource
from feast.stream_feature_view import StreamFeatureView
from feast.types import Float32


def test_create_batch_feature_view():
    batch_source = FileSource(path="some path")
    BatchFeatureView(
        name="test batch feature view",
        entities=[],
        ttl=timedelta(days=30),
        source=batch_source,
    )

    with pytest.raises(ValueError):
        BatchFeatureView(
            name="test batch feature view", entities=[], ttl=timedelta(days=30)
        )

    stream_source = KafkaSource(
        name="kafka",
        timestamp_field="event_timestamp",
        bootstrap_servers="",
        message_format=AvroFormat(""),
        topic="topic",
        batch_source=FileSource(path="some path"),
    )
    with pytest.raises(ValueError):
        BatchFeatureView(
            name="test batch feature view",
            entities=[],
            ttl=timedelta(days=30),
            source=stream_source,
        )


def test_create_stream_feature_view():
    stream_source = KafkaSource(
        name="kafka",
        timestamp_field="event_timestamp",
        bootstrap_servers="",
        message_format=AvroFormat(""),
        topic="topic",
        batch_source=FileSource(path="some path"),
    )
    StreamFeatureView(
        name="test kafka stream feature view",
        entities=[],
        ttl=timedelta(days=30),
        source=stream_source,
        aggregations=[],
    )

    push_source = PushSource(
        name="push source", batch_source=FileSource(path="some path")
    )
    StreamFeatureView(
        name="test push source feature view",
        entities=[],
        ttl=timedelta(days=30),
        source=push_source,
        aggregations=[],
    )

    with pytest.raises(ValueError):
        StreamFeatureView(
            name="test batch feature view",
            entities=[],
            ttl=timedelta(days=30),
            aggregations=[],
        )

    with pytest.raises(ValueError):
        StreamFeatureView(
            name="test batch feature view",
            entities=[],
            ttl=timedelta(days=30),
            source=FileSource(path="some path"),
            aggregations=[],
        )


def simple_udf(x: int):
    return x + 3


def test_stream_feature_view_serialization():
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
            )
        ],
        timestamp_field="event_timestamp",
        mode="spark",
        source=stream_source,
        udf=simple_udf,
        tags={},
    )

    sfv_proto = sfv.to_proto()

    new_sfv = StreamFeatureView.from_proto(sfv_proto=sfv_proto)
    assert new_sfv == sfv
