from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from codex_context_meter.handoff import create_handoff
from codex_context_meter.session_stats import (
    ROLE_OVERHEAD,
    SessionStatsTracker,
    estimate_model_visible_item,
)
from codex_context_meter.widget import (
    HEIGHT,
    PAYLOAD_CRITICAL_BYTES,
    PAYLOAD_WARNING_BYTES,
    WIDTH,
    clamp_overlay_position,
    payload_risk,
)
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

    def test_detects_image_payload_and_413(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            session_dir = home / "sessions" / "2026" / "11" / "02"
            session_dir.mkdir(parents=True)
            session_path = session_dir / f"rollout-2026-11-02T20-00-00-{THREAD_ID}.jsonl"
            image_url = "data:image/png;base64," + ("A" * 10_000)
            rows = [
                {
                    "type": "turn_context",
                    "payload": {"model": "deepseek-flash"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "output": [
                            {
                                "type": "input_image",
                                "image_url": image_url,
                            }
                        ],
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 5_000,
                                "cached_input_tokens": 4_000,
                            },
                            "last_token_usage": {"input_tokens": 5_000},
                            "model_context_window": 100_000,
                        },
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "error": {
                            "message": "unexpected status 413 Request Entity Too Large"
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
            self.assertEqual(snapshot.image_count, 1)
            self.assertEqual(snapshot.image_payload_bytes, len(image_url.encode("utf-8")))
            self.assertGreater(snapshot.request_payload_bytes, snapshot.image_payload_bytes)
            self.assertTrue(snapshot.request_too_large)
            self.assertEqual(snapshot.request_error, "413")

    def test_payload_stats_update_incrementally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            session_dir = home / "sessions" / "2026" / "11" / "02"
            session_dir.mkdir(parents=True)
            session_path = session_dir / f"rollout-2026-11-02T20-00-00-{THREAD_ID}.jsonl"
            base_rows = [
                {"type": "turn_context", "payload": {"model": "deepseek-flash"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "output": [
                            {
                                "type": "input_image",
                                "image_url": "data:image/png;base64,AAAA",
                            }
                        ],
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"input_tokens": 100},
                            "last_token_usage": {"input_tokens": 100},
                            "model_context_window": 10_000,
                        },
                    },
                },
            ]
            session_path.write_text(
                "\n".join(json.dumps(row) for row in base_rows) + "\n",
                encoding="utf-8",
            )
            tracker = SessionStatsTracker(home)
            first = tracker.snapshot(THREAD_ID)
            assert first is not None
            self.assertEqual(first.image_count, 1)

            extra_rows = [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "output": [
                            {
                                "type": "input_image",
                                "image_url": "data:image/png;base64,BBBBBBBB",
                            }
                        ],
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "error": {"message": "413 Payload Too Large"},
                    },
                },
            ]
            with session_path.open("a", encoding="utf-8") as stream:
                for row in extra_rows:
                    stream.write(json.dumps(row) + "\n")

            second = tracker.snapshot(THREAD_ID)
            assert second is not None
            self.assertEqual(second.image_count, 2)
            self.assertGreater(second.image_payload_bytes, first.image_payload_bytes)
            self.assertGreater(second.request_payload_bytes, first.request_payload_bytes)
            self.assertTrue(second.request_too_large)
            self.assertGreater(second.min_failure_payload_bytes, 0)

            with session_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {"type": "task_complete", "error": None},
                        }
                    )
                    + "\n"
                )
            recovered = tracker.snapshot(THREAD_ID)
            assert recovered is not None
            self.assertFalse(recovered.request_too_large)
            self.assertEqual(recovered.min_failure_payload_bytes, 0)

    def test_payload_budget_and_handoff_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            session_dir = home / "sessions" / "2026" / "11" / "02"
            session_dir.mkdir(parents=True)
            session_path = session_dir / f"rollout-2026-11-02T20-00-00-{THREAD_ID}.jsonl"
            rows = [
                {"type": "turn_context", "payload": {"model": "deepseek-flash"}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"input_tokens": 1_000},
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
            snapshot = SessionStatsTracker(home).snapshot(THREAD_ID)
            assert snapshot is not None
            normal = replace(
                snapshot,
                request_payload_bytes=10_000,
                request_too_large=False,
            )
            warning = replace(
                normal,
                request_payload_bytes=PAYLOAD_WARNING_BYTES,
            )
            critical = replace(
                normal,
                request_payload_bytes=PAYLOAD_CRITICAL_BYTES,
            )
            known_safe = replace(
                normal,
                request_payload_bytes=50_000_000,
                max_success_payload_bytes=62_000_000,
            )
            beyond_known = replace(
                known_safe,
                request_payload_bytes=64_000_000,
            )
            near_failure = replace(
                normal,
                request_payload_bytes=54_000_000,
                max_success_payload_bytes=50_000_000,
                min_failure_payload_bytes=56_000_000,
            )
            self.assertEqual(payload_risk(normal), "normal")
            self.assertEqual(payload_risk(warning), "warning")
            self.assertEqual(payload_risk(critical), "critical")
            self.assertEqual(payload_risk(known_safe), "normal")
            self.assertEqual(payload_risk(beyond_known), "warning")
            self.assertEqual(payload_risk(near_failure), "critical")

            first = create_handoff(
                directory=home / "handoffs",
                chain_name="word-v2.4",
                thread_id=THREAD_ID,
                thread_title="测试会话",
                summary="完成第一阶段",
                completed=["完成第一阶段"],
                pending=["继续第二阶段"],
                decisions=["保留原格式"],
                files=["output.docx"],
                verification=["验证通过"],
                next_action="开始第二阶段",
                reason="request-413",
                metrics={
                    "projectedTokens": critical.projected_tokens,
                    "contextWindow": critical.context_window,
                    "remainingTokens": critical.remaining_tokens,
                    "percent": critical.percent,
                    "requestPayloadBytes": critical.request_payload_bytes,
                    "maxSuccessfulRequestBytes": critical.max_success_payload_bytes,
                    "minFailedRequestBytes": critical.min_failure_payload_bytes,
                    "imageCount": critical.image_count,
                    "imagePayloadBytes": critical.image_payload_bytes,
                    "payloadRisk": "critical",
                },
            )
            report = Path(first["path"])
            text = report.read_text(encoding="utf-8")
            self.assertIn("交接快照", text)
            self.assertIn(THREAD_ID, text)
            self.assertIn("完成第一阶段", text)

            second = create_handoff(
                directory=home / "handoffs",
                chain_name="word-v2.4",
                thread_id="01a187b3-00ff-7270-909e-c60b1d3e8150",
                thread_title="测试会话二",
                summary="完成第二阶段",
                completed=["完成第二阶段"],
                pending=["继续第三阶段"],
                next_action="开始第三阶段",
                reason="manual",
                metrics={"percent": 12.5},
            )
            self.assertEqual(second["chainId"], first["chainId"])
            self.assertEqual(second["handoffCount"], 2)
            self.assertTrue(
                Path(first["path"]).as_posix().endswith(second["previousHandoff"])
            )
            archive = Path(second["chainContextPath"]).read_text(encoding="utf-8")
            self.assertIn("完成第一阶段", archive)
            self.assertIn("完成第二阶段", archive)
            self.assertIn("不得跳过或仅用摘要替代", archive)
            index = json.loads(Path(second["indexPath"]).read_text(encoding="utf-8"))
            self.assertTrue(index["coverageComplete"])
            self.assertEqual(index["handoffCount"], 2)
            self.assertEqual(len(index["handoffs"]), 2)


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
