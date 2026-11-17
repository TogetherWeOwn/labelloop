"""Internal discovery uses core scores; it cannot publish assignments or decisions."""

from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from labelloop_api.management_grouping_publication import discover_initial_pairs
from labelloop_api.management_setup import INVALIDATING_OPERATIONS
from labelloop_core.grouping import ALGORITHM, AssetEvidence, GroupingThresholds

MODULE = "labelloop_api.management_grouping_publication"


def observations(distance):
    return [
        AssetEvidence("a", "a" * 64, 0, (), {}, {}),
        AssetEvidence("b", "b" * 64, 2**distance - 1, (), {}, {}),
    ]


class PairDiscoveryTests(TestCase):
    def discover(self, distance):
        connection = MagicMock()
        identifier = uuid4()
        connection.execute.return_value.scalar_one.return_value = identifier
        sources = observations(distance)
        assets = [SimpleNamespace(id=uuid4(), asset_key=row.asset_id) for row in sources]
        pins = {"sources": [row.asset_id for row in sources], "source_policy_sha256": "f" * 64}
        prepared = (assets, sources, {}, [], pins)
        with patch(f"{MODULE}.prepare_initial_grouping", return_value=prepared) as prepare:
            result = discover_initial_pairs(connection, "project", "workspace", "store", "pins")
        prepare.assert_called_once_with(connection, "project", "workspace", "store", "pins")
        return connection, identifier, assets, pins, result

    def test_band_endpoints_persist_only_pending_core_suggestions(self):
        for distance in (9, 12):
            with self.subTest(distance=distance):
                connection, identifier, assets, pins, result = self.discover(distance)
                connection.execute.assert_called_once()
                statement = connection.execute.call_args.args[0]
                compiled = statement.compile(dialect=postgresql.dialect())
                self.assertEqual(statement.table.name, "borderline_review")
                self.assertEqual(compiled.params["left_asset_id"], assets[0].id)
                self.assertEqual(compiled.params["right_asset_id"], assets[1].id)
                self.assertEqual(compiled.params["value"], str(distance))
                self.assertEqual(compiled.params["metric"], "phash")
                self.assertEqual(compiled.params["review_state"], "pending")
                self.assertEqual(compiled.params["algorithm_version"], ALGORITHM)
                self.assertEqual(result["thresholds"], asdict(GroupingThresholds()))
                self.assertEqual(result["sources"], pins["sources"])
                self.assertEqual(
                    result["pending_pairs"],
                    [
                        {
                            "id": str(identifier),
                            "left_asset_id": "a",
                            "right_asset_id": "b",
                            "metric": "phash",
                            "value": distance,
                        }
                    ],
                )

    def test_automatic_or_distant_pairs_do_not_become_human_decisions(self):
        for distance in (0, 8, 13, 64):
            with self.subTest(distance=distance):
                connection, _, _, _, result = self.discover(distance)
                connection.execute.assert_not_called()
                self.assertEqual(result["pending_pairs"], [])

    def test_preparation_refusal_does_not_write(self):
        connection = MagicMock()
        with patch(f"{MODULE}.prepare_initial_grouping", side_effect=ValueError("fixture")):
            with self.assertRaises(ValueError):
                discover_initial_pairs(connection, None, None, None, None)
        connection.execute.assert_not_called()

    def test_discovery_does_not_mutate_core_observations(self):
        sources = observations(10)
        sources[1] = replace(sources[1], metadata={"session": "shared"})
        before = list(sources)
        assets = [SimpleNamespace(id=uuid4(), asset_key=row.asset_id) for row in sources]
        with patch(
            f"{MODULE}.prepare_initial_grouping", return_value=(assets, sources, {}, [], {})
        ):
            discover_initial_pairs(MagicMock(), None, None, None, None)
        self.assertEqual(sources, before)

    def test_discovery_invalidates_prior_setup_receipts(self):
        self.assertIn("discover_initial_source_pairs", INVALIDATING_OPERATIONS)
