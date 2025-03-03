import datetime
import itertools
import os
import time
import unittest
from datetime import timedelta
from typing import Any, Dict, List, Tuple, Union

import assertpy
import numpy as np
import pandas as pd
import pytest
import requests
from botocore.exceptions import BotoCoreError

from feast import Entity, FeatureService, FeatureView, Field
from feast.errors import (
    FeatureNameCollisionError,
    RequestDataNotFoundInEntityRowsException,
)
from feast.online_response import TIMESTAMP_POSTFIX
from feast.types import Float32, Int32, String
from feast.wait import wait_retry_backoff
from tests.integration.feature_repos.repo_configuration import (
    Environment,
    construct_universal_feature_views,
)
from tests.integration.feature_repos.universal.entities import (
    customer,
    driver,
    location,
)
from tests.integration.feature_repos.universal.feature_views import (
    create_driver_hourly_stats_feature_view,
    driver_feature_view,
)
from tests.utils.data_source_utils import prep_file_source


@pytest.mark.integration
@pytest.mark.universal_online_stores(only=["redis"])
def test_entity_ttl_online_store(environment, universal_data_sources):
    if os.getenv("FEAST_IS_LOCAL_TEST", "False") == "True":
        return
    fs = environment.feature_store
    # setting ttl setting in online store to 1 second
    fs.config.online_store.key_ttl_seconds = 1
    entities, datasets, data_sources = universal_data_sources
    driver_hourly_stats = create_driver_hourly_stats_feature_view(data_sources.driver)
    driver_entity = driver()

    # Register Feature View and Entity
    fs.apply([driver_hourly_stats, driver_entity])

    # fake data to ingest into Online Store
    data = {
        "driver_id": [1],
        "conv_rate": [0.5],
        "acc_rate": [0.6],
        "avg_daily_trips": [4],
        "event_timestamp": [pd.Timestamp(datetime.datetime.utcnow()).round("ms")],
        "created": [pd.Timestamp(datetime.datetime.utcnow()).round("ms")],
    }
    df_ingest = pd.DataFrame(data)

    # directly ingest data into the Online Store
    fs.write_to_online_store("driver_stats", df_ingest)

    # assert the right data is in the Online Store
    df = fs.get_online_features(
        features=[
            "driver_stats:avg_daily_trips",
            "driver_stats:acc_rate",
            "driver_stats:conv_rate",
        ],
        entity_rows=[{"driver_id": 1}],
    ).to_df()
    assertpy.assert_that(df["avg_daily_trips"].iloc[0]).is_equal_to(4)
    assertpy.assert_that(df["acc_rate"].iloc[0]).is_close_to(0.6, 1e-6)
    assertpy.assert_that(df["conv_rate"].iloc[0]).is_close_to(0.5, 1e-6)

    # simulate time passing for testing ttl
    time.sleep(1)

    # retrieve the same entity again
    df = fs.get_online_features(
        features=[
            "driver_stats:avg_daily_trips",
            "driver_stats:acc_rate",
            "driver_stats:conv_rate",
        ],
        entity_rows=[{"driver_id": 1}],
    ).to_df()
    # assert that the entity features expired in the online store
    assertpy.assert_that(df["avg_daily_trips"].iloc[0]).is_none()
    assertpy.assert_that(df["acc_rate"].iloc[0]).is_none()
    assertpy.assert_that(df["conv_rate"].iloc[0]).is_none()


# TODO: make this work with all universal (all online store types)
@pytest.mark.integration
@pytest.mark.universal_online_stores(only=["redis"])
def test_write_to_online_store_event_check(environment):
    if os.getenv("FEAST_IS_LOCAL_TEST", "False") == "True":
        return
    fs = environment.feature_store

    # write same data points 3 with different timestamps
    now = pd.Timestamp(datetime.datetime.utcnow()).round("ms")
    hour_ago = pd.Timestamp(datetime.datetime.utcnow() - timedelta(hours=1)).round("ms")
    latest = pd.Timestamp(datetime.datetime.utcnow() + timedelta(seconds=1)).round("ms")

    data = {
        "id": [123, 567, 890],
        "string_col": ["OLD_FEATURE", "LATEST_VALUE2", "LATEST_VALUE3"],
        "ts_1": [hour_ago, now, now],
    }
    dataframe_source = pd.DataFrame(data)
    with prep_file_source(df=dataframe_source, timestamp_field="ts_1") as file_source:
        e = Entity(name="id")

        # Create Feature View
        fv1 = FeatureView(
            name="feature_view_123",
            schema=[Field(name="string_col", dtype=String)],
            entities=[e],
            batch_source=file_source,
            ttl=timedelta(minutes=5),
        )
        # Register Feature View and Entity
        fs.apply([fv1, e])

        #  data to ingest into Online Store (recent)
        data = {
            "id": [123],
            "string_col": ["hi_123"],
            "ts_1": [now],
        }
        df_data = pd.DataFrame(data)

        # directly ingest data into the Online Store
        fs.write_to_online_store("feature_view_123", df_data)

        df = fs.get_online_features(
            features=["feature_view_123:string_col"], entity_rows=[{"id": 123}]
        ).to_df()
        assert df["string_col"].iloc[0] == "hi_123"

        # data to ingest into Online Store (1 hour delayed data)
        # should now overwrite features for id=123 because it's less recent data
        data = {
            "id": [123, 567, 890],
            "string_col": ["bye_321", "hello_123", "greetings_321"],
            "ts_1": [hour_ago, hour_ago, hour_ago],
        }
        df_data = pd.DataFrame(data)

        # directly ingest data into the Online Store
        fs.write_to_online_store("feature_view_123", df_data)

        df = fs.get_online_features(
            features=["feature_view_123:string_col"],
            entity_rows=[{"id": 123}, {"id": 567}, {"id": 890}],
        ).to_df()
        assert df["string_col"].iloc[0] == "hi_123"
        assert df["string_col"].iloc[1] == "hello_123"
        assert df["string_col"].iloc[2] == "greetings_321"

        # should overwrite string_col for id=123 because it's most recent based on event_timestamp
        data = {
            "id": [123],
            "string_col": ["LATEST_VALUE"],
            "ts_1": [latest],
        }
        df_data = pd.DataFrame(data)

        fs.write_to_online_store("feature_view_123", df_data)

        df = fs.get_online_features(
            features=["feature_view_123:string_col"],
            entity_rows=[{"id": 123}, {"id": 567}, {"id": 890}],
        ).to_df()
        assert df["string_col"].iloc[0] == "LATEST_VALUE"
        assert df["string_col"].iloc[1] == "hello_123"
        assert df["string_col"].iloc[2] == "greetings_321"

        # writes to online store via datasource (dataframe_source) materialization
        fs.materialize(
            start_date=datetime.datetime.now() - timedelta(hours=12),
            end_date=datetime.datetime.utcnow(),
        )

        df = fs.get_online_features(
            features=["feature_view_123:string_col"],
            entity_rows=[{"id": 123}, {"id": 567}, {"id": 890}],
        ).to_df()
        assert df["string_col"].iloc[0] == "LATEST_VALUE"
        assert df["string_col"].iloc[1] == "LATEST_VALUE2"
        assert df["string_col"].iloc[2] == "LATEST_VALUE3"


@pytest.mark.integration
@pytest.mark.universal_online_stores
def test_write_to_online_store(environment, universal_data_sources):
    fs = environment.feature_store
    entities, datasets, data_sources = universal_data_sources
    driver_hourly_stats = create_driver_hourly_stats_feature_view(data_sources.driver)
    driver_entity = driver()

    # Register Feature View and Entity
    fs.apply([driver_hourly_stats, driver_entity])

    # fake data to ingest into Online Store
    data = {
        "driver_id": [123],
        "conv_rate": [0.85],
        "acc_rate": [0.91],
        "avg_daily_trips": [14],
        "event_timestamp": [pd.Timestamp(datetime.datetime.utcnow()).round("ms")],
        "created": [pd.Timestamp(datetime.datetime.utcnow()).round("ms")],
    }
    df_data = pd.DataFrame(data)

    # directly ingest data into the Online Store
    fs.write_to_online_store("driver_stats", df_data)

    # assert the right data is in the Online Store
    df = fs.get_online_features(
        features=[
            "driver_stats:avg_daily_trips",
            "driver_stats:acc_rate",
            "driver_stats:conv_rate",
        ],
        entity_rows=[{"driver_id": 123}],
    ).to_df()
    assertpy.assert_that(df["avg_daily_trips"].iloc[0]).is_equal_to(14)
    assertpy.assert_that(df["acc_rate"].iloc[0]).is_close_to(0.91, 1e-6)
    assertpy.assert_that(df["conv_rate"].iloc[0]).is_close_to(0.85, 1e-6)


def _get_online_features_dict_remotely(
    endpoint: str,
    features: Union[List[str], FeatureService],
    entity_rows: List[Dict[str, Any]],
    full_feature_names: bool = False,
) -> Dict[str, List[Any]]:
    """Sends the online feature request to a remote feature server (through endpoint) and returns the feature dict.

    The output should be identical to:

    fs.get_online_features(features=features, entity_rows=entity_rows, full_feature_names=full_feature_names).to_dict()

    This makes it easy to test the remote feature server by comparing the output to the local method.

    """
    request = {
        # Convert list of dicts (entity_rows) into dict of lists (entities) for json request
        "entities": {key: [row[key] for row in entity_rows] for key in entity_rows[0]},
        "full_feature_names": full_feature_names,
    }
    # Either set features of feature_service depending on the parameter
    if isinstance(features, list):
        request["features"] = features
    else:
        request["feature_service"] = features.name
    for _ in range(25):
        # Send the request to the remote feature server and get the response in JSON format
        response = requests.post(
            f"{endpoint}/get-online-features", json=request, timeout=30
        ).json()
        # Retry if the response is internal server error, which can happen when lambda is being restarted
        if response.get("message") != "Internal Server Error":
            break
        # Sleep between retries to give the server some time to start
        time.sleep(15)
    else:
        raise Exception("Failed to get online features from remote feature server")
    if "metadata" not in response:
        raise Exception(
            f"Failed to get online features from remote feature server {response}"
        )
    keys = response["metadata"]["feature_names"]
    # Get rid of unnecessary structure in the response, leaving list of dicts
    values = [row["values"] for row in response["results"]]
    # Convert list of dicts (response) into dict of lists which is the format of the return value
    return {key: feature_vector for key, feature_vector in zip(keys, values)}


def get_online_features_dict(
    environment: Environment,
    endpoint: str,
    features: Union[List[str], FeatureService],
    entity_rows: List[Dict[str, Any]],
    full_feature_names: bool = False,
) -> Dict[str, List[Any]]:
    """Get the online feature values from both SDK and remote feature servers, assert equality and return values.

    Always use this method instead of fs.get_online_features(...) in this test file.

    """
    online_features = environment.feature_store.get_online_features(
        features=features,
        entity_rows=entity_rows,
        full_feature_names=full_feature_names,
    )
    assertpy.assert_that(online_features).is_not_none()
    dict1 = online_features.to_dict()

    # If endpoint is None, it means that a local / remote feature server aren't configured
    if endpoint is not None:
        dict2 = _get_online_features_dict_remotely(
            endpoint=endpoint,
            features=features,
            entity_rows=entity_rows,
            full_feature_names=full_feature_names,
        )

        # Make sure that the two dicts are equal
        assertpy.assert_that(dict1).is_equal_to(dict2)
    elif environment.python_feature_server:
        raise ValueError(
            "feature_store.get_feature_server_endpoint() is None while python feature server is enabled"
        )
    return dict1


@pytest.mark.integration
def test_online_retrieval_with_shared_batch_source(environment, universal_data_sources):
    # Addresses https://github.com/feast-dev/feast/issues/2576

    fs = environment.feature_store

    entities, datasets, data_sources = universal_data_sources
    driver_stats_v1 = FeatureView(
        name="driver_stats_v1",
        entities=["driver"],
        schema=[Field(name="avg_daily_trips", dtype=Int32)],
        source=data_sources.driver,
    )
    driver_stats_v2 = FeatureView(
        name="driver_stats_v2",
        entities=["driver"],
        schema=[
            Field(name="avg_daily_trips", dtype=Int32),
            Field(name="conv_rate", dtype=Float32),
        ],
        source=data_sources.driver,
    )

    fs.apply([driver(), driver_stats_v1, driver_stats_v2])

    data = pd.DataFrame(
        {
            "driver_id": [1, 2],
            "avg_daily_trips": [4, 5],
            "conv_rate": [0.5, 0.3],
            "event_timestamp": [
                pd.to_datetime(1646263500, utc=True, unit="s"),
                pd.to_datetime(1646263600, utc=True, unit="s"),
            ],
            "created": [
                pd.to_datetime(1646263500, unit="s"),
                pd.to_datetime(1646263600, unit="s"),
            ],
        }
    )
    fs.write_to_online_store("driver_stats_v1", data.drop("conv_rate", axis=1))
    fs.write_to_online_store("driver_stats_v2", data)

    with pytest.raises(KeyError):
        fs.get_online_features(
            features=[
                # `driver_stats_v1` does not have `conv_rate`
                "driver_stats_v1:conv_rate",
            ],
            entity_rows=[{"driver_id": 1}, {"driver_id": 2}],
        )


@pytest.mark.integration
@pytest.mark.universal_online_stores
@pytest.mark.parametrize("full_feature_names", [True, False], ids=lambda v: str(v))
def test_online_retrieval_with_event_timestamps(
    environment, universal_data_sources, full_feature_names
):
    fs = environment.feature_store
    entities, datasets, data_sources = universal_data_sources
    feature_views = construct_universal_feature_views(data_sources)

    fs.apply([driver(), feature_views.driver, feature_views.global_fv])

    # fake data to ingest into Online Store
    data = {
        "driver_id": [1, 2],
        "conv_rate": [0.5, 0.3],
        "acc_rate": [0.6, 0.4],
        "avg_daily_trips": [4, 5],
        "event_timestamp": [
            pd.to_datetime(1646263500, utc=True, unit="s"),
            pd.to_datetime(1646263600, utc=True, unit="s"),
        ],
        "created": [
            pd.to_datetime(1646263500, unit="s"),
            pd.to_datetime(1646263600, unit="s"),
        ],
    }
    df_ingest = pd.DataFrame(data)

    # directly ingest data into the Online Store
    fs.write_to_online_store("driver_stats", df_ingest)

    response = fs.get_online_features(
        features=[
            "driver_stats:avg_daily_trips",
            "driver_stats:acc_rate",
            "driver_stats:conv_rate",
        ],
        entity_rows=[{"driver_id": 1}, {"driver_id": 2}],
    )
    df = response.to_df(True)
    assertpy.assert_that(len(df)).is_equal_to(2)
    assertpy.assert_that(df["driver_id"].iloc[0]).is_equal_to(1)
    assertpy.assert_that(df["driver_id"].iloc[1]).is_equal_to(2)
    assertpy.assert_that(df["avg_daily_trips" + TIMESTAMP_POSTFIX].iloc[0]).is_equal_to(
        1646263500
    )
    assertpy.assert_that(df["avg_daily_trips" + TIMESTAMP_POSTFIX].iloc[1]).is_equal_to(
        1646263600
    )
    assertpy.assert_that(df["acc_rate" + TIMESTAMP_POSTFIX].iloc[0]).is_equal_to(
        1646263500
    )
    assertpy.assert_that(df["acc_rate" + TIMESTAMP_POSTFIX].iloc[1]).is_equal_to(
        1646263600
    )
    assertpy.assert_that(df["conv_rate" + TIMESTAMP_POSTFIX].iloc[0]).is_equal_to(
        1646263500
    )
    assertpy.assert_that(df["conv_rate" + TIMESTAMP_POSTFIX].iloc[1]).is_equal_to(
        1646263600
    )


@pytest.mark.integration
@pytest.mark.universal_online_stores
@pytest.mark.goserver
@pytest.mark.parametrize("full_feature_names", [True, False], ids=lambda v: str(v))
def test_online_retrieval(
    environment, universal_data_sources, feature_server_endpoint, full_feature_names
):
    fs = environment.feature_store
    entities, datasets, data_sources = universal_data_sources
    feature_views = construct_universal_feature_views(data_sources)

    feature_service = FeatureService(
        "convrate_plus100",
        features=[
            feature_views.driver[["conv_rate"]],
            feature_views.driver_odfv,
            feature_views.customer[["current_balance"]],
        ],
    )
    feature_service_entity_mapping = FeatureService(
        name="entity_mapping",
        features=[
            feature_views.location.with_name("origin").with_join_key_map(
                {"location_id": "origin_id"}
            ),
            feature_views.location.with_name("destination").with_join_key_map(
                {"location_id": "destination_id"}
            ),
        ],
    )

    feast_objects = []
    feast_objects.extend(feature_views.values())
    feast_objects.extend(
        [
            driver(),
            customer(),
            location(),
            feature_service,
            feature_service_entity_mapping,
        ]
    )
    fs.apply(feast_objects)
    fs.materialize(
        environment.start_date - timedelta(days=1),
        environment.end_date + timedelta(days=1),
    )

    entity_sample = datasets.orders_df.sample(10)[
        ["customer_id", "driver_id", "order_id", "event_timestamp"]
    ]
    orders_df = datasets.orders_df[
        (
            datasets.orders_df["customer_id"].isin(entity_sample["customer_id"])
            & datasets.orders_df["driver_id"].isin(entity_sample["driver_id"])
        )
    ]

    sample_drivers = entity_sample["driver_id"]
    drivers_df = datasets.driver_df[
        datasets.driver_df["driver_id"].isin(sample_drivers)
    ]

    sample_customers = entity_sample["customer_id"]
    customers_df = datasets.customer_df[
        datasets.customer_df["customer_id"].isin(sample_customers)
    ]

    location_pairs = np.array(list(itertools.permutations(entities.location_vals, 2)))
    sample_location_pairs = location_pairs[
        np.random.choice(len(location_pairs), 10)
    ].T.tolist()
    origins_df = datasets.location_df[
        datasets.location_df["location_id"].isin(sample_location_pairs[0])
    ]
    destinations_df = datasets.location_df[
        datasets.location_df["location_id"].isin(sample_location_pairs[1])
    ]

    global_df = datasets.global_df

    entity_rows = [
        {"driver_id": d, "customer_id": c, "val_to_add": 50}
        for (d, c) in zip(sample_drivers, sample_customers)
    ]

    feature_refs = [
        "driver_stats:conv_rate",
        "driver_stats:avg_daily_trips",
        "customer_profile:current_balance",
        "customer_profile:avg_passenger_count",
        "customer_profile:lifetime_trip_count",
        "conv_rate_plus_100:conv_rate_plus_100",
        "conv_rate_plus_100:conv_rate_plus_val_to_add",
        "order:order_is_success",
        "global_stats:num_rides",
        "global_stats:avg_ride_length",
    ]
    unprefixed_feature_refs = [f.rsplit(":", 1)[-1] for f in feature_refs if ":" in f]
    # Remove the on demand feature view output features, since they're not present in the source dataframe
    unprefixed_feature_refs.remove("conv_rate_plus_100")
    unprefixed_feature_refs.remove("conv_rate_plus_val_to_add")

    online_features_dict = get_online_features_dict(
        environment=environment,
        endpoint=feature_server_endpoint,
        features=feature_refs,
        entity_rows=entity_rows,
        full_feature_names=full_feature_names,
    )

    # Test that the on demand feature views compute properly even if the dependent conv_rate
    # feature isn't requested.
    online_features_no_conv_rate = get_online_features_dict(
        environment=environment,
        endpoint=feature_server_endpoint,
        features=[ref for ref in feature_refs if ref != "driver_stats:conv_rate"],
        entity_rows=entity_rows,
        full_feature_names=full_feature_names,
    )

    assert online_features_no_conv_rate is not None

    keys = set(online_features_dict.keys())
    expected_keys = set(
        f.replace(":", "__") if full_feature_names else f.split(":")[-1]
        for f in feature_refs
    ) | {"customer_id", "driver_id"}
    assert (
        keys == expected_keys
    ), f"Response keys are different from expected: {keys - expected_keys} (extra) and {expected_keys - keys} (missing)"

    tc = unittest.TestCase()
    for i, entity_row in enumerate(entity_rows):
        df_features = get_latest_feature_values_from_dataframes(
            driver_df=drivers_df,
            customer_df=customers_df,
            orders_df=orders_df,
            global_df=global_df,
            entity_row=entity_row,
        )

        assert df_features["customer_id"] == online_features_dict["customer_id"][i]
        assert df_features["driver_id"] == online_features_dict["driver_id"][i]
        tc.assertAlmostEqual(
            online_features_dict[
                response_feature_name(
                    "conv_rate_plus_100", feature_refs, full_feature_names
                )
            ][i],
            df_features["conv_rate"] + 100,
            delta=0.0001,
        )
        tc.assertAlmostEqual(
            online_features_dict[
                response_feature_name(
                    "conv_rate_plus_val_to_add", feature_refs, full_feature_names
                )
            ][i],
            df_features["conv_rate"] + df_features["val_to_add"],
            delta=0.0001,
        )
        for unprefixed_feature_ref in unprefixed_feature_refs:
            tc.assertAlmostEqual(
                df_features[unprefixed_feature_ref],
                online_features_dict[
                    response_feature_name(
                        unprefixed_feature_ref, feature_refs, full_feature_names
                    )
                ][i],
                delta=0.0001,
            )

    # Check what happens for missing values
    missing_responses_dict = get_online_features_dict(
        environment=environment,
        endpoint=feature_server_endpoint,
        features=feature_refs,
        entity_rows=[{"driver_id": 0, "customer_id": 0, "val_to_add": 100}],
        full_feature_names=full_feature_names,
    )
    assert missing_responses_dict is not None
    for unprefixed_feature_ref in unprefixed_feature_refs:
        if unprefixed_feature_ref not in {"num_rides", "avg_ride_length"}:
            tc.assertIsNone(
                missing_responses_dict[
                    response_feature_name(
                        unprefixed_feature_ref, feature_refs, full_feature_names
                    )
                ][0]
            )

    # Check what happens for missing request data
    with pytest.raises(RequestDataNotFoundInEntityRowsException):
        get_online_features_dict(
            environment=environment,
            endpoint=feature_server_endpoint,
            features=feature_refs,
            entity_rows=[{"driver_id": 0, "customer_id": 0}],
            full_feature_names=full_feature_names,
        )

    assert_feature_service_correctness(
        environment,
        feature_server_endpoint,
        feature_service,
        entity_rows,
        full_feature_names,
        drivers_df,
        customers_df,
        orders_df,
        global_df,
    )

    entity_rows = [
        {"origin_id": origin, "destination_id": destination}
        for (_driver, _customer, origin, destination) in zip(
            sample_drivers, sample_customers, *sample_location_pairs
        )
    ]
    assert_feature_service_entity_mapping_correctness(
        environment,
        feature_server_endpoint,
        feature_service_entity_mapping,
        entity_rows,
        full_feature_names,
        origins_df,
        destinations_df,
    )


@pytest.mark.integration
@pytest.mark.universal_online_stores(only=["redis"])
def test_online_store_cleanup(environment, universal_data_sources):
    """
    Some online store implementations (like Redis) keep features from different features views
    but with common entities together.
    This might end up with deletion of all features attached to the entity,
    when only one feature view was deletion target (see https://github.com/feast-dev/feast/issues/2150).

    Plan:
        1. Register two feature views with common entity "driver"
        2. Materialize data
        3. Check if features are available (via online retrieval)
        4. Delete one feature view
        5. Check that features for other are still available
        6. Delete another feature view (and create again)
        7. Verify that features for both feature view were deleted
    """
    fs = environment.feature_store
    entities, datasets, data_sources = universal_data_sources
    driver_stats_fv = construct_universal_feature_views(data_sources).driver

    driver_entities = entities.driver_vals
    df = pd.DataFrame(
        {
            "ts_1": [environment.end_date] * len(driver_entities),
            "created_ts": [environment.end_date] * len(driver_entities),
            "driver_id": driver_entities,
            "value": np.random.random(size=len(driver_entities)),
        }
    )

    ds = environment.data_source_creator.create_data_source(
        df, destination_name="simple_driver_dataset"
    )

    simple_driver_fv = driver_feature_view(
        data_source=ds, name="test_universal_online_simple_driver"
    )

    fs.apply([driver(), simple_driver_fv, driver_stats_fv])

    fs.materialize(
        environment.start_date - timedelta(days=1),
        environment.end_date + timedelta(days=1),
    )
    expected_values = df.sort_values(by="driver_id")

    features = [f"{simple_driver_fv.name}:value"]
    entity_rows = [{"driver_id": driver_id} for driver_id in sorted(driver_entities)]

    online_features = fs.get_online_features(
        features=features, entity_rows=entity_rows
    ).to_dict()
    assert np.allclose(expected_values["value"], online_features["value"])

    fs.apply(
        objects=[simple_driver_fv], objects_to_delete=[driver_stats_fv], partial=False
    )

    online_features = fs.get_online_features(
        features=features, entity_rows=entity_rows
    ).to_dict()
    assert np.allclose(expected_values["value"], online_features["value"])

    fs.apply(objects=[], objects_to_delete=[simple_driver_fv], partial=False)

    def eventually_apply() -> Tuple[None, bool]:
        try:
            fs.apply([simple_driver_fv])
        except BotoCoreError:
            return None, False

        return None, True

    # Online store backend might have eventual consistency in schema update
    # So recreating table that was just deleted might need some retries
    wait_retry_backoff(eventually_apply, timeout_secs=60)

    online_features = fs.get_online_features(
        features=features, entity_rows=entity_rows
    ).to_dict()
    assert all(v is None for v in online_features["value"])


def response_feature_name(
    feature: str, feature_refs: List[str], full_feature_names: bool
) -> str:
    if not full_feature_names:
        return feature

    for feature_ref in feature_refs:
        if feature_ref.endswith(feature):
            return feature_ref.replace(":", "__")

    return feature


def get_latest_row(entity_row, df, join_key, entity_key):
    rows = df[df[join_key] == entity_row[entity_key]]
    return rows.loc[rows["event_timestamp"].idxmax()].to_dict()


def get_latest_feature_values_from_dataframes(
    driver_df,
    customer_df,
    orders_df,
    entity_row,
    global_df=None,
    origin_df=None,
    destination_df=None,
):
    latest_driver_row = get_latest_row(entity_row, driver_df, "driver_id", "driver_id")
    latest_customer_row = get_latest_row(
        entity_row, customer_df, "customer_id", "customer_id"
    )

    # Since the event timestamp columns may contain timestamps of different timezones,
    # we must first convert the timestamps to UTC before we can compare them.
    order_rows = orders_df[
        (orders_df["driver_id"] == entity_row["driver_id"])
        & (orders_df["customer_id"] == entity_row["customer_id"])
    ]
    timestamps = order_rows[["event_timestamp"]]
    timestamps["event_timestamp"] = pd.to_datetime(
        timestamps["event_timestamp"], utc=True
    )
    max_index = timestamps["event_timestamp"].idxmax()
    latest_orders_row = order_rows.loc[max_index]

    if global_df is not None:
        latest_global_row = global_df.loc[
            global_df["event_timestamp"].idxmax()
        ].to_dict()
    if origin_df is not None:
        latest_location_row = get_latest_feature_values_for_location_df(
            entity_row, origin_df, destination_df
        )

    request_data_features = entity_row.copy()
    request_data_features.pop("driver_id")
    request_data_features.pop("customer_id")
    if global_df is not None:
        return {
            **latest_customer_row,
            **latest_driver_row,
            **latest_orders_row,
            **latest_global_row,
            **request_data_features,
        }
    if origin_df is not None:
        request_data_features.pop("origin_id")
        request_data_features.pop("destination_id")
        return {
            **latest_customer_row,
            **latest_driver_row,
            **latest_orders_row,
            **latest_location_row,
            **request_data_features,
        }
    return {
        **latest_customer_row,
        **latest_driver_row,
        **latest_orders_row,
        **request_data_features,
    }


def get_latest_feature_values_for_location_df(entity_row, origin_df, destination_df):
    latest_origin_row = get_latest_row(
        entity_row, origin_df, "location_id", "origin_id"
    )
    latest_destination_row = get_latest_row(
        entity_row, destination_df, "location_id", "destination_id"
    )
    # Need full feature names for shadow entities
    latest_origin_row["origin__temperature"] = latest_origin_row.pop("temperature")
    latest_destination_row["destination__temperature"] = latest_destination_row.pop(
        "temperature"
    )

    return {
        **latest_origin_row,
        **latest_destination_row,
    }


def assert_feature_service_correctness(
    environment,
    endpoint,
    feature_service,
    entity_rows,
    full_feature_names,
    drivers_df,
    customers_df,
    orders_df,
    global_df,
):
    feature_service_online_features_dict = get_online_features_dict(
        environment=environment,
        endpoint=endpoint,
        features=feature_service,
        entity_rows=entity_rows,
        full_feature_names=full_feature_names,
    )
    feature_service_keys = feature_service_online_features_dict.keys()
    expected_feature_refs = [
        f"{projection.name_to_use()}__{feature.name}"
        if full_feature_names
        else feature.name
        for projection in feature_service.feature_view_projections
        for feature in projection.features
    ]
    assert set(feature_service_keys) == set(expected_feature_refs) | {
        "customer_id",
        "driver_id",
    }

    tc = unittest.TestCase()
    for i, entity_row in enumerate(entity_rows):
        df_features = get_latest_feature_values_from_dataframes(
            driver_df=drivers_df,
            customer_df=customers_df,
            orders_df=orders_df,
            global_df=global_df,
            entity_row=entity_row,
        )
        tc.assertAlmostEqual(
            feature_service_online_features_dict[
                response_feature_name(
                    "conv_rate_plus_100", expected_feature_refs, full_feature_names
                )
            ][i],
            df_features["conv_rate"] + 100,
            delta=0.0001,
        )


def assert_feature_service_entity_mapping_correctness(
    environment,
    endpoint,
    feature_service,
    entity_rows,
    full_feature_names,
    origins_df,
    destinations_df,
):
    if full_feature_names:
        feature_service_online_features_dict = get_online_features_dict(
            environment=environment,
            endpoint=endpoint,
            features=feature_service,
            entity_rows=entity_rows,
            full_feature_names=full_feature_names,
        )
        feature_service_keys = feature_service_online_features_dict.keys()

        expected_features = [
            f"{projection.name_to_use()}__{feature.name}"
            if full_feature_names
            else feature.name
            for projection in feature_service.feature_view_projections
            for feature in projection.features
        ]
        assert set(feature_service_keys) == set(expected_features) | {
            "destination_id",
            "origin_id",
        }

        for i, entity_row in enumerate(entity_rows):
            df_features = get_latest_feature_values_for_location_df(
                origin_df=origins_df,
                destination_df=destinations_df,
                entity_row=entity_row,
            )
            for feature_name in ["origin__temperature", "destination__temperature"]:
                assert (
                    feature_service_online_features_dict[feature_name][i]
                    == df_features[feature_name]
                )
    else:
        # using 2 of the same FeatureView without full_feature_names=True will result in collision
        with pytest.raises(FeatureNameCollisionError):
            get_online_features_dict(
                environment=environment,
                endpoint=endpoint,
                features=feature_service,
                entity_rows=entity_rows,
                full_feature_names=full_feature_names,
            )
