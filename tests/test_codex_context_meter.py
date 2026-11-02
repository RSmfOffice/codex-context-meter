from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_context_meter.session_stats import (
    ROLE_OVERHEAD,
    SessionStatsTracker,
    estimate_model_visible_item,
)
from codex_context_meter.widget import HEIGHT, WIDTH, clamp_overlay_position
from codex_context_meter.window_follower import translated_position


THREAD_ID = "01a187b3-00ff-7270-909e-c60b1d3e814f"


class SessionStatsTrackerTests(unittest.TestCase):
    def test_projects_context_from_anchor_and_reports_cache_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            session_dir = home / "sessions" / "2026" / "11" / "02"
            session_dir.mkdir(parents=True)
            session_path = session_dir / f"rollout-2026-11-02T20-00-00-{THREAD_ID}.jsonl"
            rows = [
                {
                    "type": "session_meta",
                    "payload": {"id": THREAD_ID, "model_provider": "deepseek"},
                },
                {
                    "type": "turn_context",
                    "payload": {"model": "deepseek-flash"},
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 10_000,
                                "cached_input_tokens": 9_500,
                                "cache_write_input_tokens": 0,
                                "output_tokens": 800,
                                "reasoning_output_tokens": 100,
                            },
                            "last_token_usage": {
                                "input_tokens": 5_000,
                                "cached_input_tokens": 4_800,
                                "cache_write_input_tokens": 0,
                                "output_tokens": 300,
                                "reasoning_output_tokens": 40,
                            },
                            "model_context_window": 100_000,
                        },
                    },
                    "timestamp": "2026-11-02T12:00:00Z",
                    "ordinal": 2,
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "x" * 400}],
                    },
                    "timestamp": "2026-11-02T12:00:01Z",
                    "ordinal": 3,
                },
            ]
            session_path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            (home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": THREAD_ID,
                        "thread_name": "测试会话",
                        "updated_at": "2026-11-02T12:00:01Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (home / ".codex-global-state.json").write_text(
                json.dumps(
                    {
                        "electron-persisted-atom-state": {
                            "thread-user-activity-times-v1": {
                                json.dumps(["local", THREAD_ID]): 1_793_600_000_000
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            tracker = SessionStatsTracker(home)
            snapshot = tracker.snapshot(THREAD_ID)
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(tracker.thread_id_for_title("测试会话"), THREAD_ID)
            self.assertIsNone(tracker.thread_id_for_title("不存在"))
            self.assertEqual(snapshot.thread_id, THREAD_ID)
            self.assertEqual(snapshot.title, "测试会话")
            self.assertEqual(snapshot.anchor_tokens, 5_000)
            self.assertGreater(snapshot.surface_delta_tokens, 0)
            self.assertEqual(
                snapshot.projected_tokens,
                5_000 + snapshot.surface_delta_tokens,
            )
            self.assertFalse(snapshot.exact)
            self.assertAlmostEqual(snapshot.cache_hit_rate or 0, 0.95)
            self.assertEqual(snapshot.remaining_tokens, 100_000 - snapshot.projected_tokens)
            self.assertAlmostEqual(snapshot.percent, snapshot.projected_tokens / 1000)

    def test_item_estimator_counts_role_and_blocks(self) -> None:
        estimate = estimate_model_visible_item(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "a" * 40}],
            }
        )
        self.assertEqual(estimate, ROLE_OVERHEAD + 4 + 10)

    def test_response_id_links_output_before_usage_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            session_dir = home / "sessions" / "2026" / "11" / "02"
            session_dir.mkdir(parents=True)
            session_path = session_dir / f"rollout-2026-11-02T20-00-00-{THREAD_ID}.jsonl"
            response_id = "resp-linked"
            rows = [
                {
                    "type": "turn_context",
                    "payload": {"model": "deepseek-flash"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "reasoning",
                        "encrypted_content": response_id,
                        "content": [{"type": "reasoning_text", "text": "r" * 80}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "m" * 120}],
                    },
                },
                {
                    "type": "token_usage_record",
                    "payload": {
                        "response_id": response_id,
                        "usage": {"input_tokens": 1_000},
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 2_000,
                                "cached_input_tokens": 1_500,
                            },
                            "last_token_usage": {"input_tokens": 1_000},
                            "model_context_window": 10_000,
                        },
                    },
                },
            ]
            session_path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            tracker = SessionStatsTracker(home)
            snapshot = tracker.snapshot(THREAD_ID)
            assert snapshot is not None
            self.assertGreater(snapshot.surface_delta_tokens, 50)
            self.assertEqual(
                snapshot.projected_tokens,
                1_000 + snapshot.surface_delta_tokens,
            )


class OverlayGeometryTests(unittest.TestCase):
    def test_overlay_position_is_clamped_to_host_bounds(self) -> None:
        bounds = (100, 200, 700, 600)
        self.assertEqual(
            clamp_overlay_position(bounds, 50, 100),
            (100, 200),
        )
        self.assertEqual(
            clamp_overlay_position(bounds, 10_000, 10_000),
            (700 - WIDTH, 600 - HEIGHT),
        )

    def test_overlay_position_remains_inside_regular_bounds(self) -> None:
        self.assertEqual(
            clamp_overlay_position((0, 0, 1920, 1080), 420, 360),
            (420, 360),
        )

    def test_native_follower_translates_with_host(self) -> None:
        previous_host = (100, 100, 900, 700)
        current_host = (148, 124, 948, 724)
        current_overlay = (600, 300, 838, 382)
        self.assertEqual(
            translated_position(
                previous_host,
                current_host,
                current_overlay,
                (100, 100, 1100, 800),
            ),
            (648, 324),
        )


class SessionCatalogCacheTests(unittest.TestCase):
    def test_catalog_cache_can_be_forced_after_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            sessions = home / "sessions" / "2026" / "11" / "02"
            sessions.mkdir(parents=True)
            first_id = "01a187b3-00ff-7270-909e-c60b1d3e814f"
            second_id = "01a187b3-00ff-7270-909e-c60b1d3e8150"
            (sessions / f"rollout-a-{first_id}.jsonl").write_text("", encoding="utf-8")

            tracker = SessionStatsTracker(home)
            self.assertEqual(len(tracker.recent_threads()), 1)
            (sessions / f"rollout-b-{second_id}.jsonl").write_text("", encoding="utf-8")
            self.assertEqual(len(tracker.recent_threads()), 1)
            self.assertEqual(len(tracker.recent_threads(force=True)), 2)
            self.assertIsNotNone(tracker.find_session_file(second_id))


if __name__ == "__main__":
    unittest.main()
